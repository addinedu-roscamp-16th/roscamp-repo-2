#!/usr/bin/env python3
"""Synthetic local-only smoke check. Does not contact either physical robot."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

# Force an isolated domain before importing ROS. Never inherit hardware domain 42.
os.environ['ROS_DOMAIN_ID'] = '219'
os.environ['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
os.environ.pop('ROS_LOCALHOST_ONLY', None)

import rclpy
from rclpy.action import get_action_names_and_types
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from moveit_msgs.msg import Constraints, JointConstraint
from moveit_msgs.srv import GetMotionPlan, GetStateValidity
from tf2_ros import Buffer, TransformListener
from rclpy.time import Time


def main():
    path = Path('/tmp/jetcobot_dual_control_runtime.log')
    log = path.open('w')
    process = subprocess.Popen(
        ['ros2', 'launch', 'jetcobot_dual_control', 'dual_real.launch.py', 'rviz:=false'],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    rclpy.init()
    node = rclpy.create_node('synthetic_dual_validation')
    state = {'publish_right': True, 'last_combined': None, 'status': None}
    pubs = {side: node.create_publisher(JointState, f'/{side}/real_joint_states', 10)
            for side in ('left', 'right')}
    subscription = node.create_subscription(JointState, '/jetcobot_dual/joint_states',
        lambda msg: state.update(last_combined=msg), qos_profile_sensor_data)
    status_subscription = node.create_subscription(String, '/jetcobot_dual/state_status',
        lambda msg: state.update(status=json.loads(msg.data)), qos_profile_sensor_data)
    buffer = Buffer(node=node)
    listener = TransformListener(buffer, node)

    def publish():
        stamp = node.get_clock().now().to_msg()
        for side, pub in pubs.items():
            if side == 'right' and not state['publish_right']:
                continue
            msg = JointState()
            msg.header.stamp = stamp
            msg.name = [f'{i}_Joint' for i in range(1, 7)]
            msg.position = [0.]*6
            pub.publish(msg)

    timer = node.create_timer(.2, publish)

    def until(predicate, timeout=25):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError(f'Launch exited: {process.returncode}; see {path}')
            rclpy.spin_once(node, timeout_sec=.05)
            if predicate():
                return
        raise RuntimeError(f'Timed out; see {path}')

    try:
        until(lambda: state['last_combined'] is not None)
        assert len(state['last_combined'].name) == 12
        assert len(set(state['last_combined'].name)) == 12
        until(lambda: buffer.can_transform('world', 'left_base_link', Time()))
        tf = buffer.lookup_transform('world', 'left_base_link', Time())
        assert abs(tf.transform.translation.y-.30) < 1e-9
        assert abs(tf.transform.translation.x) < 1e-9
        assert abs(tf.transform.translation.z) < 1e-9
        print('PASS two synthetic arms -> combined 12 joints and left (.33) base at (0,+0.30,0)', flush=True)
        validity = node.create_client(GetStateValidity, '/jetcobot_dual/check_state_validity')
        until(lambda: validity.service_is_ready())
        req = GetStateValidity.Request()
        req.robot_state.joint_state.name = [f'{s}_{i}_Joint' for s in ('left', 'right') for i in range(1, 7)]
        req.robot_state.joint_state.position = [0.]*12 + [.535, .535]
        req.robot_state.joint_state.name += ['left_gripper_controller', 'right_gripper_controller']
        future = validity.call_async(req)
        until(future.done)
        response = future.result()
        assert response.valid, str(response.contacts)
        print('PASS synthetic home state collision validity', flush=True)
        client = node.create_client(GetMotionPlan, '/jetcobot_dual/plan_kinematic_path')
        until(lambda: client.service_is_ready())
        for group, sides in [('arm_left', ['left']), ('arm_right', ['right']),
                             ('both_arms', ['left', 'right'])]:
            req = GetMotionPlan.Request()
            motion = req.motion_plan_request
            motion.group_name = group
            motion.allowed_planning_time = 5.
            motion.num_planning_attempts = 1
            motion.max_velocity_scaling_factor = .1
            motion.max_acceleration_scaling_factor = .1
            motion.start_state.is_diff = True
            goal = Constraints()
            for side in sides:
                for i in range(1, 7):
                    goal.joint_constraints.append(JointConstraint(
                        joint_name=f'{side}_{i}_Joint', position=math.radians(3) if i == 1 else 0.,
                        tolerance_above=.001, tolerance_below=.001, weight=1.))
            motion.goal_constraints = [goal]
            future = client.call_async(req)
            until(future.done)
            result = future.result().motion_plan_response
            assert result.error_code.val == 1, (group, result.error_code.val)
            assert result.trajectory.joint_trajectory.points
            print('PASS joint-space planning:', group, flush=True)
        actions = dict(get_action_names_and_types(node))
        assert '/jetcobot_dual/execute_trajectory' not in actions
        assert not any('sdk_goal_controller' in name for name in actions)
        print('PASS no trajectory execution action and no hardware SDK action clients/servers', flush=True)
        state['publish_right'] = False
        until(lambda: state['status'] is not None and not state['status']['ready'], timeout=5)
        before = state['last_combined'].header.stamp
        end = time.monotonic()+.6
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.05)
        after = state['last_combined'].header.stamp
        assert (before.sec, before.nanosec) == (after.sec, after.nanosec)
        print('PASS loss of one arm stops combined publication instead of refreshing stale data', flush=True)
        print('ALL SYNTHETIC RUNTIME CHECKS PASSED; no hardware used.', flush=True)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        if process.poll() is None:
            # Let launch forward SIGINT once; signaling the whole group duplicates it.
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
        log.close()
        print(f'Runtime log: {path}', flush=True)


if __name__ == '__main__':
    main()
