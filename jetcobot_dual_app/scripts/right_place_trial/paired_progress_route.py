"""Build a short dual-arm route from two checked single-arm paths.

The left path is planned with the right arm held. The right path is planned
with the left arm at its yield goal. A small progress grid finds how far the
left arm must yield before both paths can safely overlap. The execution gate
performs its denser independent-progress check before any Pi command.
"""
import copy
import math


def sample_arm(phase, names, step_deg=5.0, min_step_deg=2.5):
    """Path samples at most step_deg apart and at least min_step_deg apart.

    MoveIt's time-parameterised trajectories hold ~100 tiny points; using them
    all made the paired grid 112 rows for a 29-degree yield (20+ s of checks,
    2026-11-19). Points closer than min_step_deg to the last kept sample are
    dropped (deviation < min_step_deg); the end point is always kept. The
    paired path itself is re-checked at 1 degree and by the gate.
    """
    trajectory = phase['result'].trajectory.joint_trajectory
    index = [trajectory.joint_names.index(name) for name in names]
    start = tuple(phase['start'][name] for name in names)
    points = [start]
    rows = [tuple(row.positions[i] for i in index) for row in trajectory.points]
    min_step = math.radians(min_step_deg)
    for k, target in enumerate(rows):
        previous = points[-1]
        gap = max(abs(a-b) for a, b in zip(previous, target))
        if gap < min_step and k != len(rows)-1:
            continue
        if gap < 1e-9:
            continue
        count = max(1, math.ceil(gap / math.radians(step_deg)))
        points.extend(tuple(a + (b-a)*i/count for a, b in zip(previous, target))
                      for i in range(1, count+1))
    if len(points) == 1:
        points.append(start)
    return points


def first_safe_overlap(left, right, valid, stride=3):
    """Find earliest left progress whose entire remaining product is safe.

    Rows are checked every `stride` steps from the end; when a strided row
    fails, the skipped rows just above it are checked one by one. A full
    106-row grid took ~40 s in the see_center return (2026-11-19). Skipped
    rows are covered by the 1-degree check of the chosen paired path and by
    the gate's dense independent-progress preflight before any motion.
    """
    if len(left) < 2 or len(right) < 2:
        raise ValueError('Both arms need a nonempty path')
    many = getattr(valid, 'many', None)

    def row_ok(i):
        row = [left[i] + r for r in right]
        return many(row) if many else all(valid(q) for q in row)

    last = len(left) - 1
    if not row_ok(last):
        raise ValueError('Right path is unsafe even with left arm fully yielded')
    earliest = last
    i = last - stride
    while i >= 0:
        if row_ok(i):
            earliest = i
            i -= stride
            continue
        for j in range(earliest - 1, i, -1):  # refine between i and earliest
            if not row_ok(j):
                return j + 1
            earliest = j
        return earliest
    for j in range(earliest - 1, -1, -1):
        if not row_ok(j):
            return j + 1
        earliest = j
    return earliest


def _at(points, u):
    x = u * (len(points)-1)
    i = min(int(x), len(points)-2)
    fraction = x-i
    return tuple(a+(b-a)*fraction for a, b in zip(points[i], points[i+1]))


def paired_points(left, right, overlap_index):
    """One left prefix, then both arms follow their original path progress."""
    combined = [l + right[0] for l in left[:overlap_index+1]]
    suffix = left[overlap_index:]
    fractions = {i/(len(suffix)-1) for i in range(len(suffix))} if len(suffix)>1 else {0., 1.}
    fractions.update(i/(len(right)-1) for i in range(len(right)))
    for u in sorted(fractions):
        q = _at(suffix, u) + _at(right, u) if len(suffix)>1 else suffix[0] + _at(right, u)
        if max(abs(a-b) for a, b in zip(q, combined[-1])) > 1e-8:
            combined.append(q)
    return combined


def make_phase(coord, state, left_phase, right_phase, overlap_index, left, right,
               all_joint_names, reverse=False, label='PAIRED CASE ARRIVAL'):
    """reverse=True plays the paired route backwards (used for the return)."""
    from builtin_interfaces.msg import Duration
    from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

    positions = paired_points(left, right, overlap_index)
    if reverse:
        positions = positions[::-1]
    trajectory = JointTrajectory()
    trajectory.joint_names = list(all_joint_names)
    elapsed = 0.0
    previous = None
    for current in positions:
        if previous is not None:
            elapsed += max(.01, max(abs(a-b) for a, b in zip(previous, current))/.06)
        seconds = round(elapsed*1_000_000_000)
        sec, nanosec = divmod(seconds, 1_000_000_000)
        trajectory.points.append(JointTrajectoryPoint(
            positions=list(current), time_from_start=Duration(sec=sec, nanosec=nanosec)))
        previous = current
    if elapsed > 120.0:
        raise ValueError('Paired route exceeds the 120 s gate trajectory limit')
    result = copy.deepcopy(left_phase['result'])
    result.trajectory_start = coord.robot_state(state)
    result.trajectory.joint_trajectory = trajectory
    end = state.copy()
    end.update(zip(all_joint_names, positions[-1]))
    return {'side': 'both', 'goal': dict(left_phase['goal'], **right_phase['goal']),
            'label': label, 'result': result,
            'start': state.copy(), 'end': end}


