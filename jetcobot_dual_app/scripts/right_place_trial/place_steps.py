"""Place the held object into the case after the coordinated arrival.

Sequence (user, 2026-11-19): SDK vertical MoveL down until the closed
fingertips are 10 mm below the case rim -> open the gripper only slightly
(object width + a few mm; fully open would hit) -> remove the held object
from MoveIt -> straight 30 mm lift through the coordinated gate.
The case itself is NOT in the MoveIt scene; descent and lift are vertical.
"""
import math
import time

import numpy as np

import held_object
from move_right_sam_yaw import GRASP_CLOSED_TIP_OFFSET_MM
from place_right_in_case import measured_tcp, wait_action

ARM = 'right'
ARM_JOINTS = tuple(f'{ARM}_{i}_Joint' for i in range(1, 7))


def finger_gap_mm(q):
    """Fingertip-box inner-face gap for a gripper joint value (URDF, offline)."""
    c, s = math.cos(q), math.sin(q)
    left = -0.012 + (c*-0.027 - s*0.016) + 0.0078 + 0.0125
    c2, s2 = math.cos(-q), math.sin(-q)
    right = 0.012 + (c2*0.027 - s2*0.016) - 0.0091 - 0.0125
    return (right - left) * 1000.0


def grip_value_for_gap(gap_mm):
    lo, hi = -0.83, 0.535
    if not finger_gap_mm(lo) <= gap_mm <= finger_gap_mm(hi):
        raise ValueError(f'Release gap {gap_mm:.1f} mm outside gripper range')
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if finger_gap_mm(mid) < gap_mm else (lo, mid)
    return (lo + hi) / 2


def descend(coord, case_xy, rim_z_mm, below_rim_mm, method='moveit'):
    from example_interfaces.srv import AddTwoInts
    state = coord.state()
    before = measured_tcp(coord, state, f'{ARM}_TCP')
    tip_z = before.position.z*1000.0 - GRASP_CLOSED_TIP_OFFSET_MM[ARM]
    down_mm = tip_z - (rim_z_mm - below_rim_mm)
    print(f'PLACE DESCENT start: measured {ARM} TCP mm='
          f'{[round(before.position.x*1000, 1), round(before.position.y*1000, 1), round(before.position.z*1000, 1)]} '
          f'(case center {np.round(case_xy*1000, 1).tolist()} mm).', flush=True)
    print(f'PLACE DESCENT: tips {tip_z-rim_z_mm:.1f} mm above rim -> '
          f'{below_rim_mm:.0f} mm below rim = {down_mm:.1f} mm down ({method}).',
          flush=True)
    if not -30.0 <= down_mm <= 100.0 or (method == 'sdk' and down_mm < 5.0):
        raise RuntimeError(f'Place descent {down_mm:.1f} mm outside allowed range')
    if method == 'moveit':
        # SDK MoveL drifted ~5 deg in ry at the case pose (2026-11-19) and the
        # grasp-pose compensation made it worse; joint-space execution of a
        # straight MoveIt path keeps the orientation without per-pose tuning.
        # First center over the case at the current height, then descend,
        # both with measured-feedback correction of the arm's sag.
        tcp_target_z = (rim_z_mm - below_rim_mm + GRASP_CLOSED_TIP_OFFSET_MM[ARM]) / 1000.0
        try:
            move_to(coord, (case_xy[0], case_xy[1], before.position.z), 'PLACE CENTER')
            move_to(coord, (case_xy[0], case_xy[1], tcp_target_z), 'PLACE DESCENT')
        except RuntimeError as exc:
            # User (2026-11-19): releasing where the arm already is above the
            # case is acceptable. Only allow it while the tips stay above the rim.
            now = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
            tips_above = now.position.z*1000.0 - GRASP_CLOSED_TIP_OFFSET_MM[ARM] - rim_z_mm
            if tips_above < 5.0:
                raise
            print(f'PLACE: correction failed ({exc}); releasing here, tips '
                  f'{tips_above:.1f} mm above rim.', flush=True)
        after = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
        print(f'PLACE DESCENT done: TCP mm='
              f'{[round(after.position.x*1000, 1), round(after.position.y*1000, 1), round(after.position.z*1000, 1)]}',
              flush=True)
        return
    else:
        _sdk_descend(coord, down_mm)
    time.sleep(0.6)
    after = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
    delta = np.array([after.position.x-before.position.x,
                      after.position.y-before.position.y,
                      after.position.z-before.position.z]) * 1000.0
    print(f'PLACE DESCENT measured TCP delta mm={np.round(delta, 1).tolist()}', flush=True)
    if np.linalg.norm(delta[:2]) > 15.0 or abs(delta[2] + down_mm) > 10.0:
        raise RuntimeError('Place descent endpoint differs from vertical target; '
                           'gripper NOT opened')


