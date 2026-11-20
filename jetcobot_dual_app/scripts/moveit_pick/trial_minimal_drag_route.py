#!/usr/bin/env python3
"""Split one RViz MoveIt plan into endpoint / drag / endpoint trial segments.

Start before pressing Plan in RViz. Default is preview only. --execute forwards
the chosen segments to the existing guarded arm action, one at a time. The
sdk_drag_hybrid gate independently rechecks every segment before Pi dispatch.
"""
import argparse
import copy
import math
from pathlib import Path


def _pkg_share(name, fallback):
    """Installed ROS package share dir; old source path when not built."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory(name))
    except Exception:
        return Path(fallback).expanduser()

import sys
import time

import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from moveit_msgs.msg import DisplayTrajectory
from moveit_msgs.srv import GetStateValidity
from rcl_interfaces.srv import GetParameters
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import JointState

BRIDGE = _pkg_share('jetcobot_dual_bridge', '~/pinky_ws/src/jetcobot_dual/jetcobot_dual_bridge') / 'scripts'
sys.path.insert(0, str(BRIDGE))
from trajectory_waypoints import round_joint_path, select_waypoints


def wait(node, future, timeout=8.0):
    deadline = time.monotonic() + timeout
    while rclpy.ok() and not future.done() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.02)
    if not future.done():
        raise RuntimeError('ROS request timed out')
    return future.result()


def joint_points(trajectory, side):
    names = [f'{side}_{i}_Joint' for i in range(1, 7)]
    if len(trajectory.joint_names) != 6 or set(trajectory.joint_names) != set(names):
        raise ValueError(f'Plan is not a single {side} arm trajectory')
    order = [trajectory.joint_names.index(name) for name in names]
    return [tuple(point.positions[index] for index in order) for point in trajectory.points]


def chord_deviation(points, begin, end):
    start, target = points[begin], points[end]
    delta = [b-a for a, b in zip(start, target)]
    norm = sum(value*value for value in delta)
    maximum = 0.0
    for point in points[begin:end+1]:
        fraction = (sum((q-a)*d for q, a, d in zip(point, start, delta))/norm
                    if norm > 1e-12 else 0.0)
        fraction = min(1.0, max(0.0, fraction))
        maximum = max(maximum, max(abs(q-(a+fraction*d))
                                    for q, a, d in zip(point, start, delta)))
    return maximum


class Preview(Node):
    def __init__(self, side, step_deg, max_deviation_deg):
        super().__init__('minimal_drag_route_trial')
        self.side = side
        self.step = math.radians(step_deg)
        self.max_deviation = math.radians(max_deviation_deg)
        self.state = None
        self.plan = None
        self.create_subscription(JointState, '/jetcobot_dual/joint_states',
                                 self.on_state, 10)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.create_subscription(DisplayTrajectory, '/jetcobot_dual/display_planned_path',
                                 self.on_plan, qos)
        self.validity = self.create_client(GetStateValidity, '/jetcobot_dual/check_state_validity')
        self.parameters = self.create_client(
            GetParameters, '/jetcobot_dual/selected_arm_execution_gate/get_parameters')
        self.action = ActionClient(self, FollowJointTrajectory,
                                   f'/jetcobot_dual/guarded_{side}_controller/follow_joint_trajectory')

    def on_state(self, msg):
        required = [f'{arm}_{i}_Joint' for arm in ('left', 'right') for i in range(1, 7)]
        values = dict(zip(msg.name, msg.position))
        age = (self.get_clock().now().nanoseconds -
               msg.header.stamp.sec*1_000_000_000 - msg.header.stamp.nanosec)/1e9
        if all(name in values and math.isfinite(values[name]) for name in required) and -.25 <= age <= 1.5:
            self.state = (time.monotonic(), values)

    def on_plan(self, msg):
        if msg.trajectory:
            candidate = msg.trajectory[-1].joint_trajectory
            if len(candidate.points) >= 2:
                try:
                    joint_points(candidate, self.side)
                except ValueError:
                    return
                self.plan = copy.deepcopy(candidate)

    def measured(self):
        if self.state is None or time.monotonic()-self.state[0] > 1.5:
            raise RuntimeError('Fresh combined measured state unavailable')
        return self.state[1]

    def valid(self, own, peer):
        if not self.validity.service_is_ready():
            raise RuntimeError('MoveIt check_state_validity unavailable')
        other = 'left' if self.side == 'right' else 'right'
        request = GetStateValidity.Request()
        request.robot_state.is_diff = True
        request.robot_state.joint_state.name = (
            [f'{self.side}_{i}_Joint' for i in range(1, 7)] +
            [f'{other}_{i}_Joint' for i in range(1, 7)])
        request.robot_state.joint_state.position = list(own) + list(peer)
        response = wait(self, self.validity.call_async(request), 2.0)
        if response is None:
            raise RuntimeError('MoveIt validity reply missing')
        return response.valid

    def chord_valid(self, start, target, peer):
        count = max(1, math.ceil(max(abs(b-a) for a, b in zip(start, target))/self.step))
        for index in range(count+1):
            fraction = index/count
            if not self.valid([a+fraction*(b-a) for a, b in zip(start, target)], peer):
                return False
        return True

    def choose(self, points, current, peer):
        last = len(points)-1
        if self.chord_valid(current, points[last], peer):
            return [('endpoint', 0, last)]
        # At most 24 candidate boundaries: retain a bounded number of scene
        # queries while considering both ends of the planned detour.
        stride = max(1, math.ceil(last/24))
        candidates = sorted(set([0, last] + list(range(0, last+1, stride))))
        prefixes = [0]
        suffixes = [last]
        for index in candidates[1:-1]:
            if (chord_deviation(points, 0, index) <= self.max_deviation and
                    self.chord_valid(current, points[index], peer)):
                prefixes.append(index)
            if (chord_deviation(points, index, last) <= self.max_deviation and
                    self.chord_valid(points[index], points[last], peer)):
                suffixes.append(index)
        travel = [0.0]
        for start, target in zip(points, points[1:]):
            travel.append(travel[-1] + max(abs(b-a) for a, b in zip(start, target)))
        choices = [(travel[j]-travel[i],
                    (i > 0)+(j < last), i, j)
                   for i in prefixes for j in suffixes if i <= j]
        _, _, begin, end = min(choices)
        sections = []
        if begin > 0:
            sections.append(('endpoint', 0, begin))
        if begin < end:
            middle_start = current if begin == 0 else points[begin]
            middle = 'endpoint' if self.chord_valid(middle_start, points[end], peer) else 'drag'
            sections.append((middle, begin, end))
        if end < last:
            sections.append(('endpoint', end, last))
        return sections

    def validate_drag(self, trajectory, points, begin, end, current, peer):
        source = [(point.time_from_start.sec+point.time_from_start.nanosec/1e9, points[index])
                  for index, point in enumerate(trajectory.points) if begin <= index <= end]
        reduced = round_joint_path(select_waypoints(source, 5.0, 1.5), 3.0)
        if len(reduced) > 61:
            raise RuntimeError('Drag section requires more than 60 packets')
        previous = current
        for _, target in reduced[1:]:
            if not self.chord_valid(previous, target, peer):
                raise RuntimeError('Reduced drag section collides; no motion sent')
            previous = target
        return len(reduced)-1

    def segment_goal(self, trajectory, begin, end, measured_start):
        goal = FollowJointTrajectory.Goal()
        goal.trajectory = copy.deepcopy(trajectory)
        selected = [copy.deepcopy(point) for point in trajectory.points[begin:end+1]]
        if end == begin:
            raise ValueError('Empty segment')
        origin = (selected[0].time_from_start.sec +
                  selected[0].time_from_start.nanosec/1e9)
        for point in selected:
            t = point.time_from_start.sec + point.time_from_start.nanosec/1e9 - origin
            nanoseconds = max(0, round(t*1e9))
            point.time_from_start.sec = nanoseconds//1_000_000_000
            point.time_from_start.nanosec = nanoseconds%1_000_000_000
        # Every subgoal starts at the measured joints. The first following
        # point gets enough time for the execution gate's 0.08 rad/s bound.
        order = [goal.trajectory.joint_names.index(f'{self.side}_{i}_Joint')
                 for i in range(1, 7)]
        for index, value in zip(order, measured_start):
            selected[0].positions[index] = value
        first_dt = (selected[1].time_from_start.sec +
                    selected[1].time_from_start.nanosec/1e9)
        first_q = [selected[0].positions[index] for index in order]
        next_q = [selected[1].positions[index] for index in order]
        extra = max(0.0, max(abs(a-b) for a, b in zip(first_q, next_q))/0.08 - first_dt + 0.01)
        if extra:
            for point in selected[1:]:
                current_t = point.time_from_start.sec + point.time_from_start.nanosec/1e9
                nanoseconds = round((current_t + extra)*1e9)
                point.time_from_start.sec = nanoseconds//1_000_000_000
                point.time_from_start.nanosec = nanoseconds%1_000_000_000
        if selected[-1].time_from_start.sec + selected[-1].time_from_start.nanosec/1e9 > 120:
            raise RuntimeError('Adjusted segment exceeds 120 s trajectory limit')
        goal.trajectory.points = selected
        return goal

    def run(self, execute):
        print('Waiting for one right/left arm RViz Plan and fresh measured state...', flush=True)
        deadline = time.monotonic()+90
        while rclpy.ok() and time.monotonic() < deadline and (self.plan is None or self.state is None):
            rclpy.spin_once(self, timeout_sec=0.1)
        if self.plan is None:
            raise RuntimeError('No matching RViz plan received')
        state = self.measured()
        plan = copy.deepcopy(self.plan)
        points = joint_points(plan, self.side)
        current = tuple(state[f'{self.side}_{i}_Joint'] for i in range(1, 7))
        other = 'left' if self.side == 'right' else 'right'
        peer = tuple(state[f'{other}_{i}_Joint'] for i in range(1, 7))
        if max(abs(a-b) for a, b in zip(current, points[0])) > .02:
            raise RuntimeError('Plan start differs from measured position; Plan again')
        sections = self.choose(points, current, peer)
        for kind, begin, end in sections:
            packets = (self.validate_drag(plan, points, begin, end,
                                          current if begin == 0 else points[begin], peer)
                       if kind == 'drag' else 0)
            print(f'{kind.upper()} planned points {begin}->{end}; estimated drag packets={packets}', flush=True)
        if not execute:
            print('PREVIEW ONLY: no motion command sent', flush=True)
            return
        if not self.parameters.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('Execution gate parameter service unavailable')
        config = wait(self, self.parameters.call_async(
            GetParameters.Request(names=['trajectory_backend'])))
        if not config.values or config.values[0].string_value != 'sdk_drag_hybrid':
            raise RuntimeError('Execution gate must run trajectory_backend:=sdk_drag_hybrid')
        if not self.action.wait_for_server(timeout_sec=5.0):
            raise RuntimeError('Guarded arm controller unavailable')
        for index, (kind, begin, end) in enumerate(sections, 1):
            state = self.measured()
            actual = tuple(state[f'{self.side}_{i}_Joint'] for i in range(1, 7))
            if max(abs(a-b) for a, b in zip(actual, points[begin])) > math.radians(3.0):
                raise RuntimeError('Transition pose differs from plan by >3deg; replan remainder')
            print(f'SEND {index}/{len(sections)} {kind.upper()} {begin}->{end}', flush=True)
            accepted = wait(self, self.action.send_goal_async(
                self.segment_goal(plan, begin, end, actual)))
            if accepted is None or not accepted.accepted:
                raise RuntimeError('Guarded controller rejected segment')
            outcome = wait(self, accepted.get_result_async(), timeout=180.0)
            if outcome.status != GoalStatus.STATUS_SUCCEEDED or outcome.result.error_code != 0:
                raise RuntimeError(f'Segment failed: status={outcome.status}, result={outcome.result.error_string}')
            print(f'DONE {index}/{len(sections)} {kind.upper()}', flush=True)
            received_before = self.state[0]
            deadline = time.monotonic()+2.0
            while (rclpy.ok() and time.monotonic() < deadline and
                   self.state[0] <= received_before):
                rclpy.spin_once(self, timeout_sec=0.05)
            if self.state[0] <= received_before:
                raise RuntimeError('No new measured state after segment; next segment withheld')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('left', 'right'), default='right')
    parser.add_argument('--execute', action='store_true', help='send physical motion through sdk_drag_hybrid gate')
    parser.add_argument('--check-step-deg', type=float, default=1.0)
    parser.add_argument('--endpoint-path-deviation-deg', type=float, default=3.0)
    args = parser.parse_args()
    if not 0.1 <= args.check_step_deg <= 2 or not 0.1 <= args.endpoint_path_deviation_deg <= 10:
        parser.error('Invalid check resolution or endpoint path deviation')
    rclpy.init()
    node = Preview(args.arm, args.check_step_deg, args.endpoint_path_deviation_deg)
    try:
        node.run(args.execute)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
