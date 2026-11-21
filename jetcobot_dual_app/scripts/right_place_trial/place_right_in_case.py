#!/usr/bin/env python3
"""Trial: close the right gripper at see_center and stop 30 mm above the case.

The arrival joint pose is an offline kinematic candidate for the fixed case.
MoveIt must approve the plan against the live scene before any arm command.
Default is plan only; --execute closes the gripper and moves, with no descent
or opening.
"""
import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def _pkg_share(name, fallback):
    """Installed ROS package share dir; old source path when not built."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory(name))
    except Exception:
        return Path(fallback).expanduser()


import numpy as np
import yaml

from dual_arm_coordinator_real import DualArmCoordinator, StopRequested
from move_right_sam_yaw import GRASP_CLOSED_TIP_OFFSET_MM, tcp_down_tilt_deg

CASE_CONFIG = _pkg_share('jetcobot_dual_control', '~/pinky_ws/src/jetcobot_dual/jetcobot_dual_control') / 'config' / 'tin_case.yaml'
URDF = Path.home() / 'jetcobot_moveit2_real_upload/test_urdf/urdf/jetcobot_dual_real.urdf'
CASE_HEIGHT_MM = 60.0
RIGHT_JOINTS = tuple(f'right_{i}_Joint' for i in range(1, 7))
# Right target estimated from offline FK; live MoveIt must validate collision
# and planning, with the tips about 30 mm above the rim and TCP Y parallel to base Y.
RIGHT_ARRIVAL_JOINT_DEG = (160.86, -62.04, 0.02, -24.11, 0.09, 70.71)
MAX_PLAN_ATTEMPTS = 6


def case_center_and_rim(config_path):
    config = yaml.safe_load(config_path.read_text())
    if config['frame_id'] != 'right_base_link':
        raise ValueError('Expected case coordinates in right_base_link')
    x, y, bottom = map(float, config['bottom_center_xyz_mm'])
    if not all(math.isfinite(v) for v in (x, y, bottom)):
        raise ValueError('Nonfinite case position')
    return np.array([x, y]) / 1000.0, bottom + CASE_HEIGHT_MM


def right_see_center(coord):
    """Read the right-arm named state from the running MoveIt model."""
    from rcl_interfaces.srv import GetParameters
    client = coord.node.create_client(
        GetParameters, '/jetcobot_dual/move_group/get_parameters')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt semantic model unavailable')
    response = coord.wait(client.call_async(GetParameters.Request(
        names=['robot_description_semantic'])), 8.0)
    if len(response.values) != 1 or not response.values[0].string_value:
        raise RuntimeError('MoveIt did not provide robot_description_semantic')
    root = ET.fromstring(response.values[0].string_value)
    states = [row for row in root.findall('group_state')
              if row.get('name') == 'see_center' and row.get('group') == 'arm_right']
    if len(states) != 1:
        raise RuntimeError('MoveIt has no unique arm_right/see_center state')
    values = {joint.get('name'): float(joint.get('value'))
              for joint in states[0].findall('joint')}
    if set(values) != set(RIGHT_JOINTS) or not all(math.isfinite(v) for v in values.values()):
        raise RuntimeError('Invalid arm_right/see_center state')
    return values


def wait_action(coord, client, goal, label, timeout=45.0):
    from action_msgs.msg import GoalStatus
    if not client.wait_for_server(timeout_sec=8.0):
        raise RuntimeError(f'{label} controller unavailable')
    if coord.stop:
        raise StopRequested()
    try:
        handle = coord.wait(client.send_goal_async(goal), 12.0, stoppable=False)
    except Exception:
        print(f'{label}: goal acceptance unknown; check robot and gate.', flush=True)
        raise
    if not handle.accepted:
        if coord.stop:
            raise StopRequested()
        raise RuntimeError(f'{label} rejected')
    result = handle.get_result_async()
    try:
        if coord.stop:
            raise StopRequested()
        reply = coord.wait(result, timeout)
    except BaseException:
        try:
            if not result.done():
                coord.wait(handle.cancel_goal_async(), 3.0, stoppable=False)
            terminal = coord.wait(result, 5.0, stoppable=False)
            if terminal.status not in (GoalStatus.STATUS_CANCELED, GoalStatus.STATUS_ABORTED,
                                       GoalStatus.STATUS_SUCCEEDED):
                raise RuntimeError(f'unexpected terminal status {terminal.status}')
            print(f'{label}: controller finished with status {terminal.status}.', flush=True)
        except Exception as exc:
            print(f'{label}: cancellation unconfirmed; check robot and gate ({exc}).', flush=True)
        raise
    if coord.stop:
        raise StopRequested()
    if reply.status != GoalStatus.STATUS_SUCCEEDED or reply.result.error_code != 0:
        raise RuntimeError(f'{label} failed: status={reply.status}, '
                           f'code={reply.result.error_code}, {reply.result.error_string}')


def measured_tcp(coord, state, link='right_TCP'):
    from moveit_msgs.srv import GetPositionFK
    cache = coord.node.__dict__.setdefault('_cached_clients', {})
    client = cache.get('/jetcobot_dual/compute_fk')
    if client is None:  # one client per process (FK is called thousands of times)
        client = cache['/jetcobot_dual/compute_fk'] = coord.node.create_client(
            GetPositionFK, '/jetcobot_dual/compute_fk')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt FK service unavailable')
    request = GetPositionFK.Request()
    request.header.frame_id = 'right_base_link'
    request.fk_link_names = [link]
    request.robot_state = coord.robot_state(state)
    response = coord.wait(client.call_async(request), 8.0)
    if response.error_code.val != 1 or len(response.pose_stamped) != 1:
        raise RuntimeError(f'{link} FK failed: {response.error_code.val}')
    return response.pose_stamped[0].pose


def check_case_scene(coord, case_xy, config_path, allow_absent=False):
    """Check the active case object, or explicitly allow an unmodeled case."""
    from moveit_msgs.msg import PlanningSceneComponents
    from moveit_msgs.srv import GetPlanningScene
    client = coord.node.create_client(GetPlanningScene, '/jetcobot_dual/get_planning_scene')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt planning scene service unavailable')
    request = GetPlanningScene.Request()
    request.components.components = PlanningSceneComponents.WORLD_OBJECT_GEOMETRY
    response = coord.wait(client.call_async(request), 8.0)
    matches = [item for item in response.scene.world.collision_objects
               if item.id == 'open_tin_case']
    if not matches and allow_absent:
        print('MoveIt case object is disabled; planning uses tin_case.yaml for '
              'the target pose but does not check collisions with the physical case.',
              flush=True)
        return False
    if len(matches) != 1 or len(matches[0].primitive_poses) != 5:
        raise RuntimeError('Fixed open_tin_case is not in the active MoveIt scene')
    case = matches[0]
    floor = case.primitive_poses[0]
    actual = np.array([floor.position.x, floor.position.y])
    # MoveIt stores collision objects in its world frame. In this fixed-base
    # URDF, world and right_base_link have an identity transform.
    if case.header.frame_id not in ('world', 'right_base_link'):
        raise RuntimeError(f'Unexpected case frame: {case.header.frame_id}')
    if np.linalg.norm(actual-case_xy) > .002:
        raise RuntimeError(f'Active MoveIt case XY {np.round(actual*1000, 1).tolist()} mm '
                           f'differs from tin_case.yaml {np.round(case_xy*1000, 1).tolist()} mm')
    expected_floor_z = (float(yaml.safe_load(config_path.read_text())[
        'bottom_center_xyz_mm'][2]) / 1000.0 + .0015)
    if abs(floor.position.z - expected_floor_z) > .002:
        raise RuntimeError('Active MoveIt case height differs from tin_case.yaml')
    yaw = 2.0 * math.atan2(floor.orientation.z, floor.orientation.w)
    if abs(((math.degrees(yaw)-90.0+180.0)%360.0)-180.0) > 2.0:
        raise RuntimeError('Active MoveIt case yaw differs from tin_case.yaml')
    print('Active MoveIt case pose matches the placement config.', flush=True)
    return True


def execute_right_plan(coord, planned, start):
    from control_msgs.action import FollowJointTrajectory
    from rcl_interfaces.srv import GetParameters
    from rclpy.action import ActionClient
    gate = coord.node.create_client(
        GetParameters, '/jetcobot_dual/selected_arm_execution_gate/get_parameters')
    if not gate.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt execution gate unavailable')
    settings = coord.wait(gate.call_async(GetParameters.Request(
        names=['trajectory_backend', 'execution_arm'])), 8.0).values
    if (len(settings) != 2 or settings[0].string_value != 'sdk_drag_hybrid' or
            settings[1].string_value not in ('right', 'both')):
        raise RuntimeError('Right SDK drag execution gate is not active')
    actual = coord.state()
    drift = max(abs(math.degrees(actual[name]-start[name])) for name in RIGHT_JOINTS)
    if drift > 2.0:
        raise RuntimeError(f'Right arm moved {drift:.1f} deg since planning; replan')
    trajectory = planned.trajectory.joint_trajectory
    coord.check_trajectory(trajectory, actual, group_name='arm_right')
    client = ActionClient(coord.node, FollowJointTrajectory,
        '/jetcobot_dual/guarded_right_controller/follow_joint_trajectory')
    print('RIGHT approach via guarded SDK drag controller.', flush=True)
    wait_action(coord, client, FollowJointTrajectory.Goal(trajectory=trajectory),
                'RIGHT CASE APPROACH', timeout=180.0)


def close_gripper(coord, arm='right'):
    from builtin_interfaces.msg import Duration
    from control_msgs.action import FollowJointTrajectory
    from rclpy.action import ActionClient
    from trajectory_msgs.msg import JointTrajectoryPoint
    client = ActionClient(coord.node, FollowJointTrajectory,
        f'/jetcobot_dual/guarded_{arm}_gripper_controller/follow_joint_trajectory')
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'{arm}_gripper_controller']
    goal.trajectory.points = [JointTrajectoryPoint(
        positions=[-0.83], time_from_start=Duration(sec=3))]
    print(f'CLOSE {arm.upper()} GRIPPER at see_center.', flush=True)
    wait_action(coord, client, goal, f'{arm.upper()} GRIPPER CLOSE')


def plan_closed_arrival(coord, state, case_xy, rim_z_mm):
    goal = {name: math.radians(angle) for name, angle in
            zip(RIGHT_JOINTS, RIGHT_ARRIVAL_JOINT_DEG)}
    if not coord.valid(dict(state, **goal), group_name='arm_right'):
        raise RuntimeError('Right arrival pose collides in the current MoveIt scene')
    planned = None
    for attempt in range(1, MAX_PLAN_ATTEMPTS + 1):
        candidate = coord.plan_joint_goal('right', state, goal)
        if candidate is None:
            print(f'RIGHT plan attempt {attempt}/{MAX_PLAN_ATTEMPTS} rejected; '
                  'searching another collision-free route.', flush=True)
            continue
        try:
            coord.check_trajectory(candidate.trajectory.joint_trajectory, state,
                                   group_name='arm_right')
        except RuntimeError as exc:
            if str(exc) != 'Planned trajectory collides after retiming':
                raise
            print(f'RIGHT plan attempt {attempt}/{MAX_PLAN_ATTEMPTS} failed '
                  'trajectory collision check; searching again.', flush=True)
            continue
        planned = candidate
        break
    if planned is None:
        raise RuntimeError('MoveIt found no collision-free right-arm route '
                           f'after {MAX_PLAN_ATTEMPTS} attempts')
    trajectory = planned.trajectory.joint_trajectory
    end = state.copy()
    end.update(zip(trajectory.joint_names, trajectory.points[-1].positions))
    pose = measured_tcp(coord, end)
    xyz = np.array([pose.position.x, pose.position.y, pose.position.z])
    xy_error_mm = float(np.linalg.norm(xyz[:2] - case_xy) * 1000.0)
    tip_above_rim_mm = xyz[2] * 1000.0 - GRASP_CLOSED_TIP_OFFSET_MM['right'] - rim_z_mm
    tilt = tcp_down_tilt_deg([pose.orientation.x, pose.orientation.y,
                              pose.orientation.z, pose.orientation.w])
    qx, qy, qz, qw = (pose.orientation.x, pose.orientation.y,
                      pose.orientation.z, pose.orientation.w)
    qnorm = qx*qx + qy*qy + qz*qz + qw*qw
    if not math.isfinite(qnorm) or qnorm < 1e-12:
        raise RuntimeError('Invalid planned TCP orientation')
    y_axis_world_y = 1.0 - 2.0*(qx*qx + qz*qz)/qnorm
    parallel_error = math.degrees(math.acos(min(1.0, abs(y_axis_world_y))))
    print(f'Planned arrival TCP mm={np.round(xyz*1000, 1).tolist()}; '
          f'case-center XY error={xy_error_mm:.1f} mm; '
          f'closed tips above rim={tip_above_rim_mm:.1f} mm; '
          f'down tilt={tilt:.1f} deg; '
          f'TCP Y parallel error={parallel_error:.1f} deg.', flush=True)
    if (xy_error_mm > 10.0 or not 25.0 <= tip_above_rim_mm <= 40.0 or
            tilt > 10.0 or parallel_error > 10.0):
        raise RuntimeError('Planned arrival is outside the 30 mm-above-case range')
    coord.display({'phases': [{'side': 'right', 'result': planned,
                               'start': state.copy(), 'route': 'auto'}]})
    return planned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-config', type=Path, default=CASE_CONFIG)
    parser.add_argument('--execute', action='store_true',
                        help='Close right gripper and move 30 mm above the case')
    args = parser.parse_args()
    case_xy, rim_z_mm = case_center_and_rim(args.case_config)
    print(f'CASE center XY mm={np.round(case_xy*1000, 1).tolist()}; '
          'RIGHT closed-gripper arrival 30 mm above rim; no descent or opening.',
          flush=True)
    coord = DualArmCoordinator(URDF, velocity=.10, acceleration=.10,
                               plan_time=8.0, max_plans=MAX_PLAN_ATTEMPTS,
                               planning_budget=60.0)
    try:
        state = coord.ready()
        check_case_scene(coord, case_xy, args.case_config)
        reference = right_see_center(coord)
        error = max(abs(math.degrees(state[name] - reference[name]))
                    for name in RIGHT_JOINTS)
        if error > 5.0:
            raise RuntimeError(f'Right arm must start at see_center; max error {error:.1f} deg')
        if not coord.valid(state, group_name='arm_right'):
            raise RuntimeError('Right arm start is in collision in MoveIt scene')
        planned = plan_closed_arrival(coord, state, case_xy, rim_z_mm)
        if not args.execute:
            print('PLAN ONLY COMPLETE. No gripper or arm command sent.', flush=True)
            return 0
        close_gripper(coord)
        state = coord.state()
        planned = plan_closed_arrival(coord, state, case_xy, rim_z_mm)
        execute_right_plan(coord, planned, state)
        print('RIGHT ARRIVAL COMPLETE. Gripper remains closed; no descent or opening.', flush=True)
        return 0
    finally:
        coord.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('INTERRUPTED; inspect the robot before retrying.', flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR {type(exc).__name__}: {exc}', flush=True)
        raise SystemExit(1)
