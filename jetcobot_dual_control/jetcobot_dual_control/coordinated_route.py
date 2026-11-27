"""Convert a dual-arm MoveIt path into independently safe SDK motion sections.

The Pi drag bridges advance from measured progress, not MoveIt time. A section
may run both arms together only if every sampled combination of their progress
is collision-free. Otherwise one arm yields before the other advances.
"""
import math

from .model import LEFT_LIMITS_DEG, LIMITS_DEG


ARM_JOINTS = {side: tuple(f'{side}_{i}_Joint' for i in range(1, 7))
              for side in ('left', 'right')}
ALL_JOINTS = ARM_JOINTS['left'] + ARM_JOINTS['right']
MAX_SECTION_STEP = math.radians(15.0)
CHECK_STEP = math.radians(1.0)


def ordered_endpoint(trajectory, measured):
    """Read an immediate joint target, with the uncommanded arm held."""
    names = tuple(trajectory.joint_names)
    valid_orders = (*ARM_JOINTS.values(), ALL_JOINTS)
    if not any(len(names) == len(order) and set(names) == set(order) for order in valid_orders):
        raise ValueError('Joint goal requires one complete arm or both arms')
    if len(trajectory.points) != 1:
        raise ValueError('Joint goal requires exactly one target point')
    point = trajectory.points[0]
    if len(point.positions) != len(names):
        raise ValueError('Joint goal has incomplete joint positions')
    target = dict(zip(ALL_JOINTS, measured))
    target.update(zip(names, point.positions))
    ordered = tuple(float(target[name]) for name in ALL_JOINTS)
    for value, (low, high) in zip(ordered, LEFT_LIMITS_DEG + LIMITS_DEG):
        if not math.isfinite(value) or not math.radians(low) <= value <= math.radians(high):
            raise ValueError('Joint goal has a nonfinite position or exceeds a hardware joint limit')
    return ordered


def ordered_points(trajectory, measured):
    names = tuple(trajectory.joint_names)
    if len(names) == 12 and set(names) == set(ALL_JOINTS):
        pass
    elif len(names) == 6 and any(set(names) == set(ARM_JOINTS[side])
                                 for side in ('left', 'right')):
        # MoveIt may select the combined controller for an arm_left/arm_right
        # request. Keep the other arm at its measured start throughout.
        pass
    else:
        raise ValueError('Combined controller requires one complete arm or both arms')
    if trajectory.header.stamp.sec or trajectory.header.stamp.nanosec:
        raise ValueError('Combined trajectory must start immediately')
    if not 2 <= len(trajectory.points) <= 2000:
        raise ValueError('Combined trajectory needs 2..2000 points')
    order = tuple(ALL_JOINTS.index(name) for name in names)
    result = []
    previous_t = -1.0
    for point in trajectory.points:
        t = point.time_from_start.sec + point.time_from_start.nanosec / 1e9
        if not math.isfinite(t) or not previous_t < t <= 120.0:
            raise ValueError('Invalid combined trajectory timing')
        if len(point.positions) != len(names):
            raise ValueError('Incomplete arm trajectory point')
        q = list(measured)
        for source, target in enumerate(order):
            q[target] = float(point.positions[source])
        q = tuple(q)
        if not all(math.isfinite(value) for value in q):
            raise ValueError('Nonfinite combined trajectory position')
        for value, (low, high) in zip(q, LEFT_LIMITS_DEG + LIMITS_DEG):
            if not math.radians(low) <= value <= math.radians(high):
                raise ValueError('Combined trajectory exceeds a hardware joint limit')
        if result and max(abs(a-b)/(t-previous_t) for a, b in zip(q, result[-1])) > .08:
            raise ValueError('Combined trajectory exceeds 0.08 rad/s')
        result.append(q)
        previous_t = t
    if max(abs(a-b) for a, b in zip(result[0], measured)) > math.radians(5.0):
        raise ValueError('Combined plan start differs from measured arms; replan')
    return result


def _samples(start, end):
    count = max(1, math.ceil(max(abs(a-b) for a, b in zip(start, end)) / CHECK_STEP))
    return [tuple(a + (b-a)*i/count for a, b in zip(start, end))
            for i in range(count + 1)]


def _all_valid(states, valid):
    """all(valid(q)); uses valid.many(list) for batched checks when provided."""
    many = getattr(valid, 'many', None)
    if many is not None:
        return many(list(states))
    return all(valid(q) for q in states)


