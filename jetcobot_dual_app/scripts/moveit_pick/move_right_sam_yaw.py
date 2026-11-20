#!/usr/bin/env python3
"""ROS2 Jazzy MoveIt 2 pose planning + opt-in ExecuteTrajectory for selected JetCobot arm.

Requires MoveGroup and ExecuteTrajectory action servers (normally /jetcobot_dual/move_action,
/jetcobot_dual/execute_trajectory) and fresh /left or /right real_joint_states.
Approach, optional ROI-based XY alignment, configured-offset descent, and gripper close are separate phases. Lift remains disabled.

WARNING: With SDK final-goal bridging, executing a planned trajectory does NOT
mean the real robot follows that collision-checked trajectory. Verify the
controller, clearance and physical workspace independently.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from motion_math import trajectory_motion_cost, sdk_goal_motion_cost

# Physical jaw tips are below the fixed model TCP; values are from the user's measurements.
GRASP_FLOOR_Z_MM = -5.0
GRASP_CLOSED_TIP_ABOVE_FLOOR_MM = 3.0
GRASP_CLOSED_TIP_OFFSET_MM = {'left': 40.0, 'right': 25.0}


def grasp_tcp_target_z_m(side):
    return (GRASP_FLOOR_Z_MM + GRASP_CLOSED_TIP_ABOVE_FLOOR_MM +
            GRASP_CLOSED_TIP_OFFSET_MM[side]) / 1000.0


def fine_request_argv(request_path):
    """Use a saved UI fine request as this worker's ordinary CLI arguments."""
    data = json.loads(request_path.read_text())
    argv = ['--target-file', data['target_file'], '--out-dir', str(request_path.parent),
            '--phase', 'align', '--fine-axis', data['axis'], '--arm', data['arm'],
            '--align-dx-mm', str(data['delta_xyz_m'][0] * 1000),
            '--align-dy-mm', str(data['delta_xyz_m'][1] * 1000),
            '--above-mm', str(data['above_mm']), '--group', data['group'],
            '--tip-link', data['tip_link'], '--yaw-camera', data['yaw_camera'],
            '--move-action', data['move_action'], '--execute-action', data['execute_action'],
            '--display-topic', data['display_topic'],
            '--tcp-closing-axis', data['tcp_closing_axis'],
            '--yaw-offset-deg', str(data['yaw_offset_deg']),
            '--max-joint-change-deg', str(data['max_joint_change_deg']),
            '--max-state-change-deg', str(data['max_state_change_deg']),
            '--min-valid-points', str(data['min_valid_points']), '--execute']
    if data.get('allow_uncertain_yaw'):
        argv.append('--allow-uncertain-yaw')
    if data['arm'] == 'right':
        # 2026-11-20: right SDK MoveL XY added +3.4 deg rx per move (11.7 deg
        # after 4 moves). Right uses a MoveIt joint goal at a LEVELLED TCP.
        argv.append('--fine-joint-goal')
    return argv


def r_z(theta):
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])


def r_y(theta):
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]])