def _sdk_descend(coord, down_mm):
    from example_interfaces.srv import AddTwoInts
    client = held_object.cached_client(coord.node, AddTwoInts, f'/{ARM}/sdk_linear_descend_tenth_mm')
    if not client.wait_for_service(timeout_sec=5.0):
        raise RuntimeError('Pi vertical MoveL service unavailable')
    request = AddTwoInts.Request()
    request.a = int(round(down_mm*10))
    request.b = 0
    if coord.wait(client.call_async(request), 60.0, stoppable=False).sum != 1:
        raise RuntimeError('Pi vertical MoveL failed; check right Pi log')


def open_slightly(coord, gap_mm):
    from builtin_interfaces.msg import Duration
    from control_msgs.action import FollowJointTrajectory
    from rclpy.action import ActionClient
    from trajectory_msgs.msg import JointTrajectoryPoint
    value = grip_value_for_gap(gap_mm)
    client = held_object.cached_action(coord.node, FollowJointTrajectory,
        f'/jetcobot_dual/guarded_{ARM}_gripper_controller/follow_joint_trajectory')
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'{ARM}_gripper_controller']
    goal.trajectory.points = [JointTrajectoryPoint(
        positions=[value], time_from_start=Duration(sec=2))]
    print(f'PLACE RELEASE: open {ARM} gripper to model gap {gap_mm:.0f} mm '
          f'(joint {value:+.3f}).', flush=True)
    # The Pi reads the gripper only while the arm is idle (every 2 s) and the
    # gate needs a reading < 5 s old, so right after a long arm motion the
    # first request is rejected (2026-11-19). Retry for a few seconds.
    for attempt in range(8):
        try:
            wait_action(coord, client, goal, 'PLACE GRIPPER RELEASE')
            return
        except RuntimeError as exc:
            if 'rejected' not in str(exc) or attempt == 7:
                raise
            time.sleep(1.0)


def vertical_move(coord, dz_mm, label):
    pose = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
    cartesian_move(coord, (pose.position.x, pose.position.y,
                           pose.position.z + dz_mm/1000.0), label)


def measured_xyz(coord):
    pose = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
    return np.array([pose.position.x, pose.position.y, pose.position.z])


def move_to(coord, target, label, tol_mm=5.0, max_moves=3, final_xy_mm=10.0,
            final_z_mm=10.0):
    """Reach target (m) by measured feedback.

    The right arm sags at the case pose: the measured TCP ended ~9 mm toward
    the robot and ~10 mm low after a goal the Pi reported as reached
    (2026-11-19). Each move commands target - last observed bias
    (measured - commanded), so a repeatable sag is cancelled.
    """
    target = np.asarray(target, dtype=float)
    bias = np.zeros(3)
    for move in range(1, max_moves+1):
        error = (target - measured_xyz(coord)) * 1000.0
        print(f'{label}: error to target mm={np.round(error, 1).tolist()}', flush=True)
        if np.linalg.norm(error[:2]) <= tol_mm and abs(error[2]) <= tol_mm:
            return
        command = target - bias
        cartesian_move(coord, command, f'{label} move {move}/{max_moves}')
        time.sleep(0.6)
        bias = measured_xyz(coord) - command
        print(f'{label}: observed bias mm={np.round(bias*1000, 1).tolist()}', flush=True)
    error = (target - measured_xyz(coord)) * 1000.0
    print(f'{label}: final error mm={np.round(error, 1).tolist()}', flush=True)
    if np.linalg.norm(error[:2]) > final_xy_mm or abs(error[2]) > final_z_mm:
        raise RuntimeError(f'{label}: could not reach target within '
                           f'{final_xy_mm:.0f} mm XY / {final_z_mm:.0f} mm Z')


