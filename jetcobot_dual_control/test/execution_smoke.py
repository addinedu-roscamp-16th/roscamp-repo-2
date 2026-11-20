#!/usr/bin/env python3
"""Selected-arm gateway test using synthetic states and a fake Pi action server."""
import os
os.environ['ROS_DOMAIN_ID'] = '219'
os.environ['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
os.environ.pop('ROS_LOCALHOST_ONLY', None)

import math
from pathlib import Path
import signal
import sys
import subprocess
import threading
import time

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from control_msgs.action import FollowJointTrajectory
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32
from trajectory_msgs.msg import JointTrajectoryPoint
from rcl_interfaces.srv import GetParameters
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints, JointConstraint


def main():
    both = '--both' in sys.argv
    refresh_mode = '--refresh' in sys.argv
    drag_mode = '--drag' in sys.argv
    hybrid_mode = '--hybrid' in sys.argv
    path_mode = '--path' in sys.argv or refresh_mode or hybrid_mode or drag_mode
    backend = ('sdk_drag_controller' if drag_mode else
               'sdk_refresh_trial_controller' if refresh_mode else
               'sdk_path_controller' if hybrid_mode else 'sdk_goal_controller')
    rclpy.init()
    node = Node('fake_dual_pi_execution_test')
    group = ReentrantCallbackGroup()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    state = {'left': [0.]*6, 'right': [0.]*6, 'calls': 0, 'cancelled': 0, 'routed': [],
             'grippers': {'left': 100, 'right': 100}, 'gripper_routed': [],
             'pause_right_until': 0., 'injected_gripper_gap': False, 'path_points': [],
              'slow_next_arm': False, 'endpoint_calls': 0}
    pubs = {s: node.create_publisher(JointState, f'/{s}/real_joint_states', 10)
            for s in ('left', 'right')}
    grip_pubs = {s: node.create_publisher(Int32, f'/{s}/gripper_value', 10) for s in ('left', 'right')}

    def publish():
        stamp = node.get_clock().now().to_msg()
        for s, pub in pubs.items():
            if s == 'right' and time.monotonic() < state['pause_right_until']:
                continue
            msg = JointState()
            msg.header.stamp = stamp
            msg.name = [f'{i}_Joint' for i in range(1, 7)]
            msg.position = list(state[s])
            pub.publish(msg)
            grip_pubs[s].publish(Int32(data=state['grippers'][s]))

    timer = node.create_timer(.1, publish)

    def fake_execute(handle):
        side = handle.request.trajectory.joint_names[0].split('_', 1)[0]
        is_gripper = handle.request.trajectory.joint_names[0].endswith('_gripper_controller')
        state['calls'] += 1
        if not is_gripper:
            state['path_points'].append(len(handle.request.trajectory.points))
        if not is_gripper:
            state['routed'].append(side)
        end = time.monotonic()+.8
        if not is_gripper and state['slow_next_arm']:
            state['slow_next_arm'] = False
            end = time.monotonic()+2.0
        if is_gripper and not state['injected_gripper_gap']:
            state['injected_gripper_gap'] = True
            state['pause_right_until'] = time.monotonic()+1.8
            end = time.monotonic()+2.5
        while time.monotonic() < end:
            if handle.is_cancel_requested:
                state['cancelled'] += 1
                handle.canceled()
                return FollowJointTrajectory.Result(error_code=0)
            time.sleep(.02)
        if is_gripper:
            value = handle.request.trajectory.points[-1].positions[0]
            state['grippers'][side] = round(100*(value+.83)/1.365)
            state['gripper_routed'].append((side, value))
        else:
            state[side] = list(handle.request.trajectory.points[-1].positions)
        handle.succeed()
        return FollowJointTrajectory.Result(error_code=0, error_string='synthetic SDK success')

    server = ActionServer(node, FollowJointTrajectory,
        f'/left/{backend}/follow_joint_trajectory', execute_callback=fake_execute,
        cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=group)
    right_server = None
    if both:
        right_server = ActionServer(node, FollowJointTrajectory,
            f'/right/{backend}/follow_joint_trajectory', execute_callback=fake_execute,
            cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=group)
    def endpoint_execute(handle):
        state['endpoint_calls'] += 1
        return fake_execute(handle)

    endpoint_servers = [ActionServer(node, FollowJointTrajectory,
        f'/{side}/sdk_endpoint_controller/follow_joint_trajectory', execute_callback=endpoint_execute,
        cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=group)
        for side in (('left', 'right') if both else ('left',))] if hybrid_mode else []
    grip_servers = [ActionServer(node, FollowJointTrajectory,
        f'/{side}/gripper_group_controller/follow_joint_trajectory', execute_callback=fake_execute,
        cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=group)
        for side in (('left', 'right') if both else ('left',))]

    def spin():
        try:
            executor.spin()
        except ExternalShutdownException:
            pass

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    path = Path('/tmp/jetcobot_dual_execution_runtime.log')
    log = path.open('w')
    process = subprocess.Popen(['ros2', 'launch', 'jetcobot_dual_control', 'dual_real.launch.py',
                                'execution_arm:='+('both' if both else 'left'),
                                 'trajectory_backend:='+('sdk_drag' if drag_mode else 'sdk_refresh_trial' if refresh_mode else 'sdk_hybrid' if hybrid_mode else 'sdk_path' if path_mode else 'sdk_goal'),
                                 'rviz:=false'],
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def until(predicate, timeout=25):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError(f'Launch exited; see {path}')
            if predicate():
                return
            time.sleep(.03)
        raise RuntimeError(f'Timed out; see {path}')

    def goal(side='left', delta_deg=3, curve=False):
        req = FollowJointTrajectory.Goal()
        req.trajectory.joint_names = [f'{side}_{i}_Joint' for i in range(1, 7)]
        current = list(state[side])
        target = list(current)
        target[0] += math.radians(delta_deg)
        req.trajectory.points = [JointTrajectoryPoint(positions=current),
                                 JointTrajectoryPoint(positions=target)]
        req.trajectory.points[-1].time_from_start.sec = 2
        if curve:
            middle = JointTrajectoryPoint(positions=[current[0]+math.radians(1.5), .015, 0., 0., 0., 0.])
            middle.time_from_start.sec = 1
            req.trajectory.points.insert(1, middle)
        return req

    try:
        client = ActionClient(node, FollowJointTrajectory,
            '/jetcobot_dual/guarded_left_controller/follow_joint_trajectory', callback_group=group)
        until(client.server_is_ready)
        grip_client = ActionClient(node, FollowJointTrajectory,
            '/jetcobot_dual/guarded_left_gripper_controller/follow_joint_trajectory', callback_group=group)
        until(grip_client.server_is_ready)
        right_client = None
        if both:
            right_client = ActionClient(node, FollowJointTrajectory,
                '/jetcobot_dual/guarded_right_controller/follow_joint_trajectory', callback_group=group)
            until(right_client.server_is_ready)
        time.sleep(.5)
        params = node.create_client(GetParameters, '/jetcobot_dual/move_group/get_parameters')
        until(params.service_is_ready)
        f = params.call_async(GetParameters.Request(names=['robot_description_semantic']))
        until(f.done)
        text = f.result().values[0].string_value
        assert 'trial_j1_plus_3deg' in text and 'name="both_arms"' not in text
        print('PASS execution launch: selected-arm measured trial target; combined group removed', flush=True)
        f = client.send_goal_async(goal('right'))
        until(f.done)
        assert not f.result().accepted and state['calls'] == 0
        print('PASS wrong-arm request rejected before fake SDK dispatch', flush=True)
        f = client.send_goal_async(goal(curve=path_mode))
        until(f.done)
        first = f.result()
        assert first.accepted
        duplicate = client.send_goal_async(goal())
        until(duplicate.done)
        assert not duplicate.result().accepted
        grip_goal = FollowJointTrajectory.Goal()
        grip_goal.trajectory.joint_names = ['left_gripper_controller']
        grip_goal.trajectory.points = [JointTrajectoryPoint(positions=[.535]),
                                        JointTrajectoryPoint(positions=[-.83])]
        grip_goal.trajectory.points[-1].time_from_start.sec = 60
        competing_grip = grip_client.send_goal_async(grip_goal)
        until(competing_grip.done)
        assert not competing_grip.result().accepted
        print('PASS gripper request rejected while arm executes (shared lock)', flush=True)
        if both:
            competing = right_client.send_goal_async(goal('right'))
            until(competing.done)
            assert not competing.result().accepted
            print('PASS both controllers connected; right request rejected while left executes', flush=True)
        result = first.get_result_async()
        until(result.done)
        assert result.result().result.error_code == 0 and state['calls'] == 1
        if path_mode:
            assert state['path_points'][0] == 3
            print(f'PASS curved MoveIt joint path forwarded to fake Pi in {backend} mode', flush=True)
        print('PASS one SDK forwarding, duplicate goal rejected, result propagated', flush=True)
        if hybrid_mode:
            state['left'] = [math.radians(45), 0., 0., 0., 0., 0.]
            state['right'] = [math.radians(-45), 0., 0., 0., 0., 0.]
            time.sleep(.5)
            f = client.send_goal_async(goal(delta_deg=3))
            until(f.done)
            assert f.result().accepted
            endpoint_result = f.result().get_result_async()
            until(endpoint_result.done)
            assert endpoint_result.result().result.error_code == 0
            assert state['endpoint_calls'] == 1
            print('PASS separated straight plan routed to fake SDK endpoint action', flush=True)
            state['left'] = [math.radians(3), 0., 0., 0., 0., 0.]
            state['right'] = [0.]*6
        time.sleep(.4)
        move_client = ActionClient(node, MoveGroup, '/jetcobot_dual/move_action', callback_group=group)
        until(move_client.server_is_ready)
        move_goal = MoveGroup.Goal()
        motion = move_goal.request
        motion.group_name = 'arm_left'
        motion.allowed_planning_time = 5.
        motion.num_planning_attempts = 1
        motion.max_velocity_scaling_factor = .1
        motion.max_acceleration_scaling_factor = .1
        motion.start_state.is_diff = True
        constraints = Constraints()
        for i in range(1, 7):
            constraints.joint_constraints.append(JointConstraint(
                joint_name=f'left_{i}_Joint', position=math.radians(4) if i == 1 else 0.,
                tolerance_above=.00001, tolerance_below=.00001, weight=1.))
        motion.goal_constraints = [constraints]
        move_goal.planning_options.plan_only = False
        f = move_client.send_goal_async(move_goal)
        until(f.done)
        assert f.result().accepted
        move_result = f.result().get_result_async()
        until(move_result.done)
        assert move_result.result().result.error_code.val == 1, move_result.result()
        assert state['calls'] == 2 + int(hybrid_mode)
        print('PASS MoveIt Plan-and-Execute -> selected gate -> fake SDK -> MoveIt success', flush=True)
        if both:
            time.sleep(.4)
            # Use the SAME MoveIt process to plan and execute the other arm.
            motion.group_name = 'arm_right'
            for c in constraints.joint_constraints:
                c.joint_name = c.joint_name.replace('left_', 'right_')
                c.position = math.radians(3) if c.joint_name == 'right_1_Joint' else 0.
            motion.goal_constraints = [constraints]
            f = move_client.send_goal_async(move_goal)
            until(f.done)
            assert f.result().accepted
            r = f.result().get_result_async()
            until(r.done)
            assert r.result().result.error_code.val == 1, r.result()
            assert state['routed'] == ['left', 'left', 'left', 'right'] if hybrid_mode else state['routed'] == ['left', 'left', 'right'], state['routed']
            print('PASS same MoveIt process switched to right group and routed only to fake right Pi', flush=True)
        for side in (('left', 'right') if both else ('left',)):
            for target in (-.83, .535):
                time.sleep(.4)
                motion.group_name = f'gripper_{side}'
                motion.goal_constraints = [Constraints(joint_constraints=[JointConstraint(
                    joint_name=f'{side}_gripper_controller', position=target,
                    tolerance_above=.00001, tolerance_below=.00001, weight=1.)])]
                f = move_client.send_goal_async(move_goal)
                until(f.done)
                assert f.result().accepted
                result = f.result().get_result_async()
                until(result.done)
                assert result.result().result.error_code.val == 1, result.result()
                assert state['gripper_routed'][-1][0] == side
                assert abs(state['gripper_routed'][-1][1]-target) < .00002
                print(f'PASS MoveIt gripper_{side} target {target} -> matching fake SDK gripper', flush=True)
        assert state['injected_gripper_gap']
        print('PASS gripper execution survived bounded 1.8s peer state gap without refreshing stale data', flush=True)
        # Give the combined state time to reflect the fake completed first move.
        time.sleep(.4)
        calls_before = state['calls']
        state['slow_next_arm'] = True
        f = client.send_goal_async(goal(delta_deg=1))
        until(f.done)
        second = f.result()
        assert second.accepted
        until(lambda: state['calls'] == calls_before+1)
        state['right'][0] += math.radians(4.0)
        result = second.get_result_async()
        until(result.done)
        assert result.result().result.error_code != 0
        until(lambda: state['cancelled'] == 1)
        f = client.send_goal_async(goal(delta_deg=1))
        until(f.done)
        assert not f.result().accepted
        print('PASS peer movement triggers remote cancellation and fault blocks subsequent goals', flush=True)
        print('ALL EXECUTION GATE CHECKS PASSED: synthetic states/fake Pi only, no hardware.', flush=True)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
        log.close()
        executor.shutdown(timeout_sec=3)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        thread.join(timeout=2)
        print(f'Log: {path}', flush=True)


if __name__ == '__main__':
    main()