def mat_to_quat(R):
    """Convert orthogonal matrix to quaternion (x,y,z,w)."""
    # Top-down tool: Rz(base) @ Ry(pi) is analytically (zsin,zcos,...) rotation.
    # Generic conversion for future corrections.
    t = np.trace(R)
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
        w = 0.25 * s
    else:
        i = int(np.argmax(np.diag(R)))
        if i == 0:
            s = math.sqrt(max(0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) * 2
            x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s
            z = (R[0, 2] + R[2, 0]) / s
            w = (R[2, 1] - R[1, 2]) / s
        elif i == 1:
            s = math.sqrt(max(0, 1 + R[1, 1] - R[0, 0] - R[2, 2])) * 2
            x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s
            z = (R[1, 2] + R[2, 1]) / s
            w = (R[0, 2] - R[2, 0]) / s
        else:
            s = math.sqrt(max(0, 1 + R[2, 2] - R[0, 0] - R[1, 1])) * 2
            x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s
            z = 0.25 * s
            w = (R[1, 0] - R[0, 1]) / s
    q = np.array([x, y, z, w])
    return q / np.linalg.norm(q)


def closing_axis_to_tcp_orientation(closing_yaw_deg, tcp_closing_axis, yaw_offset_deg):
    """TCP local +Z is assumed to approach downward (-Z in right_base_link).

    Closing axis local X: Rz(theta) @ Ry(pi): local X points theta+180 deg.
    Closing axis local Y: same matrix local Y points theta+90 deg.
    Axes are undirected, so +180 is equivalent for symmetrical gripper.
    """
    theta = closing_yaw_deg + yaw_offset_deg
    if tcp_closing_axis == 'y':
        theta -= 90.
    R = r_z(math.radians(theta)) @ r_y(math.pi)
    assert np.linalg.det(R) > .999
    return mat_to_quat(R), theta


def tcp_down_tilt_deg(quaternion):
    """Angle between TCP local +Z and world down (-Z)."""
    x, y, z, w = np.asarray(quaternion, dtype=float)
    norm_sq = x*x + y*y + z*z + w*w
    if not np.isfinite(norm_sq) or norm_sq < 1e-12:
        raise RuntimeError('Invalid TCP orientation from FK')
    local_z_world_z = 1.0 - 2.0*(x*x + y*y)/norm_sq
    return math.degrees(math.acos(float(np.clip(-local_z_world_z, -1.0, 1.0))))


def read_target(path, args):
    d = json.loads(path.read_text())
    if d.get('frame') != 'right_base_link':
        raise ValueError('Target coordinate frame must be right_base_link')
    target_xyz = np.array(d['center_xyz_mm'], dtype=float)
    side = getattr(args, 'yaw_camera', 'auto')
    if side == 'auto':
        side = d.get('selected_yaw_camera') or max(d.get('yaw_view_scores', {'right': 0.}),
                                                   key=d.get('yaw_view_scores', {'right': 0.}).get)
    side_yaws = d.get('yaw_by_side', {})
    if side_yaws:
        if side not in side_yaws:
            raise ValueError(f'No yaw estimate from the selected {side} camera')
        yaw = float(side_yaws[side]['closing_axis_yaw_deg'])
    else:
        if d.get('selected_arm', 'right') != side:
            raise ValueError('Target yaw belongs to a different arm; re-capture with --arm')
        yaw = float(d['closing_axis_yaw_deg'])
    if target_xyz.shape != (3,) or not np.isfinite(target_xyz).all() or not np.isfinite(yaw):
        raise ValueError('Bad target XYZ/yaw')
    if int(d.get('valid_points', 0)) < args.min_valid_points:
        raise ValueError('Too few 3D points')
    uncertain_yaw = any(
        p in w for w in d.get('warnings', [])
        for p in ('YAW UNSTABLE', 'MASK YAW DIFFERS', 'LEFT/RIGHT MASK YAW DISAGREE',
                  'NO PLANAR MASK FOOTPRINT')
    )
    if uncertain_yaw:
        print(f'WARNING: uncertain/disagreeing yaw; using {side.upper()} camera yaw.', flush=True)
    if args.phase == 'approach' and time.time() - float(d['capture_time_unix']) > args.max_capture_age:
        raise ValueError('Captured images/poses too old; recapture')
    goal_m = (target_xyz + np.array([0., 0., args.above_mm])) / 1000.
    mode = d.get('approach_mode')
    if mode not in ('camera_center', 'tcp_above_target'):
        raise ValueError('Approach mode missing; recapture with the updated ROI program')
    if 'observation_tcp_xy_m' not in d or abs(float(d.get('observation_above_mm', -1)) - args.above_mm) > 1e-6:
        raise ValueError('Approach offset missing or stale; recapture with the updated ROI program')
    offset = np.asarray(d['observation_tcp_xy_m'], dtype=float)
    if offset.shape != (2,) or not np.isfinite(offset).all() or np.linalg.norm(offset) > .08:
        raise ValueError('Invalid approach TCP offset')
    if mode == 'tcp_above_target' and np.linalg.norm(offset) > 1e-9:
        raise ValueError('TCP-above-target mode must have zero XY offset')
    if not (args.min_z_m <= goal_m[2] <= args.max_z_m):
        raise ValueError(f'Pregrasp Z outside configured limits: {goal_m[2]:.3f} m')
    if np.linalg.norm(goal_m) > args.max_radius_m:
        raise ValueError('Target too far from right robot base')
    return d, goal_m, yaw


def main():
    # The UI saves a complete request before starting this worker. Expanding it
    # here keeps the UI request and worker arguments in one place.
    if '--fine-request' in sys.argv:
        index = sys.argv.index('--fine-request')
        if index + 1 >= len(sys.argv):
            raise ValueError('--fine-request needs a JSON path')
        request_path = Path(sys.argv[index + 1])
        sys.argv[1:] = fine_request_argv(request_path)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target-file', type=Path, required=True)
    ap.add_argument('--arm', choices=('left', 'right'), default='right')
    ap.add_argument('--yaw-camera', choices=('auto','left','right'), default='auto')
    ap.add_argument('--peer-home', action='store_true', help='Plan/execute opposite arm to SRDF home first')
    ap.add_argument('--phase', choices=('approach','align','descend','close','lift','grasp'), default='approach',
                    help='grasp = descend + close + lift in one process (auto mode, 2026-11-19)')
    ap.add_argument('--group', default=None, help='Exact MoveIt SRDF group name; verify with local config')
    ap.add_argument('--tip-link', default=None, help='Exact selected-arm TCP link in MoveIt robot model')
    ap.add_argument('--move-action', default='/jetcobot_dual/move_action')
    ap.add_argument('--execute-action', default='/jetcobot_dual/execute_trajectory')
    ap.add_argument('--display-topic', default='/jetcobot_dual/display_planned_path')
    ap.add_argument('--above-mm', type=float, default=40.)
    ap.add_argument('--align-dx-mm', type=float, default=None)
    ap.add_argument('--align-dy-mm', type=float, default=None)
    ap.add_argument('--fine-axis', choices=('y', 'x'), default=None)
    ap.add_argument('--fine-joint-goal', action='store_true',
                    help='Use the old joint endpoint fine move instead of SDK Cartesian XY')
    ap.add_argument('--minimal-drag', action='store_true',
                    help='Execute approach via endpoint/drag/endpoint segments')
    ap.add_argument('--tcp-closing-axis', choices=['x', 'y'], default='x')
    ap.add_argument('--yaw-offset-deg', type=float, default=0.,
                    help='Calibrated correction; do not guess')
    ap.add_argument('--pos-tolerance-mm', type=float, default=5.)
    ap.add_argument('--orientation-tolerance-deg', type=float, default=2.)
    ap.add_argument('--velocity-scale', type=float, default=.10)
    ap.add_argument('--acceleration-scale', type=float, default=.10)
    ap.add_argument('--max-joint-change-deg', type=float, default=45.)
    ap.add_argument('--max-capture-age', type=float, default=120.)
    ap.add_argument('--max-state-change-deg', type=float, default=1.)
    ap.add_argument('--min-valid-points', type=int, default=6)
    ap.add_argument('--min-z-m', type=float, default=.02)
    ap.add_argument('--max-z-m', type=float, default=.50)
    ap.add_argument('--max-radius-m', type=float, default=.55)
    ap.add_argument('--allow-uncertain-yaw', action='store_true')
    ap.add_argument('--execute', action='store_true', help='Actually execute trajectory; default PLAN ONLY')
    ap.add_argument('--out-dir', type=Path, default=None)
    args = ap.parse_args()
    if args.phase != 'approach' and not args.execute:
        ap.error('Alignment, descent and close phases require --execute')
    args.group = args.group or f'arm_{args.arm}'
    args.tip_link = args.tip_link or f'{args.arm}_TCP'
    if args.group != f'arm_{args.arm}' or args.tip_link != f'{args.arm}_TCP':
        ap.error('--group and --tip-link must match --arm')
    if not args.target_file.is_file():
        ap.error(f'Target file not found: {args.target_file}')
    if args.above_mm < 10 or not 0 < args.velocity_scale <= .2 or not 0 < args.acceleration_scale <= .2:
        ap.error('Minimum approach offset is 10 mm; motion scale must be in (0, 0.2]')
    if not 0 < args.max_joint_change_deg <= 90 or not 0 < args.pos_tolerance_mm <= 20:
        ap.error('Invalid joint change or position tolerance')
    if not 0 < args.orientation_tolerance_deg <= 20:
        ap.error('Orientation tolerance out of range')

    target_data, pos, closing_yaw = read_target(args.target_file, args)
    chosen_camera = args.yaw_camera if args.yaw_camera != 'auto' else target_data.get('selected_yaw_camera', args.arm)
    captured = target_data['joint_positions_rad']
    expected = {f'{args.arm}_{i}_Joint': float(captured[f'{args.arm}_{i}_Joint']) for i in range(1, 7)}
    peer = 'left' if args.arm == 'right' else 'right'
    peer_expected = {f'{peer}_{i}_Joint': float(captured[f'{peer}_{i}_Joint']) for i in range(1, 7)}
    log_dir = args.out_dir if args.out_dir is not None else args.target_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    import rclpy
    from rclpy.action import ActionClient
    from action_msgs.msg import GoalStatus
    from sensor_msgs.msg import JointState
    from moveit_msgs.action import MoveGroup, ExecuteTrajectory
    from moveit_msgs.srv import GetPositionFK, GetStateValidity
    from std_srvs.srv import Trigger
    from example_interfaces.srv import AddTwoInts
    from control_msgs.action import FollowJointTrajectory
    from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
    from builtin_interfaces.msg import Duration
    from rcl_interfaces.srv import GetParameters
    try:
        from jetcobot_dual_control.execution_gate import validate_trial
    except ImportError:
        from jetcobot_dual_real.execution_gate import validate_trial
    from moveit_msgs.msg import (MoveItErrorCodes, Constraints, PositionConstraint,
                                 OrientationConstraint, JointConstraint, DisplayTrajectory)
    from geometry_msgs.msg import Pose, Quaternion
    from shape_msgs.msg import SolidPrimitive
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy

    rclpy.init()
    node = rclpy.create_node(f'sam_yaw_moveit_{args.arm}')
    joint_current = {'msg': None, 'at': 0.}
    active = {'handle': None}
    def joint_cb(msg):
        joint_current['msg'] = msg
        joint_current['at'] = time.monotonic()
    sub = node.create_subscription(JointState, f'/{args.arm}/real_joint_states', joint_cb, 10)
    peer_current = {'msg': None, 'at': 0.}
    peer_sub = node.create_subscription(JointState, f'/{peer}/real_joint_states',
        lambda msg: peer_current.update(msg=msg, at=time.monotonic()), 10)

    def fresh_state(timeout=4.):
        end = time.monotonic()+timeout
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.10)
            msg = joint_current['msg']
            if msg is not None and time.monotonic()-joint_current['at'] <= 1.0:
                q = dict(zip(msg.name, msg.position))
                out = {}
                for i in range(1, 7):
                    name = f'{args.arm}_{i}_Joint'
                    v = q.get(name, q.get(f'{i}_Joint'))
                    if v is None or not np.isfinite(v):
                        break
                    out[name] = float(v)
                if len(out) == 6:
                    return out
        raise RuntimeError(f'Missing recent valid /{args.arm}/real_joint_states (received within 1 s)')

    def fresh_peer_state(timeout=4.):
        end = time.monotonic()+timeout
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.10)
            msg = peer_current['msg']
            if msg is None or time.monotonic()-peer_current['at'] > 1.0:
                continue
            raw = dict(zip(msg.name,msg.position))
            values = {}
            for i in range(1,7):
                value = raw.get(f'{peer}_{i}_Joint', raw.get(f'{i}_Joint'))
                if value is None or not np.isfinite(value):
                    break
                values[f'{peer}_{i}_Joint'] = float(value)
            if len(values) == 6:
                return values
        raise RuntimeError(f'Missing fresh /{peer}/real_joint_states')

    def check_capture_drift(state):
        max_dev = max(abs(math.degrees(state[k]-expected[k])) for k in expected)
        print(f'Current-to-captured joint difference max = {max_dev:.2f} deg', flush=True)
        if max_dev > args.max_state_change_deg:
            raise RuntimeError('Robot moved since ROI capture. Re-capture before any action.')

    def await_future(fut, timeout=40.):
        end = time.monotonic()+timeout
        while rclpy.ok() and not fut.done() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.10)
        if not fut.done():
            raise TimeoutError('MoveIt response timeout')
        return fut.result()

    def action_goal(client, goal, name, timeout):
        if not client.wait_for_server(timeout_sec=8.):
            raise RuntimeError(f'{name} action server unavailable; check MoveIt launch/namespaces')
        handle = await_future(client.send_goal_async(goal), 12.)
        if not handle.accepted:
            raise RuntimeError(f'{name} goal rejected')
        active['handle'] = handle
        try:
            wrapped = await_future(handle.get_result_async(), timeout)
        except (KeyboardInterrupt, Exception):
            print(f'Cancel requested for {name}; not a substitute for a physical E-stop!', flush=True)
            try:
                await_future(handle.cancel_goal_async(), 3.)
            except Exception as cancel_exc:
                print(f'ROS cancellation not confirmed: {cancel_exc}', flush=True)
            raise
        finally:
            active['handle'] = None
        code = wrapped.result.error_code.val
        if wrapped.status != GoalStatus.STATUS_SUCCEEDED or code != MoveItErrorCodes.SUCCESS:
            meanings = { -1:'PLANNING_FAILED', -4:'CONTROL_FAILED', -10:'START_STATE_IN_COLLISION',
                         -12:'GOAL_IN_COLLISION', -15:'INVALID_GROUP_NAME',
                         -18:'INVALID_LINK_NAME', -31:'NO_IK_SOLUTION', -6:'TIMED_OUT' }
            raise RuntimeError(f'{name}: status={wrapped.status}, MoveIt error={code} '
                               f'({meanings.get(code, "see MoveIt log")})')
        return wrapped.result

    def plan(goal_position, orientation, peer_at_home=False, joint_goal=None):
        request = MoveGroup.Goal()
        r = request.request
        r.group_name = args.group
        r.num_planning_attempts = 3
        r.allowed_planning_time = 8.0
        r.max_velocity_scaling_factor = args.velocity_scale
        r.max_acceleration_scaling_factor = args.acceleration_scale
        # Pin the start to fresh measured joints. MoveIt can otherwise plan from a
        # stale monitored state when the Pi joint publisher pauses briefly.
        selected_start = fresh_state()
        r.start_state.is_diff = True
        r.start_state.joint_state.name = list(selected_start)
        r.start_state.joint_state.position = [float(selected_start[n]) for n in selected_start]
        if peer_at_home:
            # Preview the selected-arm path in the state reached after the peer-home phase.
            r.start_state.joint_state.name = list(r.start_state.joint_state.name) + [f'{peer}_{i}_Joint' for i in range(1, 7)]
            r.start_state.joint_state.position = list(r.start_state.joint_state.position) + [0.0] * 6
        if joint_goal is not None:
            c = Constraints()
            c.name = f'{args.arm}_previewed_joint_endpoint'
            c.joint_constraints = [JointConstraint(
                joint_name=name, position=float(joint_goal[name]),
                tolerance_above=.01, tolerance_below=.01, weight=1.)
                for name in expected]
            r.goal_constraints = [c]
        else:
            c = Constraints()
            c.name = f'{args.arm}_tcp_sam_topdown'
            pc = PositionConstraint()
            pc.header.frame_id = 'right_base_link'
            pc.link_name = args.tip_link
            pc.weight = 1.
            sphere = SolidPrimitive()
            sphere.type = SolidPrimitive.SPHERE
            sphere.dimensions = [args.pos_tolerance_mm / 1000.]
            target_pose = Pose()
            target_pose.position.x = float(goal_position[0])
            target_pose.position.y = float(goal_position[1])
            target_pose.position.z = float(goal_position[2])
            target_pose.orientation.w = 1.
            pc.constraint_region.primitives = [sphere]
            pc.constraint_region.primitive_poses = [target_pose]
            oc = OrientationConstraint()
            oc.header.frame_id = 'right_base_link'
            oc.link_name = args.tip_link
            oc.orientation = Quaternion(x=float(orientation[0]), y=float(orientation[1]),
                                        z=float(orientation[2]), w=float(orientation[3]))
            tol = math.radians(args.orientation_tolerance_deg)
            oc.absolute_x_axis_tolerance = tol
            oc.absolute_y_axis_tolerance = tol
            oc.absolute_z_axis_tolerance = tol
            oc.parameterization = OrientationConstraint.ROTATION_VECTOR
            oc.weight = 1.
            c.position_constraints = [pc]
            c.orientation_constraints = [oc]
            r.goal_constraints = [c]
        request.planning_options.plan_only = True
        request.planning_options.replan = False
        request.planning_options.look_around = False
        request.planning_options.planning_scene_diff.is_diff = True
        request.planning_options.planning_scene_diff.robot_state.is_diff = True
        r.pipeline_id = 'ompl'
        r.planner_id = 'RRTConnectkConfigDefault'
        goal_kind = 'saved joint endpoint' if joint_goal is not None else 'TCP pose'
        print(f'Planning: group={args.group}, tip={args.tip_link}, goal={goal_kind}, frame=right_base_link', flush=True)
        return action_goal(move_client, request, 'MoveGroup PLAN', 35.)

    def ik_straight_lift(pose, quat, current, heights_m=(.05, .03, .02)):
        """IK at the lift pose, then a straight joint path (<=2 deg steps)."""
        import types as _types
        from moveit_msgs.srv import GetPositionIK
        from moveit_msgs.msg import RobotState, RobotTrajectory
        from geometry_msgs.msg import PoseStamped
        from builtin_interfaces.msg import Duration
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
        ik_client = node.create_client(GetPositionIK, '/jetcobot_dual/compute_ik')
        if not ik_client.wait_for_service(timeout_sec=5.):
            raise RuntimeError('MoveIt IK service unavailable for lift fallback')
        start_state = RobotState()
        start_state.is_diff = True
        start_state.joint_state.name = list(current)
        start_state.joint_state.position = [float(v) for v in current.values()]
        for height in heights_m:
            request = GetPositionIK.Request()
            ik = request.ik_request
            ik.group_name = args.group
            ik.robot_state = start_state
            ik.avoid_collisions = True
            ik.ik_link_name = args.tip_link
            target = PoseStamped()
            target.header.frame_id = 'right_base_link'
            target.pose.position.x = float(pose.position.x)
            target.pose.position.y = float(pose.position.y)
            target.pose.position.z = float(pose.position.z + height)
            (target.pose.orientation.x, target.pose.orientation.y,
             target.pose.orientation.z, target.pose.orientation.w) = [float(v) for v in quat]
            ik.pose_stamped = target
            ik.timeout = Duration(sec=1)
            reply = await_future(ik_client.call_async(request), 8.)
            if reply.error_code.val != MoveItErrorCodes.SUCCESS:
                print(f'  IK lift {height*1000:.0f} mm: no solution ({reply.error_code.val})', flush=True)
                continue
            solution = dict(zip(reply.solution.joint_state.name, reply.solution.joint_state.position))
            names = list(expected)
            q0 = np.array([current[n] for n in names])
            q1 = np.array([solution[n] for n in names])
            count = max(1, int(np.ceil(np.max(np.abs(q1-q0)) / np.radians(2.0))))
            jt = JointTrajectory()
            jt.joint_names = names
            elapsed = 0.0
            for k in range(count+1):
                q = q0 + (q1-q0)*k/count
                if k:
                    elapsed += max(.05, float(np.max(np.abs(q1-q0)))/count/.06)
                ns = int(round(elapsed*1e9))
                jt.points.append(JointTrajectoryPoint(positions=q.tolist(),
                    time_from_start=Duration(sec=ns//1_000_000_000, nanosec=ns % 1_000_000_000)))
            print(f'  IK lift {height*1000:.0f} mm: {count} steps, '
                  f'max joint change {np.degrees(np.max(np.abs(q1-q0))):.1f} deg', flush=True)
            return _types.SimpleNamespace(planned_trajectory=RobotTrajectory(joint_trajectory=jt),
                                          trajectory_start=start_state)
        raise RuntimeError('Lift failed: neither MoveIt plan nor IK straight lift (5/3/2 cm) found a pose')

    if args.phase in ('align', 'descend', 'close', 'lift', 'grasp'):
        try:
            summary_path = log_dir / 'moveit_plan_summary.json'
            summary = json.loads(summary_path.read_text())
            if summary.get('selected_arm') != args.arm or summary.get('phase') != 'approach':
                raise RuntimeError('No matching approach record; recapture and execute approach first')
            done = json.loads((log_dir/'approach_complete.json').read_text())
            if done.get('arm') != args.arm:
                raise RuntimeError('Approach execution has not completed')
            endpoint = done.get('endpoint_rad')
            align_record = log_dir/'align_complete.json'
            if args.phase in ('align', 'descend', 'grasp') and align_record.exists():
                aligned = json.loads(align_record.read_text())
                if aligned.get('arm') != args.arm:
                    raise RuntimeError('Alignment record belongs to another arm')
                endpoint = aligned['endpoint_rad']
            if not endpoint:
                raise RuntimeError('Approach endpoint missing; recapture')
            current = fresh_state()
            mismatch = max(abs(math.degrees(current[k]-endpoint[k])) for k in expected)
            if args.phase == 'align' and mismatch > 5.0 and not args.fine_axis:
                raise RuntimeError(f'Arm moved from last approach/alignment endpoint ({mismatch:.1f} deg)')
            if args.phase == 'align' and args.fine_axis and mismatch > 5.0:
                print(f'Fine ROI: using current measured arm pose after {mismatch:.1f} deg change.', flush=True)
            if args.phase in ('descend', 'grasp'):
                print(f'G descent: using current measured arm pose; last recorded endpoint difference={mismatch:.1f} deg.', flush=True)
            if args.phase == 'align':
                delta_mm = np.array([args.align_dx_mm, args.align_dy_mm], dtype=float)
                if not np.isfinite(delta_mm).all() or not 1. <= np.linalg.norm(delta_mm) <= 80.01:
                    raise RuntimeError('Predicted one-shot alignment must be 1 to 80 mm in base XY')
                if target_data['approach_mode'] != 'tcp_above_target':
                    raise RuntimeError('Alignment requires the TCP-above-target approach')
                pending_path = log_dir/'align_attempt_pending.json'
                if pending_path.exists():
                    if args.fine_axis:
                        archived = log_dir / f'align_attempt_previous_{int(time.time()*1000)}.json'
                        pending_path.rename(archived)
                        print(f'Fine ROI: previous incomplete attempt archived as {archived.name}; '
                              'using current measured pose for the new ROI.', flush=True)
                    else:
                        raise RuntimeError('Previous alignment attempt was not confirmed; inspect robot and recapture')
                count = int(aligned['count']) if align_record.exists() else 0
                if not args.fine_axis and count >= 1:
                    raise RuntimeError('One-shot predicted move already used for this capture; recapture')
                move_client = ActionClient(node, MoveGroup, args.move_action)
                if not move_client.wait_for_server(timeout_sec=8.):
                    raise RuntimeError('MoveGroup unavailable')
                fk_client = node.create_client(GetPositionFK, '/jetcobot_dual/compute_fk')
                if not fk_client.wait_for_service(timeout_sec=5.):
                    raise RuntimeError('MoveIt FK service unavailable')
                fk_req = GetPositionFK.Request()
                fk_req.header.frame_id = 'right_base_link'
                fk_req.fk_link_names = [args.tip_link]
                fk_req.robot_state.is_diff = True
                fk_req.robot_state.joint_state.name = list(current)
                fk_req.robot_state.joint_state.position = list(current.values())
                before_fk = await_future(fk_client.call_async(fk_req), 8.)
                if before_fk.error_code.val != MoveItErrorCodes.SUCCESS or len(before_fk.pose_stamped) != 1:
                    raise RuntimeError('Could not compute TCP before alignment')
                before = before_fk.pose_stamped[0].pose
                before_xyz = np.array([before.position.x, before.position.y, before.position.z])
                goal_xyz = before_xyz + np.array([delta_mm[0], delta_mm[1], 0.]) / 1000.
                if not (args.min_z_m <= goal_xyz[2] <= args.max_z_m) or np.linalg.norm(goal_xyz) > args.max_radius_m:
                    raise RuntimeError('Alignment target outside configured workspace')
                quat = np.array([before.orientation.x, before.orientation.y,
                                 before.orientation.z, before.orientation.w])
                start_tilt_deg = tcp_down_tilt_deg(quat) if args.fine_axis else None
                level = bool(args.fine_axis and args.fine_joint_goal)
                if level:
                    # Keep the TCP heading (yaw), force local +Z straight down.
                    x, y, z, w = quat / np.linalg.norm(quat)
                    R_now = np.array([
                        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
                    heading = math.atan2(-R_now[1, 0], -R_now[0, 0])
                    quat = np.asarray(mat_to_quat(r_z(heading) @ r_y(math.pi)), dtype=float)
                    print(f'FINE LEVEL: start tilt {start_tilt_deg:.1f} deg -> goal 0.0 deg '
                          f'(heading kept {math.degrees(heading):.1f} deg)', flush=True)
                if args.fine_axis and start_tilt_deg > (15.0 if level else 8.0):
                    raise RuntimeError(f'Fine correction starts with TCP tilted '
                                       f'{start_tilt_deg:.1f} deg; inspect the approach pose')
                planned = plan(goal_xyz, quat)
                jt = planned.planned_trajectory.joint_trajectory
                if not jt.points or set(jt.joint_names) != set(expected):
                    raise RuntimeError('Invalid alignment trajectory joints')
                plan_delta = max(abs(math.degrees(q-current[name])) for pt in jt.points
                                 for name,q in zip(jt.joint_names, pt.positions))
                if plan_delta > args.max_joint_change_deg:
                    raise RuntimeError(f'Predicted XY plan changes a joint {plan_delta:.1f} deg; limit is {args.max_joint_change_deg:.1f} deg')
                validate_trial(FollowJointTrajectory.Goal(trajectory=jt), args.arm,
                    [current[f'{args.arm}_{i}_Joint'] for i in range(1, 7)])
                start = dict(zip(planned.trajectory_start.joint_state.name,
                                 planned.trajectory_start.joint_state.position))
                measured = fresh_state()
                if not all(k in start for k in expected) or max(
                    abs(math.degrees(measured[k]-start[k])) for k in expected) > args.max_state_change_deg:
                    raise RuntimeError('Alignment start differs from measured state')
                if args.fine_axis and args.fine_joint_goal:
                    # The firmware did not reliably execute Cartesian jog or
                    # finite Cartesian increments. Use the endpoint of the
                    # collision-checked MoveIt TCP pose plan as one slow joint goal.
                    goal_joint = dict(zip(jt.joint_names, jt.points[-1].positions))
                    peer_now = fresh_peer_state()
                    validity = node.create_client(GetStateValidity,
                        '/jetcobot_dual/check_state_validity')
                    if not validity.wait_for_service(timeout_sec=5.):
                        raise RuntimeError('MoveIt state validity service unavailable')
                    max_joint_delta = max(abs(goal_joint[name]-measured[name]) for name in expected)
                    check_steps = max(1, math.ceil(max_joint_delta / math.radians(2.0)))
                    for step in range(check_steps+1):
                        fraction = step/check_steps
                        values = {name: measured[name]+fraction*(goal_joint[name]-measured[name])
                                  for name in expected}
                        values.update(peer_now)
                        check = GetStateValidity.Request()
                        check.group_name = args.group
                        check.robot_state.is_diff = True
                        check.robot_state.joint_state.name = list(values)
                        check.robot_state.joint_state.position = list(values.values())
                        result = await_future(validity.call_async(check), 5.)
                        if not result.valid:
                            raise RuntimeError(f'Direct slow joint segment invalid at {fraction:.0%}')
                        # The Pi executes one joint-space endpoint move, not the
                        # MoveIt path. Check the orientation along that exact
                        # interpolated joint segment before sending it.
                        segment_fk = GetPositionFK.Request()
                        segment_fk.header.frame_id = 'right_base_link'
                        segment_fk.fk_link_names = [args.tip_link]
                        segment_fk.robot_state.is_diff = True
                        segment_fk.robot_state.joint_state.name = list(expected)
                        segment_fk.robot_state.joint_state.position = [values[name] for name in expected]
                        pose_result = await_future(fk_client.call_async(segment_fk), 5.)
                        if (pose_result.error_code.val != MoveItErrorCodes.SUCCESS or
                                len(pose_result.pose_stamped) != 1):
                            raise RuntimeError('Could not check TCP angle along fine correction')
                        pose = pose_result.pose_stamped[0].pose
                        sample_quat = np.array([pose.orientation.x, pose.orientation.y,
                                                pose.orientation.z, pose.orientation.w])
                        tilt_deg = tcp_down_tilt_deg(sample_quat)
                        turn_deg = math.degrees(2 * math.acos(float(np.clip(
                            abs(np.dot(quat, sample_quat)), 0., 1.))))
                        tilt_limit = max(8.0, start_tilt_deg + .5) if level else 8.0
                        turn_limit = start_tilt_deg + 3.0 if level else 3.0
                        if tilt_deg > tilt_limit or turn_deg > turn_limit:
                            raise RuntimeError(f'Fine correction would tilt TCP at '
                                               f'{fraction:.0%}: down tilt={tilt_deg:.1f} deg '
                                               f'(start {start_tilt_deg:.1f}), '
                                               f'orientation change={turn_deg:.1f} deg '
                                               '(limits: 8 deg tilt, 3 deg turn); no motion sent')
                    duration = max(5., max_joint_delta / .07)
                    direct = JointTrajectory()
                    direct.joint_names = list(expected)
                    direct.points = [
                        JointTrajectoryPoint(positions=[measured[name] for name in expected],
                                             time_from_start=Duration()),
                        JointTrajectoryPoint(positions=[goal_joint[name] for name in expected],
                                             time_from_start=Duration(sec=int(duration),
                                                 nanosec=int((duration%1.)*1e9)))]
                    slow_goal = FollowJointTrajectory.Goal(trajectory=direct)
                    validate_trial(slow_goal, args.arm,
                        [measured[f'{args.arm}_{i}_Joint'] for i in range(1, 7)])
                    service_name = f'/{args.arm}/sdk_slow_goal_controller/follow_joint_trajectory'
                    slow_client = ActionClient(node, FollowJointTrajectory, service_name)
                    if not slow_client.wait_for_server(timeout_sec=5.):
                        raise RuntimeError(f'Pi slow joint action unavailable: {service_name}; restart updated bridge')
                    pending_path.write_text(json.dumps({'arm':args.arm,
                        'predicted_delta_mm':delta_mm.tolist(),
                        'commanded_joint_goal_rad':goal_joint, 'at':time.time()}))
                    print(f'EXECUTE ALIGN PREDICTION: slow SDK joint goal for XY '
                          f'{np.round(delta_mm, 1).tolist()} mm, speed=1.', flush=True)
                    handle = await_future(slow_client.send_goal_async(slow_goal), 8.)
                    if not handle.accepted:
                        raise RuntimeError('Pi rejected slow joint goal')
                    try:
                        wrapped = await_future(handle.get_result_async(), 95.)
                    except BaseException:
                        try:
                            await_future(handle.cancel_goal_async(), 3.)
                        except Exception:
                            pass
                        raise
                    if (wrapped.status != GoalStatus.STATUS_SUCCEEDED or
                            wrapped.result.error_code != FollowJointTrajectory.Result.SUCCESSFUL):
                        raise RuntimeError(f'Pi slow joint goal failed: '
                            f'status={wrapped.status}, code={wrapped.result.error_code}, '
                            f'message={wrapped.result.error_string}')
                    print(f'Pi slow joint goal: {wrapped.result.error_string}', flush=True)
                else:
                    service_name = f'/{args.arm}/sdk_linear_xy_tenth_mm'
                    linear_client = node.create_client(AddTwoInts, service_name)
                    if not linear_client.wait_for_service(timeout_sec=5.):
                        raise RuntimeError(f'Native SDK XY MoveL service unavailable: {service_name}')
                    command = AddTwoInts.Request()
                    command.a = int(round(float(delta_mm[0]) * 10.))
                    command.b = int(round(float(delta_mm[1]) * 10.))
                    pending_path.write_text(json.dumps({'arm':args.arm,'predicted_delta_mm':delta_mm.tolist(),
                        'commanded_delta_mm':[command.a/10., command.b/10.], 'at':time.time()}))
                    print(f'EXECUTE ALIGN PREDICTION: SDK MoveL one-shot XY '
                          f'{[command.a/10.,command.b/10.]} mm, Z/orientation fixed.', flush=True)
                    linear_reply = await_future(linear_client.call_async(command), 60.)
                    if linear_reply.sum != 1:
                        raise RuntimeError('Native SDK XY prediction failed; inspect Pi bridge log and robot pose')
                reached = fresh_state()
                fk_req.robot_state.joint_state.position = list(reached.values())
                after_fk = await_future(fk_client.call_async(fk_req), 8.)
                if after_fk.error_code.val != MoveItErrorCodes.SUCCESS or len(after_fk.pose_stamped) != 1:
                    raise RuntimeError('Could not verify TCP after alignment')
                after = after_fk.pose_stamped[0].pose
                after_xyz = np.array([after.position.x, after.position.y, after.position.z])
                actual_delta = after_xyz - before_xyz
                after_quat = np.array([after.orientation.x, after.orientation.y,
                                       after.orientation.z, after.orientation.w])
                angle_deg = math.degrees(2 * math.acos(min(1., abs(float(np.dot(quat, after_quat))))))
                progress = float(np.dot(actual_delta[:2], delta_mm)) / np.linalg.norm(delta_mm)
                print(f'ALIGN measured TCP delta [mm]: {(actual_delta*1000).round(2).tolist()}, '
                      f'orientation change={angle_deg:.1f} deg', flush=True)
                if args.fine_axis:
                    actual_tilt_deg = tcp_down_tilt_deg(after_quat)
                    print(f'FINE measured TCP down tilt={actual_tilt_deg:.2f} deg '
                          f'(start {start_tilt_deg:.2f} deg), '
                          f'orientation change={angle_deg:.2f} deg', flush=True)
                    if np.linalg.norm(actual_delta[:2]) < .0005:
                        raise RuntimeError('SDK XY reported success but measured TCP moved '
                                           'less than 0.5 mm; stopping repeated fine commands')
                xy_error = np.linalg.norm(actual_delta[:2] - delta_mm / 1000.)
                if args.fine_axis:
                    position_error_mm = float(np.linalg.norm(
                        actual_delta - np.array([delta_mm[0], delta_mm[1], 0.]) / 1000.) * 1000.)
                    print(f'FINE measured position error: {position_error_mm:.1f} mm', flush=True)
                    if position_error_mm > 10.0:
                        print(f'WARNING: Fine TCP position differs from requested XY/Z '
                              f'by {position_error_mm:.1f} mm', flush=True)
                else:
                    print(f'ALIGN XY target error: {xy_error*1000:.1f} mm (limit 15 mm)', flush=True)
                    if (progress < max(.003, .5*np.linalg.norm(delta_mm)/1000.) or
                            xy_error > .015 or abs(actual_delta[2]) > .015 or angle_deg > 8.):
                        raise RuntimeError('Alignment step did not move toward target with stable Z/orientation')
                align_record.write_text(json.dumps({'arm':args.arm,'endpoint_rad':reached,
                    'count':count+1,'fine_axis':args.fine_axis,'predicted_delta_mm':delta_mm.tolist(),
                    'measured_delta_mm':(actual_delta*1000.).tolist(),
                    'tcp_xyz_m':after_xyz.tolist(),'at':time.time()}))
                pending_path.unlink()
                if args.fine_axis:
                    print('SDK XY command ended. Recheck the live camera range before another move.',
                          flush=True)
                else:
                    print('ONE-SHOT PREDICTION MOVE COMPLETE. Check live ROI; no automatic descent or close.',
                          flush=True)
                return 0
            if args.phase in ('descend', 'grasp'):
                attempt_path = log_dir/'descend_attempt.json'
                if attempt_path.exists():
                    raise RuntimeError('Descent already attempted for this capture; inspect robot and recapture')
                if target_data['approach_mode'] != 'tcp_above_target':
                    raise RuntimeError('Descent requires TCP-above-target approach, not camera-center mode')
                move_client = ActionClient(node, MoveGroup, args.move_action)
                if not move_client.wait_for_server(timeout_sec=8.):
                    raise RuntimeError('MoveGroup unavailable')
                fk_client = node.create_client(GetPositionFK, '/jetcobot_dual/compute_fk')
                if not fk_client.wait_for_service(timeout_sec=5.):
                    raise RuntimeError('MoveIt FK service unavailable')
                fk_req = GetPositionFK.Request()
                fk_req.header.frame_id = 'right_base_link'
                fk_req.fk_link_names = [args.tip_link]
                fk_req.robot_state.is_diff = True
                fk_req.robot_state.joint_state.name = list(current)
                fk_req.robot_state.joint_state.position = list(current.values())
                fk = await_future(fk_client.call_async(fk_req), 8.)
                if fk.error_code.val != MoveItErrorCodes.SUCCESS or len(fk.pose_stamped) != 1:
                    raise RuntimeError(f'Could not compute current {args.tip_link} pose: {fk.error_code.val}')
                pose = fk.pose_stamped[0].pose
                target_z_m = grasp_tcp_target_z_m(args.arm)
                down_mm = (pose.position.z - target_z_m) * 1000.0
                if not -10.0 <= down_mm <= 100.0:
                    raise RuntimeError(f'G needs a -10..100 mm SDK vertical move, but current TCP Z={pose.position.z*1000:.1f} mm and calibrated target Z={target_z_m*1000:.1f} mm require {down_mm:.1f} mm')
                descend_pos = np.array([pose.position.x, pose.position.y, target_z_m])
                quat = np.array([pose.orientation.x, pose.orientation.y,
                                 pose.orientation.z, pose.orientation.w])
                print(f'Measured TCP xyz [m]: {[pose.position.x, pose.position.y, pose.position.z]}; '
                      f'G calibrated TCP Z target: {target_z_m*1000:.1f} mm, down {down_mm:.1f} mm', flush=True)
                if descend_pos[2] < args.min_z_m or np.linalg.norm(descend_pos) > args.max_radius_m:
                    raise RuntimeError(f'Descent target outside configured workspace: {descend_pos.tolist()}')
                result = plan(descend_pos, quat)
                jt = result.planned_trajectory.joint_trajectory
                if not jt.points or set(jt.joint_names) != set(expected):
                    raise RuntimeError('Invalid descent trajectory joints')
                validate_trial(FollowJointTrajectory.Goal(trajectory=jt), args.arm,
                    [current[f'{args.arm}_{i}_Joint'] for i in range(1, 7)])
                start = dict(zip(result.trajectory_start.joint_state.name,
                                 result.trajectory_start.joint_state.position))
                measured = fresh_state()
                if not all(k in start for k in expected) or max(
                    abs(math.degrees(measured[k]-start[k])) for k in expected) > args.max_state_change_deg:
                    raise RuntimeError('Descent start differs from measured state')
                if abs(down_mm) < 5.0:
                    # Already within hardware error of the grasp height. A
                    # 2.9 mm SDK MoveL on the left arm never settled (2026-11-19);
                    # skip it instead of failing the pick.
                    reached = fresh_state()
                    attempt_path.write_text(json.dumps({'arm': args.arm, 'down_mm': 0.0,
                         'skipped_mm': down_mm, 'at': time.time()}))
                    (log_dir/'descend_complete.json').write_text(json.dumps({
                        'arm': args.arm, 'endpoint_rad': reached,
                        'tcp_xyz_m': [pose.position.x, pose.position.y, pose.position.z],
                        'at': time.time()}))
                    print(f'DESCENT SKIPPED: TCP is only {down_mm:.1f} mm above the grasp '
                          'height (< 5 mm). Closing gripper next.', flush=True)
                else:
                    service_name = f'/{args.arm}/sdk_linear_descend_tenth_mm'
                    linear_client = node.create_client(AddTwoInts, service_name)
                    if not linear_client.wait_for_service(timeout_sec=5.):
                        raise RuntimeError(f'Native SDK MoveL service unavailable: {service_name}; deploy updated Pi bridge')
                    distance = AddTwoInts.Request()
                    distance.a = int(round(down_mm * 10.))
                    distance.b = 0
                    attempt_path.write_text(json.dumps({'arm': args.arm, 'down_mm': distance.a / 10.,
                         'target_tcp_z_mm': target_z_m * 1000.0, 'at': time.time()}))
                    if args.arm == 'left':
                        # Left SDK MoveL ignored 2.9 mm and 6.7 mm descents (Z did
                        # not change in 45 s, 2026-11-19); the right arm's MoveL is
                        # calibrated and kept. Execute the already planned and
                        # validated MoveIt descent in joint space through the gate.
                        print(f'EXECUTE: left descent {down_mm:.1f} mm via MoveIt joint path '
                              '(SDK MoveL not used on the left arm).', flush=True)
                        exec_client = ActionClient(node, ExecuteTrajectory, args.execute_action)
                        goal = ExecuteTrajectory.Goal()
                        goal.trajectory = result.planned_trajectory
                        action_goal(exec_client, goal, 'LEFT DESCENT EXECUTE', 90.)
                        time.sleep(0.6)
                    else:
                        print(f'EXECUTE: native SDK MoveL {distance.a / 10.:g} mm vertical (+down) with fixed orientation.', flush=True)
                        linear_reply = await_future(linear_client.call_async(distance), 60.)
                        if linear_reply.sum != 1:
                            raise RuntimeError('Native SDK MoveL failed; inspect Pi bridge log and robot pose')
                    reached = fresh_state()
                    fk_req.robot_state.joint_state.position = list(reached.values())
                    after_fk = await_future(fk_client.call_async(fk_req), 8.)
                    if after_fk.error_code.val != MoveItErrorCodes.SUCCESS or len(after_fk.pose_stamped) != 1:
                        raise RuntimeError('Could not verify TCP after SDK MoveL')
                    after_pose = after_fk.pose_stamped[0].pose
                    after_xyz = np.array([after_pose.position.x, after_pose.position.y, after_pose.position.z])
                    before_xyz = np.array([pose.position.x, pose.position.y, pose.position.z])
                    after_quat = np.array([after_pose.orientation.x, after_pose.orientation.y,
                                           after_pose.orientation.z, after_pose.orientation.w])
                    angle_deg = math.degrees(2 * math.acos(min(1., abs(float(np.dot(quat, after_quat))))))
                    delta = after_xyz - before_xyz
                    print(f'Measured TCP MoveL delta [mm]: {(delta * 1000).round(1).tolist()}, '
                          f'orientation change={angle_deg:.1f} deg', flush=True)
                    z_tolerance_m = min(.010, max(.002, abs(down_mm) / 1000. * .5))
                    if np.linalg.norm(delta[:2]) > .015 or abs(delta[2] + down_mm / 1000.) > z_tolerance_m or angle_deg > 7.:
                        raise RuntimeError('Native MoveL endpoint differs from requested vertical TCP target')
                    (log_dir/'descend_complete.json').write_text(json.dumps({
                        'arm': args.arm, 'endpoint_rad': reached, 'tcp_xyz_m': after_xyz.tolist(),
                        'at': time.time()}))
                    print('NATIVE MOVEL DESCENT SUCCESS. Camera remains live; closing gripper next.', flush=True)
                if args.phase != 'grasp':
                    return 0
                current = fresh_state()  # chained grasp: continue to close
            if args.phase in ('close', 'grasp'):
                descended = json.loads((log_dir/'descend_complete.json').read_text())
                if descended.get('arm') != args.arm:
                    raise RuntimeError('No matching completed descent')
                descent_endpoint = descended['endpoint_rad']
                mismatch = max(abs(math.degrees(current[k]-descent_endpoint[k])) for k in expected)
                if mismatch > 5.0:
                    raise RuntimeError(f'Arm moved from descent endpoint ({mismatch:.1f} deg)')
                from trajectory_msgs.msg import JointTrajectoryPoint
                from builtin_interfaces.msg import Duration
                grip_client = ActionClient(node, FollowJointTrajectory,
                    f'/jetcobot_dual/guarded_{args.arm}_gripper_controller/follow_joint_trajectory')
                if not grip_client.wait_for_server(timeout_sec=8.):
                    raise RuntimeError('Gripper controller unavailable; use execution_arm:=both')
                grip = FollowJointTrajectory.Goal()
                grip.trajectory.joint_names = [f'{args.arm}_gripper_controller']
                point = JointTrajectoryPoint()
                point.positions = [-0.83]
                point.time_from_start = Duration(sec=3)
                grip.trajectory.points = [point]
                print(f'EXECUTE: closing {args.arm.upper()} gripper.', flush=True)
                # Gate needs a gripper reading < 5 s old; the Pi reads it only
                # while the arm is idle, so retry briefly after a motion.
                for attempt in range(8):
                    handle = await_future(grip_client.send_goal_async(grip), 12.)
                    if handle.accepted:
                        break
                    time.sleep(1.0)
                if not handle.accepted:
                    raise RuntimeError('Gripper close goal rejected')
                reply = await_future(handle.get_result_async(), 45.)
                if reply.status != GoalStatus.STATUS_SUCCEEDED or reply.result.error_code != 0:
                    raise RuntimeError(f'Gripper close failed: status={reply.status}, error={reply.result.error_code}, detail={reply.result.error_string}')
                (log_dir/'gripper_close_complete.json').write_text(json.dumps({'arm':args.arm,'at':time.time()}))
                print('GRIPPER CLOSE SUCCESS.', flush=True)
                # Model the held object on the gripper before lift/return so
                # every later MoveIt and gate check includes it.
                object_type = target_data.get('object_type')
                if object_type:
                    import held_object
                    held_object.apply(node, await_future, object_type, args.arm)
                else:
                    print('WARNING: no object_type in target; held object NOT modeled.', flush=True)
                if args.phase != 'grasp':
                    return 0
                current = fresh_state()  # chained grasp: continue to lift
            closed = json.loads((log_dir/'gripper_close_complete.json').read_text())
            if closed.get('arm') != args.arm:
                raise RuntimeError('No matching completed gripper close')
            move_client = ActionClient(node, MoveGroup, args.move_action)
            if not move_client.wait_for_server(timeout_sec=8.):
                raise RuntimeError('MoveGroup unavailable')
            # Lift from the measured TCP pose, not the camera's earlier target pose.
            fk_client = node.create_client(GetPositionFK, '/jetcobot_dual/compute_fk')
            if not fk_client.wait_for_service(timeout_sec=5.):
                raise RuntimeError('MoveIt FK service unavailable')
            fk_req = GetPositionFK.Request()
            fk_req.header.frame_id = 'right_base_link'
            fk_req.fk_link_names = [args.tip_link]
            fk_req.robot_state.is_diff = True
            fk_req.robot_state.joint_state.name = list(current)
            fk_req.robot_state.joint_state.position = list(current.values())
            fk = await_future(fk_client.call_async(fk_req), 8.)
            if fk.error_code.val != MoveItErrorCodes.SUCCESS or len(fk.pose_stamped) != 1:
                raise RuntimeError(f'Could not compute current {args.tip_link} pose: {fk.error_code.val}')
            pose = fk.pose_stamped[0].pose
            lift_pos = np.array([pose.position.x, pose.position.y, pose.position.z + .05])
            quat = np.array([pose.orientation.x, pose.orientation.y,
                             pose.orientation.z, pose.orientation.w])
            print(f'Measured TCP xyz [m]: {[pose.position.x, pose.position.y, pose.position.z]}; 5 cm lift target: {lift_pos.tolist()}', flush=True)
            if lift_pos[2] > args.max_z_m or np.linalg.norm(lift_pos) > args.max_radius_m:
                raise RuntimeError(f'Lift target outside configured workspace: {lift_pos.tolist()}')
            try:
                lift = plan(lift_pos, quat)
            except RuntimeError as exc:
                # Far-reaching grasp poses (e.g. the right arm on the left side,
                # 2026-11-19 multi run) made the RRT pose plan fail (99999).
                # Fallback: IK at the lift height, straight joint path, same
                # validation and gate execution as the planned lift.
                print(f'Lift pose plan failed ({exc}); trying IK straight lift.', flush=True)
                lift = ik_straight_lift(pose, quat, current)
            jt = lift.planned_trajectory.joint_trajectory
            if not jt.points or set(jt.joint_names) != set(expected):
                raise RuntimeError('Invalid lift trajectory joints')
            validate_trial(FollowJointTrajectory.Goal(trajectory=jt), args.arm,
                [current[f'{args.arm}_{i}_Joint'] for i in range(1,7)])
            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
            pub = node.create_publisher(DisplayTrajectory, args.display_topic, qos)
            display = DisplayTrajectory()
            display.model_id = 'jetcobot_dual'
            display.trajectory_start = lift.trajectory_start
            display.trajectory = [lift.planned_trajectory]
            pub.publish(display)
            until = time.monotonic() + .5
            while time.monotonic() < until:
                rclpy.spin_once(node, timeout_sec=.05)
            start = dict(zip(lift.trajectory_start.joint_state.name,
                             lift.trajectory_start.joint_state.position))
            if not all(k in start for k in expected) or max(
                abs(math.degrees(fresh_state()[k]-start[k])) for k in expected) > args.max_state_change_deg:
                raise RuntimeError('Lift start differs from measured state')
            print('EXECUTE: lift TCP by 5 cm.', flush=True)
            exec_client = ActionClient(node, ExecuteTrajectory, args.execute_action)
            goal = ExecuteTrajectory.Goal()
            goal.trajectory = lift.planned_trajectory
            action_goal(exec_client, goal, 'LIFT EXECUTE', 90.)
            print('LIFT SUCCESS.', flush=True)
            return 0
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()

    def plan_peer_home(current):
        if max(abs(math.degrees(current[name])) for name in current) <= 2.0:
            print(f'{peer.upper()} arm already at SRDF home (all joints near zero).', flush=True)
            return None, []
        request = MoveGroup.Goal()
        req = request.request
        req.group_name = f'arm_{peer}'
        req.pipeline_id = 'ompl'
        req.planner_id = 'RRTConnectkConfigDefault'
        req.num_planning_attempts = 3
        req.allowed_planning_time = 8.0
        req.max_velocity_scaling_factor = args.velocity_scale
        req.max_acceleration_scaling_factor = args.acceleration_scale
        req.start_state.is_diff = True
        req.goal_constraints = [Constraints(name=f'{peer}_srdf_home', joint_constraints=[
            JointConstraint(joint_name=f'{peer}_{i}_Joint', position=0.,
                            tolerance_above=.01, tolerance_below=.01, weight=1.)
            for i in range(1,7)])]
        request.planning_options.plan_only = True
        request.planning_options.planning_scene_diff.is_diff = True
        request.planning_options.planning_scene_diff.robot_state.is_diff = True
        print(f'Planning {peer.upper()} arm to SRDF home BEFORE {args.arm.upper()} target.', flush=True)
        result = action_goal(move_client, request, 'PEER HOME PLAN', 35.)
        jt = result.planned_trajectory.joint_trajectory
        if not jt.points or set(jt.joint_names) != set(current):
            raise RuntimeError('Peer home plan missing or wrong joints')
        restrictions = []
        max_change = max(abs(math.degrees(value-current[name]))
                         for point in jt.points for name,value in zip(jt.joint_names,point.positions))
        # Large joint deltas are reported for review but do not block execution.
        try:
            validate_trial(FollowJointTrajectory.Goal(trajectory=jt), peer,
                           [current[f'{peer}_{i}_Joint'] for i in range(1,7)])
        except ValueError as exc:
            restrictions.append(f'Peer home SDK gate: {exc}')
        print(f'Peer home plan: {len(jt.points)} points, max change {max_change:.1f} deg', flush=True)
        return result, restrictions

    try:
        print('MODE:', 'PLAN + EXECUTE' if args.execute else 'PLAN ONLY', '| ARM:', args.arm.upper(), '| YAW CAMERA:', chosen_camera.upper(), flush=True)
        print('Approach mode:', target_data['approach_mode'], flush=True)
        print('Nominal TCP above object right_base_link [m]:', np.round(pos, 4).tolist(), flush=True)
        print('Approach TCP XY offset [m]:', target_data['observation_tcp_xy_m'], flush=True)
        print('Closing axis yaw [deg]:', round(closing_yaw, 2), flush=True)
        print('Top-down assumes TCP local +Z points DOWN; TCP closing local axis:', args.tcp_closing_axis, flush=True)
        print('WARNING: SDK final-angle bridge does not track collision-checked trajectory!', flush=True)
        current_start = fresh_state()
        check_capture_drift(current_start)
        peer_start = fresh_peer_state() if args.peer_home else None
        if args.peer_home and max(abs(math.degrees(peer_start[k]-peer_expected[k])) for k in peer_expected) > args.max_state_change_deg:
            raise RuntimeError('Other arm moved since ROI capture; recapture')
        move_client = ActionClient(node, MoveGroup, args.move_action)
        print(f'MoveIt action={args.move_action}; display={args.display_topic}', flush=True)
        if not move_client.wait_for_server(timeout_sec=8.):
            from rclpy.action import get_action_names_and_types
            available = [name for name, types in get_action_names_and_types(node)
                         if 'moveit_msgs/action/MoveGroup' in types]
            raise RuntimeError(f'MoveGroup action unavailable: {args.move_action}; available={available}. '
                               'Use the same ROS_DOMAIN_ID as MoveIt and source the workspace.')
        home_result, home_restrictions = plan_peer_home(peer_start) if args.peer_home else (None, [])
        candidates = []
        # Use the yaw inspected in the PLAN preview when E replans the same capture.
        preview_endpoint = None
        if args.execute:
            preview = json.loads((log_dir/'moveit_plan_summary.json').read_text())
            if preview.get('phase') != 'approach' or preview.get('selected_arm') != args.arm:
                raise RuntimeError('No matching approach preview; plan again')
            preview_yaw = float(preview['closing_axis_yaw_deg_selected'])
            offsets = [plus for plus in (0., 180.)
                       if abs(preview_yaw - (closing_yaw + plus)) < 1e-4]
            if len(offsets) != 1:
                raise RuntimeError('Previewed yaw differs from the current ROI selection; plan again')
            preview_endpoint = preview['approach_endpoint_rad']
            if set(preview_endpoint) != set(expected) or not all(
                    math.isfinite(float(v)) for v in preview_endpoint.values()):
                raise RuntimeError('Previewed joint endpoint missing or invalid; plan again')
            print(f'Replanning previewed yaw {preview_yaw:+.2f} deg at saved joint endpoint.', flush=True)
        else:
            offsets = (0., 180.)
        for plus in offsets:
            angle = closing_yaw + plus
            quat, tcp_base_yaw = closing_axis_to_tcp_orientation(angle, args.tcp_closing_axis,
                                                                args.yaw_offset_deg)
            R_world_tcp = r_z(math.radians(tcp_base_yaw)) @ r_y(math.pi)
            offset_tcp = np.array([*target_data['observation_tcp_xy_m'], 0.], dtype=float)
            observation_pos = pos - R_world_tcp @ offset_tcp
            print(f'Candidate closing yaw {angle:+.2f}, TCP nominal yaw {tcp_base_yaw:+.2f}; '
                  f'approach xyz={np.round(observation_pos, 4).tolist()}', flush=True)
            try:
                if args.execute:
                    saved_xyz = np.asarray(preview['pregrasp_xyz_m'], dtype=float)
                    saved_quat = np.asarray(preview['tcp_quaternion_xyzw'], dtype=float)
                    if (saved_xyz.shape != (3,) or saved_quat.shape != (4,) or
                            np.linalg.norm(saved_xyz-observation_pos) > .001 or
                            abs(float(np.dot(saved_quat, quat))) < .9999):
                        raise RuntimeError('Previewed pose differs from current ROI target; plan again')
                if not (args.min_z_m <= observation_pos[2] <= args.max_z_m) or np.linalg.norm(observation_pos) > args.max_radius_m:
                    raise ValueError('Camera-centered observation pose outside configured workspace')
                last_error = None
                for attempt in range(1, 4):
                    try:
                        print(f'Candidate {angle:+.2f} planning attempt {attempt}/3', flush=True)
                        res = plan(observation_pos, quat, peer_at_home=args.peer_home,
                                   joint_goal=preview_endpoint if args.execute else None)
                        break
                    except RuntimeError as exc:
                        last_error = exc
                        print(f'Planning attempt {attempt}/3 failed: {exc}', flush=True)
                else:
                    raise RuntimeError(f'Candidate still has no MoveIt plan: {last_error}')
                traj = res.planned_trajectory
                jt = traj.joint_trajectory
                names = list(jt.joint_names)
                if not jt.points:
                    raise RuntimeError('MoveIt returned empty planned trajectory')
                expected_names = set(expected)
                if len(names) != 6 or set(names) != expected_names:
                    raise RuntimeError(f'Unexpected joint names in trajectory: {names}; refusing dual-arm motion')
                max_change = 0.
                for pt in jt.points:
                    for n, q in zip(names, pt.positions):
                        if n in expected:
                            max_change = max(max_change, abs(math.degrees(q-expected[n])))
                restrictions = []
                # Keep delta visible in the plan summary; do not block on its magnitude.
                try:
                    validate_trial(FollowJointTrajectory.Goal(trajectory=jt), args.arm,
                                   [expected[f'{args.arm}_{i}_Joint'] for i in range(1, 7)])
                except ValueError as exc:
                    restrictions.append(f'SDK gate: {exc}')
                motion = trajectory_motion_cost(jt, current_start, args.arm)
                sdk_motion = sdk_goal_motion_cost(jt, current_start, args.arm)
                selection_cost = (sdk_motion['tool_roll_endpoint_deg'],
                                  sdk_motion['weighted_endpoint_deg'])
                print(f'PLAN SUCCESS: {len(jt.points)} points, max change={max_change:.1f} deg, '
                      f'SDK goal tool roll={selection_cost[0]:.1f} deg, '
                      f'SDK goal weighted joints={selection_cost[1]:.1f} deg; '
                      f'MoveIt path weighted travel={motion["weighted_travel_deg"]:.1f} deg', flush=True)
                if restrictions:
                    print('PREVIEW ONLY: ' + '; '.join(restrictions), flush=True)
                candidates.append((selection_cost, angle, quat, observation_pos, res, max_change, restrictions, motion, sdk_motion))
            except Exception as exc:
                print(f'Candidate {angle:+.1f} failed: {exc}', flush=True)
        if not candidates:
            raise RuntimeError('Previewed yaw replan failed' if args.execute else
                               'Neither 180-symmetric yaw candidate produced a MoveIt plan; see candidate errors above')
        candidates.sort(key=lambda c: (bool(c[6]), *c[0]))
        cost, chosen_yaw, quat, observation_pos, res, max_change, restrictions, motion, sdk_motion = candidates[0]
        restrictions = home_restrictions + restrictions
        print(f'SELECTED closing yaw: {chosen_yaw:+.2f} deg; TCP q(xyzw)={quat.round(6).tolist()}', flush=True)
        print(f'MoveIt planned duration: {res.planned_trajectory.joint_trajectory.points[-1].time_from_start.sec} sec (controller-dependent)', flush=True)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        pub = node.create_publisher(DisplayTrajectory, args.display_topic, qos)
        if home_result is not None:
            home_display = DisplayTrajectory()
            home_display.model_id = 'jetcobot_dual'
            home_display.trajectory_start = home_result.trajectory_start
            home_display.trajectory = [home_result.planned_trajectory]
            pub.publish(home_display)
            print(f'RViz: showing {peer.upper()} home plan for 3 seconds, then {args.arm.upper()} target preview.', flush=True)
            until = time.monotonic() + 3.0
            while time.monotonic() < until:
                rclpy.spin_once(node, timeout_sec=.05)
        display = DisplayTrajectory()
        display.model_id = 'jetcobot_dual'
        display.trajectory_start = res.trajectory_start
        display.trajectory = [res.planned_trajectory]
        pub.publish(display)
        until = time.monotonic() + 0.5
        while time.monotonic() < until:
            rclpy.spin_once(node, timeout_sec=.05)
        summary = {
            'mode': 'execute' if args.execute else 'plan_only',
            'phase': 'approach',
            'selected_arm': args.arm, 'yaw_camera': chosen_camera,
            'approach_mode': target_data['approach_mode'],
            'yaw_warnings': target_data.get('warnings', []),
            'execution_allowed': not restrictions,
            'peer_home': args.peer_home, 'peer_arm': peer if args.peer_home else None,
            'peer_home_planned': home_result is not None,
            'peer_home_trajectory_points': (len(home_result.planned_trajectory.joint_trajectory.points)
                                            if home_result is not None else 0),
            'peer_home_max_joint_delta_deg': (max(abs(math.degrees(q-peer_start[name]))
                for point in home_result.planned_trajectory.joint_trajectory.points
                for name,q in zip(home_result.planned_trajectory.joint_trajectory.joint_names,point.positions))
                if home_result is not None else 0.0),
            'execution_block_reasons': restrictions,
            'move_action': args.move_action, 'display_topic': args.display_topic,
            'frame': 'right_base_link', 'group': args.group,
            'tip_link': args.tip_link,
            'pregrasp_xyz_m': observation_pos.tolist(),
            'nominal_tcp_above_object_xyz_m': pos.tolist(),
            'observation_tcp_xy_m': target_data['observation_tcp_xy_m'],
            'closing_axis_yaw_deg_selected': float(chosen_yaw),
            'tcp_quaternion_xyzw': quat.tolist(),
            'max_joint_delta_deg': float(max_change),
            'weighted_joint_travel_deg': float(motion['weighted_travel_deg']),
            'tool_roll_travel_deg': float(motion['tool_roll_travel_deg']),
            'sdk_goal_tool_roll_deg': float(sdk_motion['tool_roll_endpoint_deg']),
            'sdk_goal_weighted_joint_delta_deg': float(sdk_motion['weighted_endpoint_deg']),
            'sdk_goal_joint_delta_deg': sdk_motion['joint_endpoint_delta_deg'],
            'n_trajectory_points': len(res.planned_trajectory.joint_trajectory.points),
            'approach_endpoint_rad': dict(zip(res.planned_trajectory.joint_trajectory.joint_names,
                res.planned_trajectory.joint_trajectory.points[-1].positions)),
            'warning': 'SDK bridge final-goal mode does not replay intermediate trajectory points'
        }
        (log_dir/'moveit_plan_summary.json').write_text(json.dumps(summary, indent=2))
        if restrictions:
            print('EXECUTION BLOCKED: ' + '; '.join(restrictions), flush=True)
        if not args.execute:
            print('PLAN ONLY COMPLETE. Inspect RViz path, gripper TCP convention and obstacles BEFORE execute.', flush=True)
            return 0
        if restrictions:
            raise RuntimeError('Execution refused: ' + '; '.join(restrictions))
        # Verify that MoveIt can route the selected arm and, when requested, its peer.
        param_client = node.create_client(GetParameters, '/jetcobot_dual/move_group/get_parameters')
        if not param_client.wait_for_service(timeout_sec=5.):
            raise RuntimeError('Cannot verify MoveIt execution configuration')
        params = await_future(param_client.call_async(GetParameters.Request(names=[
            'allow_trajectory_execution', 'moveit_simple_controller_manager.controller_names'])), 8.).values
        configured = set(params[1].string_array_value)
        needed_arms = (args.arm, peer) if args.peer_home else (args.arm,)
        needed = {f'guarded_{side}_controller' for side in needed_arms} | {
                  f'guarded_{side}_gripper_controller' for side in needed_arms}
        if not params[0].bool_value or configured != needed:
            mode = 'both' if args.peer_home else args.arm
            raise RuntimeError(f'Start exactly one MoveIt with execution_arm:={mode} before execution; '
                               f'controllers={sorted(configured)}')
        check_capture_drift(fresh_state())
        if args.peer_home:
            before_home = fresh_peer_state()
            peer_drift = max(abs(math.degrees(before_home[k]-peer_expected[k])) for k in peer_expected)
            if peer_drift > args.max_state_change_deg:
                raise RuntimeError(f'Other arm moved since ROI capture ({peer_drift:.2f} deg); recapture')
        exec_client = (None if args.minimal_drag else
                       ActionClient(node, ExecuteTrajectory, args.execute_action))

        def execute_approach(trajectory, side, label, timeout):
            if args.minimal_drag:
                from trial_minimal_drag_route import Preview
                route = Preview(side, 1.0, 3.0)
                try:
                    route.plan = trajectory.joint_trajectory
                    route.run(True)
                finally:
                    route.destroy_node()
            else:
                goal = ExecuteTrajectory.Goal()
                goal.trajectory = trajectory
                action_goal(exec_client, goal, label, timeout)

        if home_result is not None:
            home_start = dict(zip(home_result.trajectory_start.joint_state.name,
                                  home_result.trajectory_start.joint_state.position))
            if not all(k in home_start for k in peer_expected):
                raise RuntimeError('Peer home plan has no measured start state')
            mismatch = max(abs(math.degrees(before_home[k]-home_start[k])) for k in peer_expected)
            if mismatch > args.max_state_change_deg:
                raise RuntimeError(f'Other arm diverged from home plan start ({mismatch:.2f} deg)')
            print(f'EXECUTE PHASE 1: {peer.upper()} arm to home.', flush=True)
            execute_approach(home_result.planned_trajectory, peer, 'PEER HOME EXECUTE', 90.)
        if args.peer_home:
            at_home = fresh_peer_state()
            home_error = max(abs(math.degrees(at_home[k])) for k in at_home)
            if home_error > 2.0:
                raise RuntimeError(f'Other arm did not reach home: max joint error {home_error:.2f} deg')
            check_capture_drift(fresh_state())
            # The selected-arm preview was planned before peer motion. Replan with
            # measured peer home state so collision checking uses the new scene.
            print(f'Replanning {args.arm.upper()} target with {peer.upper()} arm at home.', flush=True)
            last_plan_error = None
            for attempt in range(1, 4):
                try:
                    print(f'Post-home target planning attempt {attempt}/3', flush=True)
                    res = plan(observation_pos, quat, peer_at_home=False,
                               joint_goal=preview_endpoint)
                    break
                except RuntimeError as exc:
                    last_plan_error = exc
                    print(f'Post-home plan attempt {attempt} failed: {exc}', flush=True)
            else:
                raise RuntimeError(
                    f'{peer.upper()} arm reached home, but no collision-valid {args.arm.upper()} target plan was found. '
                    'The selected arm did not move. Re-capture both cameras with the left arm at home '
                    'and inspect the new RViz plan before pressing E again. '
                    f'Last MoveIt error: {last_plan_error}')
            jt = res.planned_trajectory.joint_trajectory
            if not jt.points or len(jt.joint_names) != 6 or set(jt.joint_names) != set(expected):
                raise RuntimeError('Selected-arm replan has missing or unexpected joints')
            current = fresh_state()
            replan_change = max(abs(math.degrees(q-current[name]))
                                for point in jt.points
                                for name,q in zip(jt.joint_names,point.positions))
            print(f'Replanned joint change: {replan_change:.1f} deg (informational).', flush=True)
            try:
                validate_trial(FollowJointTrajectory.Goal(trajectory=jt), args.arm,
                               [current[f'{args.arm}_{i}_Joint'] for i in range(1,7)])
            except ValueError as exc:
                raise RuntimeError(f'Replanned SDK gate: {exc}') from exc
            display = DisplayTrajectory()
            display.model_id = 'jetcobot_dual'
            display.trajectory_start = res.trajectory_start
            display.trajectory = [res.planned_trajectory]
            pub.publish(display)
            until = time.monotonic() + .5
            while time.monotonic() < until:
                rclpy.spin_once(node, timeout_sec=.05)
        # The current measured state must still match the plan start.
        start_js = res.trajectory_start.joint_state
        start = dict(zip(start_js.name, start_js.position))
        if not all(k in start for k in expected):
            raise RuntimeError('MoveIt plan has no measured selected-arm start state')
        current = fresh_state()
        max_mismatch = max(abs(math.degrees(current[k]-start[k])) for k in expected)
        if max_mismatch > args.max_state_change_deg:
            raise RuntimeError(f'Current robot state diverges from planned start: {max_mismatch:.2f} deg')
        print(f'EXECUTE PHASE {2 if args.peer_home else 1}: {args.arm.upper()} arm to target.', flush=True)
        endpoint = dict(zip(res.planned_trajectory.joint_trajectory.joint_names,
                            res.planned_trajectory.joint_trajectory.points[-1].positions))
        (log_dir/'approach_commanded_endpoint.json').write_text(json.dumps({
            'arm': args.arm, 'endpoint_rad': endpoint, 'at': time.time()}, indent=2))
        print('Commanded arm goal [deg]:',
              [round(math.degrees(endpoint[k]), 2) for k in expected], flush=True)
        try:
            execute_approach(res.planned_trajectory, args.arm, 'SELECTED ARM EXECUTE', 90.)
        except Exception:
            try:
                measured = fresh_state()
                errors = {k: round(math.degrees(measured[k]-endpoint[k]), 2) for k in expected}
                (log_dir/'approach_failed_state.json').write_text(json.dumps({
                    'arm': args.arm, 'measured_rad': measured,
                    'commanded_endpoint_rad': endpoint, 'error_deg': errors,
                    'at': time.time()}, indent=2))
                print('Approach failed; measured minus commanded joint error [deg]:', errors, flush=True)
            except Exception as state_exc:
                print(f'Approach failed; current joint state unavailable: {state_exc}', flush=True)
            raise
        (log_dir/'approach_complete.json').write_text(json.dumps({
            'arm': args.arm, 'endpoint_rad': dict(zip(res.planned_trajectory.joint_trajectory.joint_names,
                res.planned_trajectory.joint_trajectory.points[-1].positions)), 'at': time.time()}))
        print('MOVEIT APPROACH SUCCESS. Moving-arm camera remains live for ROI alignment.', flush=True)
        return 0
    finally:
        # rclpy cancellation best-effort; physical stop is an external responsibility.
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('INTERRUPTED. Check physical robot; ROS goal cancellation is not guaranteed.', flush=True)
        sys.exit(130)
    except Exception as exc:
        print(f'ERROR {type(exc).__name__}: {exc}', flush=True)
        sys.exit(1)