def _quat_to_matrix(q):
    x, y, z, w = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def _rotvec(R):
    """Rotation vector (rad) of a rotation matrix."""
    angle = math.acos(max(-1.0, min(1.0, (np.trace(R)-1)/2)))
    if angle < 1e-9:
        return np.zeros(3)
    axis = np.array([R[2, 1]-R[1, 2], R[0, 2]-R[2, 0], R[1, 0]-R[0, 1]]) / (2*math.sin(angle))
    return axis * angle


def _fk(coord, state):
    pose = measured_tcp(coord, state, f'{ARM}_TCP')
    p = np.array([pose.position.x, pose.position.y, pose.position.z])
    q = pose.orientation
    return p, _quat_to_matrix((q.x, q.y, q.z, q.w))


def solve_small_move(coord, state, target_p, target_R, iterations=15):
    """Joint angles for a short TCP move (damped least squares on a numeric
    Jacobian from MoveIt FK). Used because compute_cartesian_path returned
    0 % for a 9 mm move at the case pose (2026-11-19).

    Position has priority: the case pose is near the wrist singularity
    (J5 ~ 0), where an exact orientation demand made the solver crawl and
    leave several mm of position error. Orientation only weakly pulls back.
    """
    q = dict(state)
    rot_weight = 0.01   # metres per radian: 1 deg ~ 0.17 mm of "error"
    damping = 0.01
    for _ in range(iterations):
        p, R = _fk(coord, q)
        error = np.concatenate([target_p - p, rot_weight*_rotvec(target_R @ R.T)])
        if np.linalg.norm(error[:3]) < 3e-4:
            break
        J = np.zeros((6, 6))
        for j, name in enumerate(ARM_JOINTS):
            probe = dict(q)
            probe[name] += 1e-3
            p2, R2 = _fk(coord, probe)
            J[:3, j] = (p2 - p) / 1e-3
            J[3:, j] = rot_weight*_rotvec(R2 @ R.T) / 1e-3
        dq = J.T @ np.linalg.solve(J @ J.T + damping**2*np.eye(6), error)
        step = float(np.max(np.abs(dq)))
        if step > math.radians(5):
            dq *= math.radians(5) / step
        for j, name in enumerate(ARM_JOINTS):
            q[name] += float(dq[j])
    p, R = _fk(coord, q)
    residual = np.linalg.norm(target_p - p) * 1000.0
    tilt = \
        math.degrees(np.linalg.norm(_rotvec(target_R @ R.T)))
    # Hardware already misses by up to ~10 mm (user, 2026-11-19); only refuse
    # a clearly wrong solution.
    if residual > 10.0 or tilt > 10.0:
        raise RuntimeError(f'Small-move solution misses target by {residual:.1f} mm, '
                           f'{tilt:.1f} deg')
    if residual > 2.0 or tilt > 3.0:
        print(f'  small-move solution {residual:.1f} mm / {tilt:.1f} deg from target; '
              'continuing', flush=True)
    return q


