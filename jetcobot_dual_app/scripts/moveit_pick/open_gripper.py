#!/usr/bin/env python3
"""Open one JetCobot gripper through the same guarded controller used for close."""
import argparse
import time

import rclpy
from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from trajectory_msgs.msg import JointTrajectoryPoint


def wait(node, future, seconds):
    deadline = time.monotonic() + seconds
    while rclpy.ok() and not future.done() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    if not future.done():
        raise TimeoutError('Gripper controller response timed out')
    return future.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('left', 'right'), required=True)
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node(f'open_{args.arm}_gripper')
    handle = None
    try:
        name = f'/jetcobot_dual/guarded_{args.arm}_gripper_controller/follow_joint_trajectory'
        client = ActionClient(node, FollowJointTrajectory, name)
        if not client.wait_for_server(timeout_sec=8.0):
            raise RuntimeError(f'Gripper controller unavailable: {name}')
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = [f'{args.arm}_gripper_controller']
        point = JointTrajectoryPoint()
        point.positions = [0.535]  # Existing URDF open endpoint; Pi maps this to SDK 100.
        point.time_from_start = Duration(sec=3)
        goal.trajectory.points = [point]
        print(f'OPEN {args.arm.upper()} gripper', flush=True)
        handle = wait(node, client.send_goal_async(goal), 12.0)
        if not handle.accepted:
            raise RuntimeError('Gripper open goal rejected')
        reply = wait(node, handle.get_result_async(), 45.0)
        if reply.status != GoalStatus.STATUS_SUCCEEDED or reply.result.error_code != 0:
            raise RuntimeError(f'Gripper open failed: status={reply.status}, '
                               f'error={reply.result.error_code}, detail={reply.result.error_string}')
        print('GRIPPER OPEN SUCCESS', flush=True)
    except KeyboardInterrupt:
        if handle is not None and handle.accepted:
            try:
                wait(node, handle.cancel_goal_async(), 3.0)
                print('Gripper open cancel requested', flush=True)
            except Exception as exc:
                print(f'Gripper open cancel failed: {exc}', flush=True)
        raise
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR {type(exc).__name__}: {exc}', flush=True)
        raise SystemExit(1)