def _section_modes(start, end, valid):
    left = _samples(start[:6], end[:6])
    right = _samples(start[6:], end[6:])
    if _all_valid([l + r for l in left for r in right], valid):
        left_moves = max(abs(a-b) for a, b in zip(start[:6], end[:6])) > 1e-4
        right_moves = max(abs(a-b) for a, b in zip(start[6:], end[6:])) > 1e-4
        mode = 'both' if left_moves and right_moves else 'left' if left_moves else 'right'
        return [(mode, start, end)]
    if _all_valid([l + start[6:] for l in left] +
                  [end[:6] + r for r in right], valid):
        return [('left', start, end[:6] + start[6:]),
                ('right', end[:6] + start[6:], end)]
    if _all_valid([start[:6] + r for r in right] +
                  [l + end[6:] for l in left], valid):
        return [('right', start, start[:6] + end[6:]),
                ('left', start[:6] + end[6:], end)]
    return None


def safe_sections(points, valid):
    """Preflight every section before any motor command is accepted."""
    if not points or not valid(points[0]):
        raise ValueError('Combined start state is in collision')
    sections = []
    index = 0
    while index < len(points)-1:
        end = index + 1
        while (end + 1 < len(points) and end-index < 50 and
               max(abs(a-b) for a, b in zip(points[index], points[end+1]))
               <= MAX_SECTION_STEP):
            end += 1
        chosen = None
        while end > index:
            modes = _section_modes(points[index], points[end], valid)
            if modes is not None:
                chosen = (end, modes)
                break
            end = index + max(0, (end-index)//2)
        if chosen is None:
            raise ValueError(f'No independently safe dual-arm section after point {index}')
        end, modes = chosen
        sections.extend((mode, start, target) for mode, start, target in modes
                        if max(abs(a-b) for a, b in zip(start, target)) > 1e-4)
        index = end
    if not sections:
        raise ValueError('Combined plan contains no arm movement')
    return sections


def sampled_arm_path(points, offset):
    """Positions an independently progressing arm may occupy on a polyline."""
    samples = []
    for start, end in zip(points, points[1:]):
        samples.extend(_samples(start[offset:offset+6], end[offset:offset+6])[:-1])
    samples.append(points[-1][offset:offset+6])
    return samples


def independent_path_valid(points, valid):
    """Check every sampled pair, since the two Pi bridges do not share a clock."""
    left = sampled_arm_path(points, 0)
    right = sampled_arm_path(points, 6)
    return _all_valid([l + r for l in left for r in right], valid)


def independent_arms_valid(left_points, right_points, valid):
    """Check two independently timed six-joint polylines."""
    left = sampled_arm_path([q + (0.0,)*6 for q in left_points], 0)
    right = sampled_arm_path([(0.0,)*6 + q for q in right_points], 6)
    return _all_valid([l + r for l in left for r in right], valid)


def dense_path(points, max_step_deg):
    """Preserve each checked chord while making it acceptable to Pi's reducer."""
    # Stay just below the Pi limit: an exact multiple (e.g. 30 deg / 5 deg)
    # can round 1 ulp above the step and be rejected by select_waypoints.
    step = math.radians(max_step_deg) * 0.999
    output = [points[0]]
    for start, end in zip(points, points[1:]):
        count = max(1, math.ceil(max(abs(a-b) for a, b in zip(start, end))/step))
        output.extend(tuple(a+(b-a)*index/count for a, b in zip(start, end))
                      for index in range(1, count+1))
    return output


def chord_deviation(points, begin, end):
    """Largest joint departure of a planned portion from its shortcut."""
    start, target = points[begin], points[end]
    delta = [b-a for a, b in zip(start, target)]
    norm2 = sum(value*value for value in delta)
    maximum = 0.0
    for point in points[begin:end+1]:
        fraction = (sum((q-a)*d for q, a, d in zip(point, start, delta))/norm2
                    if norm2 > 1e-12 else 0.0)
        fraction = min(1.0, max(0.0, fraction))
        maximum = max(maximum, max(abs(q-(a+fraction*d))
                                    for q, a, d in zip(point, start, delta)))
    return maximum


def continuous_sections(sections, valid):
    """Join adjacent sections when independent arm progress remains safe.

    This also turns consecutive left/right moves into one concurrent action
    when their full sampled path product is collision-free. The Pi bridges may
    then advance at different speeds without waiting at the old boundary.
    """
    groups = []
    for mode, start, target in sections:
        if groups:
            candidate = groups[-1][1] + [target]
            if independent_path_valid(candidate, valid):
                merged_mode = (mode if groups[-1][0] == mode else 'both')
                groups[-1] = (merged_mode, candidate)
                continue
        groups.append((mode, [start, target]))
    return groups