def cartesian_trajectory(coord, state, target, label):
    """Plan a short straight TCP move with the measured orientation."""
    from builtin_interfaces.msg import Duration
    from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
    p0, R0 = _fk(coord, state)
    target = np.asarray(target, dtype=float)
    distance = np.linalg.norm(target - p0)
    if distance > 0.06:
        raise RuntimeError(f'{label}: {distance*1000:.0f} mm is too long for a small move')
    # Waypoints every ~5 mm keep the joint path close to the straight line.
    count = max(1, math.ceil(distance/0.005))
    q = state
    joints = [[state[n] for n in ARM_JOINTS]]
    for i in range(1, count+1):
        q = solve_small_move(coord, q, p0 + (target-p0)*i/count, R0)
        joints.append([q[n] for n in ARM_JOINTS])
    trajectory = JointTrajectory()
    trajectory.joint_names = list(ARM_JOINTS)
    elapsed, previous = 0.0, None
    for current in joints:
        if previous is not None:
            elapsed += max(.05, max(abs(a-b) for a, b in zip(previous, current))/.06)
        ns = round(elapsed*1e9)
        trajectory.points.append(JointTrajectoryPoint(
            positions=current,
            time_from_start=Duration(sec=ns//1_000_000_000, nanosec=ns % 1_000_000_000)))
        previous = current
    return trajectory, distance


def cartesian_move(coord, target, label):
    from control_msgs.action import FollowJointTrajectory

    state = coord.state()
    trajectory, distance = cartesian_trajectory(coord, state, target, label)
    coord.check_trajectory(trajectory, state, group_name='both_arms', geometric_only=True)
    print(f'{label}: {distance*1000:.1f} mm straight move via gate '
          f'({len(trajectory.points)} points).', flush=True)
    action = held_object.cached_action(coord.node, FollowJointTrajectory,
        '/jetcobot_dual/guarded_both_controller/follow_joint_trajectory')
    wait_action(coord, action, FollowJointTrajectory.Goal(trajectory=trajectory),
                label, timeout=120.0)


def place(coord, case_xy, rim_z_mm, below_rim_mm=-13.0, release_gap_mm=None, up_mm=30.0,
          descent_method='moveit', arm='right', checkpoint=None):
    # Same steps for either arm. Nothing here is a fixed right-arm offset:
    # the sag is corrected from measured joints, and the SDK MoveL path (with
    # the right-arm grasp compensation) is not used by default.
    global ARM, ARM_JOINTS
    ARM = arm
    ARM_JOINTS = tuple(f'{arm}_{i}_Joint' for i in range(1, 7))
    wait = lambda f, t: coord.wait(f, t)
    kind = held_object.held_type(coord.node, wait, ARM)
    if release_gap_mm is None:
        width = {'chocobar': 35.0, 'mentos_pair': 32.0}.get(kind, 35.0)
        release_gap_mm = width + 5.0
    t = time.monotonic()
    # Disabled 2026-11-19 (user): release right at the arrival pose (tips
    # ~30 mm above the rim); the centering + lowering with measured-feedback
    # correction is kept here for later use.
    # descend(coord, case_xy, rim_z_mm, below_rim_mm, descent_method)
    # print(f'  TIME place center+lower: {time.monotonic()-t:.1f} s', flush=True)
    now = measured_tcp(coord, coord.state(), f'{ARM}_TCP')
    print(f'PLACE: releasing at the arrival pose, {ARM} TCP mm='
          f'{[round(now.position.x*1000, 1), round(now.position.y*1000, 1), round(now.position.z*1000, 1)]}, '
          f'tips {now.position.z*1000.0 - GRASP_CLOSED_TIP_OFFSET_MM[ARM] - rim_z_mm:.1f} mm above rim '
          f'(case center [{case_xy[0]*1000:.0f}, {case_xy[1]*1000:.0f}]).', flush=True)
    open_slightly(coord, release_gap_mm)
    if checkpoint is not None:
        checkpoint('gripper_released')
    print(f'  TIME place release: {time.monotonic()-t:.1f} s', flush=True)
    t = time.monotonic()
    held_object.apply(coord.node, wait, arm=ARM, remove=True)
    if checkpoint is not None:
        checkpoint('scene_detached')
    vertical_move(coord, up_mm, 'PLACE LIFT')
    print(f'  TIME place lift: {time.monotonic()-t:.1f} s', flush=True)
    print('PLACE COMPLETE: object released, gripper partly open, lifted.', flush=True)