GRIPPER_LINKS = tuple(f'{side}_gripper_{part}' for side in ('left', 'right')
                      for part in ('base', 'left1', 'left2', 'left3',
                                   'right1', 'right2', 'right3'))
CAMERA_LINKS = ('left_jetcocam', 'right_jetcocam')
# Arm links 2..6 too: observed contacts were left_2_Link/left_4_Link against
# the right gripper/camera, so both sides of the pair get the margin.
ARM_LINKS = tuple(f'{side}_{i}_Link' for side in ('left', 'right') for i in range(2, 7))
MARGIN_LINKS = GRIPPER_LINKS + CAMERA_LINKS + ARM_LINKS


class PlanningMargin:
    """Plan with larger gripper padding, then restore the execution padding.

    Planning and the gate's live monitor otherwise share one padding, so any
    tracking error near the other arm trips the monitor. Planning (and the
    script's paired checks) at the larger value leaves the difference as real
    margin. The original padding is always restored before execution.
    """

    # Long side of a held object gets this much more than the other sides
    # (user, 2026-11-19: 15 mm was not enough along the chocobar's length).
    long_extra_mm = 10.0
    # Gripper, camera and the held object get this much more than arm links
    # (user, 2026-11-19 after pick-place runs: +1 cm for those).
    tool_extra_mm = 10.0
    # Held object, per side while planning (user: the +25/+35 mm chocobar was
    # far too big and blocked simple routes, 2026-11-19).
    object_extra_mm = 10.0

    def __init__(self, coord, extra_m=0.015):
        self.coord, self.extra = coord, extra_m
        self.original = None

    def _apply(self, values):
        from moveit_msgs.msg import LinkPadding, PlanningScene
        from moveit_msgs.srv import ApplyPlanningScene
        client = __import__('held_object').cached_client(self.coord.node, 
            ApplyPlanningScene, '/jetcobot_dual/apply_planning_scene')
        if not client.wait_for_service(timeout_sec=6.0):
            raise RuntimeError('MoveIt apply_planning_scene unavailable')
        request = ApplyPlanningScene.Request()
        request.scene = PlanningScene()
        request.scene.is_diff = True
        request.scene.robot_state.is_diff = True
        request.scene.link_padding = [LinkPadding(link_name=n, padding=float(p))
                                      for n, p in values.items()]
        if not self.coord.wait(client.call_async(request), 8.0,
                               stoppable=False).success:
            raise RuntimeError('MoveIt rejected gripper padding change')

    def __enter__(self):
        from moveit_msgs.msg import PlanningSceneComponents
        from moveit_msgs.srv import GetPlanningScene
        client = __import__('held_object').cached_client(self.coord.node, 
            GetPlanningScene, '/jetcobot_dual/get_planning_scene')
        if not client.wait_for_service(timeout_sec=6.0):
            raise RuntimeError('MoveIt planning scene service unavailable')
        request = GetPlanningScene.Request()
        request.components.components = PlanningSceneComponents.LINK_PADDING_AND_SCALING
        scene = self.coord.wait(client.call_async(request), 8.0).scene
        current = {row.link_name: row.padding for row in scene.link_padding}
        self.original = {n: current.get(n, 0.0) for n in MARGIN_LINKS}
        tool_links = set(GRIPPER_LINKS) | set(CAMERA_LINKS)
        tool_extra = self.extra + (self.tool_extra_mm/1000.0 if self.extra > 0 else 0.0)
        self._apply({n: p + (tool_extra if n in tool_links else self.extra)
                     for n, p in self.original.items()})
        # The held object gets the same planning margin (per side); it is
        # re-attached at its true size on exit.
        import held_object
        wait = lambda f, t: self.coord.wait(f, t, stoppable=False)
        self.held = [(arm, kind) for arm in ('left', 'right')
                     for kind in [held_object.held_type(self.coord.node, wait, arm)] if kind]
        for arm, kind in self.held:
            held_object.apply(self.coord.node, wait, kind, arm,
                              inflate_mm=self.object_extra_mm if self.extra > 0 else 0.0,
                              inflate_long_mm=(self.object_extra_mm + self.long_extra_mm)
                              if self.extra > 0 else 0.0)
        print(f'PLANNING MARGIN: arm links +{self.extra*1000:.0f} mm, gripper/camera '
              f'+{tool_extra*1000:.0f} mm, held object +{self.object_extra_mm:.0f} mm '
              f'(long side +{self.object_extra_mm + self.long_extra_mm:.0f} mm) '
              'while planning; execution keeps the launch padding.', flush=True)
        return self

    def __exit__(self, *_):
        if self.original is not None:
            import held_object
            wait = lambda f, t: self.coord.wait(f, t, stoppable=False)
            for arm, kind in getattr(self, 'held', []):
                held_object.apply(self.coord.node, wait, kind, arm)
            self._apply(self.original)
            print('PLANNING MARGIN: execution padding restored.', flush=True)
        return False
