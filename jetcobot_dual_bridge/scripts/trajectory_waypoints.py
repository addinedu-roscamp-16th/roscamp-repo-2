"""Select sparse joint-space targets while bounding deviation from a planned path."""
import math


def select_waypoints(points, max_step_deg=4.0, max_deviation_deg=0.5):
    """Return planned (time, six-joint tuple) points to send, including start/end.

    A skipped point must stay close to the joint-space line between retained
    endpoints and progress monotonically along it. Planned time is deliberately
    excluded: a speed profile can be nonlinear even on a perfectly straight
    joint path. This is a geometric approximation, not proof of SDK motion.
    """
    if len(points) < 2:
        raise ValueError('At least two trajectory points are required')
    if not 0 < max_deviation_deg < max_step_deg <= 10:
        raise ValueError('Invalid waypoint spacing/deviation')
    step = math.radians(max_step_deg)
    deviation = math.radians(max_deviation_deg)
    result = [points[0]]
    start = 0
    while start < len(points) - 1:
        best = None
        t0, q0 = points[start]
        for end in range(start + 1, len(points)):
            t1, q1 = points[end]
            if t1 <= t0:
                raise ValueError('Trajectory times must increase')
            delta = [b-a for a, b in zip(q0, q1)]
            if max(abs(d) for d in delta) > step:
                break
            norm2 = sum(d*d for d in delta)
            previous_fraction = -1e-6
            fits = True
            for _, q in points[start+1:end]:
                fraction = (sum((v-a)*d for v, a, d in zip(q, q0, delta))/norm2
                            if norm2 > 1e-12 else 0.0)
                if (fraction < -1e-6 or fraction > 1.000001 or
                        fraction < previous_fraction-1e-6 or
                        segment_deviation(q, q0, q1) > deviation):
                    fits = False
                    break
                previous_fraction = fraction
            if not fits:
                continue
            best = end
        if best is None:
            raise ValueError('A planned segment exceeds the SDK waypoint step')
        result.append(points[best])
        start = best
    return result


def segment_deviation(actual, start, end):
    """Max absolute joint error in radians to the nearest point on a segment."""
    delta = [b-a for a, b in zip(start, end)]
    norm2 = sum(d*d for d in delta)
    fraction = (sum((v-a)*d for v, a, d in zip(actual, start, delta))/norm2
                if norm2 > 1e-12 else 0.0)
    fraction = max(0.0, min(1.0, fraction))
    return max(abs(v-(a+fraction*d)) for v, a, d in zip(actual, start, delta))


def segment_progress(actual, start, end):
    """Unclamped progress and max joint error to a segment (radians).

    Negative values are before start; values above one are beyond end.
    """
    delta = [b-a for a, b in zip(start, end)]
    norm2 = sum(d*d for d in delta)
    if norm2 <= 1e-12:
        return 0.0, max(abs(v-a) for v, a in zip(actual, start))
    fraction = sum((v-a)*d for v, a, d in zip(actual, start, delta))/norm2
    clipped = max(0.0, min(1.0, fraction))
    deviation = max(abs(v-(a+clipped*d)) for v, a, d in zip(actual, start, delta))
    return fraction, deviation


def effective_goal_tolerance_deg(initial, target, configured_deg):
    """Require at least half of a small requested move to be measured.

    A fixed 5-degree endpoint tolerance must not confirm a 2-degree command
    before the arm moves. The configured value remains an upper bound.
    """
    amplitude = math.degrees(max(abs(a-b) for a, b in zip(initial, target)))
    return min(configured_deg, max(0.1, 0.5*amplitude))


def round_joint_path(points, max_trim_deg=1.0, corner_angle_deg=12.0, samples_per_corner=4):
    """Round sharp joint-space corners with bounded local deviation.

    Each corner is replaced by a quadratic Bezier arc. The trim on either
    adjoining segment is at most max_trim_deg in the largest joint coordinate.
    This is an execution approximation; collision checking remains on MoveIt's
    original path, so callers must enforce a measured deviation corridor.
    """
    if len(points) < 3 or not 0 < max_trim_deg <= 3 or samples_per_corner < 2:
        return list(points)
    trim_limit = math.radians(max_trim_deg)
    threshold = math.radians(corner_angle_deg)
    output = [points[0]]
    for index in range(1, len(points)-1):
        before = points[index-1][1]
        tc, corner = points[index]
        tn, after = points[index+1]
        incoming = tuple(c-b for b, c in zip(before, corner))
        outgoing = tuple(a-c for c, a in zip(corner, after))
        in_norm = math.sqrt(sum(v*v for v in incoming))
        out_norm = math.sqrt(sum(v*v for v in outgoing))
        if in_norm < 1e-9 or out_norm < 1e-9:
            output.append((tc, corner))
            continue
        cosine = max(-1.0, min(1.0, sum(a*b for a, b in zip(incoming, outgoing)) /
                               (in_norm*out_norm)))
        turn = math.acos(cosine)
        if turn < threshold:
            output.append((tc, corner))
            continue
        in_max = max(abs(v) for v in incoming)
        out_max = max(abs(v) for v in outgoing)
        trim = min(trim_limit, 0.35*in_max, 0.35*out_max)
        if trim < math.radians(0.15):
            output.append((tc, corner))
            continue
        enter = tuple(c - v*(trim/in_max) for c, v in zip(corner, incoming))
        leave = tuple(c + v*(trim/out_max) for c, v in zip(corner, outgoing))
        output.append((tc, enter))
        for sample in range(1, samples_per_corner):
            u = sample/samples_per_corner
            q = tuple((1-u)**2*p + 2*(1-u)*u*c + u*u*n
                      for p, c, n in zip(enter, corner, leave))
            output.append((tc, q))
        output.append((tc, leave))
    output.append(points[-1])
    return output
