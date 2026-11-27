#!/usr/bin/env python3
"""Return both physical arms concurrently to MoveIt's see_center named states."""
import argparse
import json
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import rclpy
from rclpy.action import ActionClient
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from rcl_interfaces.srv import GetParameters
from moveit_msgs.srv import GetStateValidity
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from builtin_interfaces.msg import Duration

SIDES = ('left', 'right')


def parse_see_center(xml):
    semantic = ET.fromstring(xml)
    goals = {}
    for side in SIDES:
        group = f'arm_{side}'
        matches = [row for row in semantic.findall('group_state')
                   if row.get('name') == 'see_center' and row.get('group') == group]
        if len(matches) != 1:
            raise RuntimeError(f'MoveIt SRDF has no unique {group}/see_center state')
        values = {row.get('name'): float(row.get('value')) for row in matches[0].findall('joint')}
        required = {f'{side}_{i}_Joint' for i in range(1, 7)}
        if set(values) != required or not all(math.isfinite(v) for v in values.values()):
            raise RuntimeError(f'Invalid {group}/see_center joints in MoveIt SRDF')
        goals[side] = values
    return goals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--open-grippers', action='store_true')
    parser.add_argument('--detach-held', action='store_true',
                        help='Remove any held object from the MoveIt scene first (missed grasp recovery)')
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node('return_both_see_center')
    latest = {}
    gripper_feedback = {}
    handles = {}
    sent = {}
    completed = set()
    subs = []

    def on_state(side, msg):
        values = dict(zip(msg.name, msg.position))
        q = {}
        for i in range(1, 7):
            value = values.get(f'{side}_{i}_Joint', values.get(f'{i}_Joint'))
            if value is None or not math.isfinite(value):
                return
            q[f'{side}_{i}_Joint'] = float(value)
        latest[side] = q

    def on_gripper(side, msg):
        if 0 <= msg.data <= 100:
            gripper_feedback[side] = time.monotonic()
        else:
            gripper_feedback.pop(side, None)

    for side in SIDES:
        subs.append(node.create_subscription(
            JointState, f'/{side}/real_joint_states',
            lambda msg, side=side: on_state(side, msg), 10))
        if args.open_grippers:
            subs.append(node.create_subscription(
                Int32, f'/{side}/gripper_value',
                lambda msg, side=side: on_gripper(side, msg), 10))

    def wait(future, seconds):
        deadline = time.monotonic() + seconds
        while rclpy.ok() and not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        if not future.done():
            raise TimeoutError('ROS response timed out')
        return future.result()

    def measured_states(seconds=5.0):
        deadline = time.monotonic() + seconds
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
            if all(side in latest for side in SIDES):
                return {side: dict(latest[side]) for side in SIDES}
        missing = [side for side in SIDES if side not in latest]
        publishers = {side: node.count_publishers(f'/{side}/real_joint_states')
                      for side in missing}
        raise RuntimeError(f'No measured joint state received from: {missing}; '
                           f'publisher counts: {publishers}. '
                           'Check Pi state/SDK bridges and ROS_DOMAIN_ID.')

    def wait_for_gripper_feedback(side, since, seconds=10.0):
        deadline = time.monotonic() + seconds
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if gripper_feedback.get(side, 0.0) >= since:
                return
        raise RuntimeError(f'{side} gripper feedback did not resume after arm motion; '
                           'check Pi gripper reading and /{side}/gripper_value')

    try:
        params = node.create_client(GetParameters, '/jetcobot_dual/move_group/get_parameters')
        if not params.wait_for_service(timeout_sec=6.0):
            raise RuntimeError('MoveIt parameter service unavailable')
        reply = wait(params.call_async(GetParameters.Request(names=['robot_description_semantic'])), 8.0)
        if len(reply.values) != 1 or not reply.values[0].string_value:
            raise RuntimeError('MoveIt did not provide robot_description_semantic')
        goal = parse_see_center(reply.values[0].string_value)
        if args.detach_held and not args.plan_only:
            # 2026-11-25: a missed grasp leaves the chocobar attached to the TCP
            # in MoveIt; remove it before the return so later plans are clean.
            import held_object
            for side in SIDES:
                held_object.apply(node, wait, arm=side, remove=True)
        current = measured_states()
        print('MoveIt named pose: see_center for arm_left and arm_right.', flush=True)
        for side in SIDES:
            delta = [math.degrees(goal[side][name] - current[side][name]) for name in goal[side]]
            print(f'{side.upper()} target deg: {[round(math.degrees(goal[side][name]),2) for name in goal[side]]}; '
                  f'current-to-target deg: {[round(v,2) for v in delta]}', flush=True)
        (args.out_dir / 'return_target.json').write_text(json.dumps({
            'target_rad': goal, 'start_rad': current, 'at': time.time()}, indent=2))

        # Both native SDK targets move at once. Validate their combined straight
        # joint-space interpolation against MoveIt's current scene first.
        validity = node.create_client(GetStateValidity, '/jetcobot_dual/check_state_validity')
        if not validity.wait_for_service(timeout_sec=6.0):
            raise RuntimeError('MoveIt state validity service unavailable')
        maximum = max(abs(goal[side][name] - current[side][name])
                      for side in SIDES for name in goal[side])
        steps = max(1, math.ceil(maximum / math.radians(3.0)))
        all_names = [f'{side}_{i}_Joint' for side in SIDES for i in range(1, 7)]
        for k in range(steps + 1):
            fraction = k / steps
            positions = [current[side][name] + fraction*(goal[side][name]-current[side][name])
                         for side in SIDES for name in (f'{side}_{i}_Joint' for i in range(1, 7))]
            for group in ('arm_left', 'arm_right'):
                request = GetStateValidity.Request()
                request.group_name = group
                request.robot_state.is_diff = True
                request.robot_state.joint_state.name = all_names
                request.robot_state.joint_state.position = positions
                answer = wait(validity.call_async(request), 5.0)
                if not answer.valid:
                    raise RuntimeError(f'Combined return line invalid at {fraction:.2%} for {group}')
        print(f'Combined see_center return line checked at {steps+1} states.', flush=True)
        # Re-read both arms immediately before dispatch; stale capture files are
        # irrelevant to this return command.
        measured = measured_states()
        mismatch = max(abs(math.degrees(measured[side][name]-current[side][name]))
                       for side in SIDES for name in current[side])
        if mismatch > 2.0:
            raise RuntimeError(f'Robot moved {mismatch:.1f} deg during return check; press C again')
        clients = {side: ActionClient(node, FollowJointTrajectory,
                   f'/{side}/sdk_endpoint_controller/follow_joint_trajectory') for side in SIDES}
        for side, client in clients.items():
            if not client.wait_for_server(timeout_sec=6.0):
                raise RuntimeError(f'{side} Pi SDK endpoint controller unavailable')
        requests = {}
        for side in SIDES:
            names = [f'{side}_{i}_Joint' for i in range(1, 7)]
            start = [measured[side][name] for name in names]
            target = [goal[side][name] for name in names]
            seconds = max(5.0, max(abs(a-b) for a,b in zip(start,target)) / 0.07)
            trajectory = JointTrajectory()
            trajectory.joint_names = names
            trajectory.points = [
                JointTrajectoryPoint(positions=start, time_from_start=Duration()),
                JointTrajectoryPoint(positions=target,
                                     time_from_start=Duration(sec=int(seconds),
                                         nanosec=int((seconds % 1)*1e9)))]
            requests[side] = FollowJointTrajectory.Goal(trajectory=trajectory)
        if args.plan_only:
            print('PLAN ONLY COMPLETE; both SDK goals constructed but not sent.', flush=True)
            return 0
        print('Sending LEFT and RIGHT see_center goals concurrently.', flush=True)
        sent = {side: clients[side].send_goal_async(requests[side]) for side in SIDES}
        for side in SIDES:
            handles[side] = wait(sent[side], 8.0)
        for side in SIDES:
            if not handles[side].accepted:
                raise RuntimeError(f'{side} Pi rejected see_center goal')
        results = {side: handles[side].get_result_async() for side in SIDES}
        deadline = time.monotonic() + 120.0
        while not all(f.done() for f in results.values()):
            if time.monotonic() > deadline:
                raise TimeoutError('Timed out waiting for both see_center goals')
            rclpy.spin_once(node, timeout_sec=0.05)
            for side, future in results.items():
                if future.done():
                    wrapped = future.result()
                    if wrapped.status != GoalStatus.STATUS_SUCCEEDED or wrapped.result.error_code != 0:
                        completed.add(side)
                        raise RuntimeError(f'{side} see_center failed: status={wrapped.status}, '
                                           f'code={wrapped.result.error_code}, '
                                           f'message={wrapped.result.error_string}')
                    completed.add(side)
        reached = measured_states()
        for side in SIDES:
            error = max(abs(math.degrees(reached[side][name]-goal[side][name])) for name in goal[side])
            if error > 5.0:
                raise RuntimeError(f'{side} see_center endpoint error {error:.1f} deg')
            print(f'{side.upper()} see_center reached; max error {error:.2f} deg', flush=True)
        if args.open_grippers:
            gripper_clients = {side: ActionClient(node, FollowJointTrajectory,
                f'/jetcobot_dual/guarded_{side}_gripper_controller/follow_joint_trajectory')
                for side in SIDES}
            for side, client in gripper_clients.items():
                if not client.wait_for_server(timeout_sec=8.0):
                    raise RuntimeError(f'{side} gripper controller unavailable')
            gripper_requests = {}
            for side in SIDES:
                request = FollowJointTrajectory.Goal()
                request.trajectory.joint_names = [f'{side}_gripper_controller']
                request.trajectory.points = [JointTrajectoryPoint(
                    positions=[0.535], time_from_start=Duration(sec=3))]
                gripper_requests[side] = request
            print('OPEN LEFT then RIGHT gripper at see_center.', flush=True)
            for side in SIDES:
                # The Pi skips gripper reads while an arm goal is active. A long
                # return can therefore leave the gate's five-second feedback
                # cache stale even though the arm goal has completed.
                wait_for_gripper_feedback(side, time.monotonic())
                handle = wait(gripper_clients[side].send_goal_async(gripper_requests[side]), 12.0)
                if not handle.accepted:
                    raise RuntimeError(f'{side} gripper open goal rejected; check execution gate log')
                reply = wait(handle.get_result_async(), 45.0)
                if reply.status != GoalStatus.STATUS_SUCCEEDED or reply.result.error_code != 0:
                    raise RuntimeError(f'{side} gripper open failed: status={reply.status}, '
                                       f'code={reply.result.error_code}, '
                                       f'message={reply.result.error_string}')
                print(f'{side.upper()} GRIPPER OPEN.', flush=True)
            print('BOTH GRIPPERS OPEN.', flush=True)
        print('BOTH SEE_CENTER COMPLETE. Start a new two-camera ROI capture.', flush=True)
        return 0
    finally:
        for side, future in sent.items():
            if side not in handles:
                try:
                    handles[side] = wait(future, 2.0)
                except Exception as exc:
                    print(f'{side} goal response unavailable during cancellation: {exc}', flush=True)
        for side, handle in handles.items():
            if handle.accepted and side not in completed:
                try:
                    wait(handle.cancel_goal_async(), 2.0)
                    print(f'{side} cancel requested.', flush=True)
                except Exception as exc:
                    print(f'{side} cancellation not confirmed: {exc}', flush=True)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('RETURN INTERRUPTED; cancellation requested.', flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR {type(exc).__name__}: {exc}', flush=True)
        raise SystemExit(1)
