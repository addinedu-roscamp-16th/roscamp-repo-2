"""MoveIt-to-SDK execution gate for selected or coordinated arms."""
import copy
import fcntl
import math
import os
from pathlib import Path
import sys
import threading
import time

import rclpy
from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32
from std_srvs.srv import SetBool
from trajectory_msgs.msg import JointTrajectoryPoint
from moveit_msgs.srv import GetStateValidity
from ament_index_python.packages import get_package_share_directory

from .model import LIMITS_DEG, LEFT_LIMITS_DEG
from .clearance import ArmClearance
from .coordinated_route import (ALL_JOINTS, ordered_points, safe_sections,
                                continuous_sections, independent_path_valid,
                                independent_arms_valid, dense_path,
                                chord_deviation)


def validate_trial(request, side, current, allow_path=False):
    trajectory = request.trajectory
    expected = [f'{side}_{i}_Joint' for i in range(1, 7)]
    if len(trajectory.joint_names) != 6 or set(trajectory.joint_names) != set(expected):
        raise ValueError('Only the selected arm, all six prefixed joints, is accepted')
    if trajectory.header.stamp.sec or trajectory.header.stamp.nanosec:
        raise ValueError('Only immediate trajectories supported')
    if not 2 <= len(trajectory.points) <= 2000:
        raise ValueError('Expected 2..2000 trajectory points')
    order = [trajectory.joint_names.index(name) for name in expected]
    limits = LEFT_LIMITS_DEG if side == 'left' else LIMITS_DEG
    points, previous_t = [], -1.
    for p in trajectory.points:
        t = p.time_from_start.sec+p.time_from_start.nanosec/1e9
        if not math.isfinite(t) or not 0 <= t <= 120 or t <= previous_t:
            raise ValueError('Invalid trajectory timing')
        if len(p.positions) != 6:
            raise ValueError('Incomplete trajectory point')
        q = [p.positions[i] for i in order]
        for i, value in enumerate(q):
            if not math.isfinite(value) or not math.radians(limits[i][0]) <= value <= math.radians(limits[i][1]):
                raise ValueError('Nonfinite target or hardware limit exceeded')
        if points and max(abs(a-b)/(t-previous_t) for a, b in zip(q, points[-1][1])) > .08:
            raise ValueError('Initial test velocity exceeds 0.08rad/s; reduce planning velocity')
        points.append((t, q))
        previous_t = t
    start, target = points[0][1], points[-1][1]
    if max(abs(a-b) for a, b in zip(start, current)) > .02:
        raise ValueError('Plan start no longer matches measured position; replan')
    if allow_path:
        return target
    delta = [b-a for a, b in zip(start, target)]
    norm2 = sum(v*v for v in delta)
    previous = -1e-6
    for _, q in points:
        fraction = sum((v-a)*d for v, a, d in zip(q, start, delta))/norm2 if norm2 > 1e-12 else 0.
        if not -1e-6 <= fraction <= 1.000001 or fraction < previous-1e-6:
            raise ValueError('SDK cannot reproduce preview reversal/overshoot')
        if any(abs(v-(a+fraction*d)) > math.radians(.25) for v, a, d in zip(q, start, delta)):
            raise ValueError('SDK cannot reproduce preview detours')
        previous = fraction
    return target


def validate_gripper(request, side):
    trajectory = request.trajectory
    if list(trajectory.joint_names) != [f'{side}_gripper_controller']:
        raise ValueError('Expected only the matching side gripper joint')
    if trajectory.header.stamp.sec or trajectory.header.stamp.nanosec:
        raise ValueError('Only immediate gripper trajectories supported')
    if not 1 <= len(trajectory.points) <= 2000:
        raise ValueError('Expected 1..2000 gripper points')
    values, previous_t = [], -1.
    for point in trajectory.points:
        t = point.time_from_start.sec+point.time_from_start.nanosec/1e9
        if not math.isfinite(t) or not 0 <= t <= 120 or t <= previous_t:
            raise ValueError('Invalid gripper timing')
        if len(point.positions) != 1:
            raise ValueError('Expected one gripper position per point')
        value = point.positions[0]
        if not math.isfinite(value) or not -.83001 <= value <= .53501:
            raise ValueError('Gripper target outside closed/open range')
        values.append(value)
        previous_t = t
    delta = values[-1]-values[0]
    if abs(delta) < 1e-8:
        if max(abs(value-values[0]) for value in values) > 1e-5:
            raise ValueError('SDK gripper cannot reproduce an intermediate detour')
    else:
        fractions = [(value-values[0])/delta for value in values]
        if any(not -1e-5 <= f <= 1.00001 for f in fractions) or any(
                b < a-1e-5 for a, b in zip(fractions, fractions[1:])):
            raise ValueError('SDK gripper cannot reproduce preview reversal/overshoot')
    return values[-1]


class ExecutionGate(Node):
    def __init__(self):
        super().__init__('selected_arm_execution_gate')
        self.declare_parameter('execution_arm', 'none')
        self.declare_parameter('trajectory_backend', 'sdk_goal')
        self.declare_parameter('coordinated_execution', False)
        self.declare_parameter('robot_description', '')
        self.declare_parameter('hybrid_clearance_m', 0.10)
        self.side = str(self.get_parameter('execution_arm').value)
        self.trajectory_backend = str(self.get_parameter('trajectory_backend').value)
        self.coordinated_execution = bool(self.get_parameter('coordinated_execution').value)
        if self.trajectory_backend not in ('sdk_goal', 'sdk_path', 'sdk_refresh_trial', 'sdk_hybrid', 'sdk_drag', 'sdk_drag_hybrid'):
            raise ValueError('trajectory_backend must be sdk_goal, sdk_path, sdk_hybrid, sdk_refresh_trial, sdk_drag or sdk_drag_hybrid')
        if self.side not in ('left', 'right', 'both'):
            raise ValueError('execution_arm must explicitly be left, right or both')
        if self.coordinated_execution and (self.side != 'both' or
                                           self.trajectory_backend != 'sdk_drag_hybrid'):
            raise ValueError('Coordinated execution requires both arms and sdk_drag_hybrid')
        # Only one such gate per PC/domain, even if two launches are accidentally started.
        self.file_lock = (Path('/tmp') / f'jetcobot_dual_execution_{os.getuid()}_{os.environ.get("ROS_DOMAIN_ID", "0")}.lock').open('a')
        try:
            fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A dual-arm execution gate is already active on this PC/domain')
        self.clearance = None
        if self.trajectory_backend == 'sdk_hybrid':
            try:
                self.clearance = ArmClearance(str(self.get_parameter('robot_description').value),
                    float(self.get_parameter('hybrid_clearance_m').value))
            except Exception as exc:
                self.get_logger().error(f'Clearance model unavailable; all arm goals use path route: {exc}')
        self.callback_group = ReentrantCallbackGroup()
        self.validity_client = None
        self.drag_waypoint_tools = None
        if self.trajectory_backend == 'sdk_drag_hybrid':
            self.declare_parameter('hybrid_check_step_deg', 1.0)
            self.declare_parameter('hybrid_drag_step_deg', 5.0)
            self.declare_parameter('hybrid_drag_deviation_deg', 1.5)
            self.declare_parameter('hybrid_drag_corner_trim_deg', 3.0)
            self.hybrid_check_step = float(self.get_parameter('hybrid_check_step_deg').value)
            self.hybrid_drag_step = float(self.get_parameter('hybrid_drag_step_deg').value)
            self.hybrid_drag_deviation = float(self.get_parameter('hybrid_drag_deviation_deg').value)
            self.hybrid_drag_corner_trim = float(self.get_parameter('hybrid_drag_corner_trim_deg').value)
            self.declare_parameter('merge_endpoint_routes', False)
            self.declare_parameter('endpoint_as_drag', False)
            # Sampling step of the coordinated (two-arm) preflight grid. The
            # planner already checks the paired path at 1 deg with enlarged
            # padding; 2 deg here roughly quarters the gate's pair checks.
            self.declare_parameter('coordinated_check_step_deg', 1.0)
            step = float(self.get_parameter('coordinated_check_step_deg').value)
            if not 0.5 <= step <= 2.0:
                raise ValueError('coordinated_check_step_deg must be 0.5..2.0')
            from . import coordinated_route as _route
            _route.CHECK_STEP = math.radians(step)
            self.declare_parameter('state_gap_grace_s', 2.5)
            self.state_gap_grace_s = float(self.get_parameter('state_gap_grace_s').value)
            if not 1.0 <= self.state_gap_grace_s <= 5.0:
                raise ValueError('state_gap_grace_s must be 1..5')
            self.endpoint_as_drag = bool(self.get_parameter('endpoint_as_drag').value)
            self.merge_endpoint_routes = bool(self.get_parameter('merge_endpoint_routes').value)
            if not 0.1 <= self.hybrid_check_step <= 2.0:
                raise ValueError('hybrid_check_step_deg must be 0.1..2.0')
            self.validity_client = self.create_client(
                GetStateValidity, '/jetcobot_dual/check_state_validity',
                callback_group=self.callback_group)
            # The PC route checker and the Pi drag bridge load the same reducer and
            # corner-rounding implementation, preventing two subtly different paths.
            bridge_dir = Path(get_package_share_directory('jetcobot_dual_bridge')) / 'scripts'
            sys.path.insert(0, str(bridge_dir))
            try:
                from trajectory_waypoints import select_waypoints, round_joint_path
                self.drag_waypoint_tools = (select_waypoints, round_joint_path)
                select_waypoints([(0.0, (0.0,)*6), (1.0, (0.0,)*6)],
                                 self.hybrid_drag_step, self.hybrid_drag_deviation)
            finally:
                if sys.path and sys.path[0] == str(bridge_dir):
                    sys.path.pop(0)
        self.selected_route = None
        self.guard = threading.Lock()
        self.active = False
        self.faulted = False
        self.latest = None
        self.gripper_feedback = {}
        self.pending_remote = None
        self.remote = None
        self.combined_remotes = {}
        self.combined_pending = {}
        self.combined_points = None
        self.combined_sections = None
        self.combined_may_move = False
        self.last_invalid_combined = None
        self.stop_late_goal = False
        self.subscription = self.create_subscription(JointState, '/jetcobot_dual/joint_states',
            self.on_state, qos_profile_sensor_data, callback_group=self.callback_group)
        self.sides = ('left', 'right') if self.side == 'both' else (self.side,)
        self.gripper_subscriptions = [self.create_subscription(
            Int32, f'/{side}/gripper_value', lambda msg, side=side: self.on_gripper(side, msg),
            qos_profile_sensor_data, callback_group=self.callback_group) for side in self.sides]
        self.sdk_clients, self.gate_servers = {}, {}
        self.hybrid_clients = {}
        self.drag_hybrid_clients = {}
        for side in self.sides:
            for kind in ('arm', 'gripper'):
                backend = ('sdk_drag_controller' if self.trajectory_backend in ('sdk_drag', 'sdk_drag_hybrid')
                           else 'sdk_refresh_trial_controller' if self.trajectory_backend == 'sdk_refresh_trial'
                           else 'sdk_path_controller' if self.trajectory_backend == 'sdk_hybrid'
                           else 'sdk_goal_controller') if kind == 'arm' else 'gripper_group_controller'
                controller = f'guarded_{side}_controller' if kind == 'arm' else f'guarded_{side}_gripper_controller'
                self.sdk_clients[side, kind] = ActionClient(self, FollowJointTrajectory,
                    f'/{side}/{backend}/follow_joint_trajectory', callback_group=self.callback_group)
                if kind == 'arm' and self.trajectory_backend == 'sdk_hybrid':
                    self.hybrid_clients[side] = ActionClient(self, FollowJointTrajectory,
                        f'/{side}/sdk_endpoint_controller/follow_joint_trajectory',
                        callback_group=self.callback_group)
                if kind == 'arm' and self.trajectory_backend == 'sdk_drag_hybrid':
                    self.drag_hybrid_clients[side] = ActionClient(self, FollowJointTrajectory,
                        f'/{side}/sdk_endpoint_controller/follow_joint_trajectory',
                        callback_group=self.callback_group)
                self.gate_servers[side, kind] = ActionServer(self, FollowJointTrajectory,
                    f'/jetcobot_dual/{controller}/follow_joint_trajectory',
                    execute_callback=lambda handle, side=side, kind=kind: self.execute(handle, side, kind),
                    goal_callback=lambda request, side=side, kind=kind: self.goal(request, side, kind),
                    cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=self.callback_group)
        self.combined_server = None
        self.fault_reset_service = None
        if self.coordinated_execution:
            self.combined_server = ActionServer(
                self, FollowJointTrajectory,
                '/jetcobot_dual/guarded_both_controller/follow_joint_trajectory',
                execute_callback=self.execute_combined,
                goal_callback=self.goal_combined,
                cancel_callback=lambda _: CancelResponse.ACCEPT,
                callback_group=self.callback_group)
            self.fault_reset_service = self.create_service(
                SetBool, '/jetcobot_dual/reset_execution_fault',
                self.reset_execution_fault, callback_group=self.callback_group)
        mode = ('coordinated controller enabled for single-arm or dual-arm MoveIt plans'
                if self.combined_server is not None else 'ONE ARM AT A TIME')
        self.get_logger().warning(f'{self.side} SDK connections enabled; {mode}. '
                                   'Multi-joint targets allowed; hardware joint limits enforced. '
                                   'Grippers connected under the same execution lock. '
                                   f'Trajectory backend={self.trajectory_backend}; No startup motion.')

    def hybrid_route(self, request, side, current, peer):
        validate_trial(request, side, current, allow_path=True)
        try:
            validate_trial(request, side, current, allow_path=False)
            if self.clearance is None:
                return 'path'
            order = [request.trajectory.joint_names.index(f'{side}_{i}_Joint') for i in range(1, 7)]
            planned = [[point.positions[i] for i in order] for point in request.trajectory.points]
            separation = self.clearance.minimum_separation(side, planned, peer)
            if separation >= self.clearance.margin:
                self.get_logger().info(f'{side}: endpoint route; conservative arm separation={separation:.3f}m')
                return 'endpoint'
            self.get_logger().info(f'{side}: path route; conservative arm separation={separation:.3f}m')
        except Exception as exc:
            self.get_logger().info(f'{side}: path route; endpoint unavailable: {exc}')
        return 'path'

    def _state_valid(self, side, own, peer):
        if self.validity_client is None or not self.validity_client.service_is_ready():
            raise ValueError('MoveIt check_state_validity is unavailable; refusing hybrid route')
        request = GetStateValidity.Request()
        request.robot_state.is_diff = True
        request.robot_state.joint_state.name = (
            [f'{side}_{i}_Joint' for i in range(1, 7)] +
            [f'{("right" if side == "left" else "left")}_{i}_Joint' for i in range(1, 7)])
        request.robot_state.joint_state.position = list(own) + list(peer)
        future = self.validity_client.call_async(request)
        deadline = time.monotonic() + 2.0
        while rclpy.ok() and not future.done() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not future.done():
            raise ValueError('MoveIt state-validity query timed out; refusing hybrid route')
        try:
            response = future.result()
        except Exception as exc:
            raise ValueError(f'MoveIt state-validity query failed: {exc}') from exc
        if response is None:
            raise ValueError('MoveIt state-validity query failed; refusing hybrid route')
        return bool(response.valid)

    def _segment_valid(self, side, start, end, peer):
        max_step = math.radians(self.hybrid_check_step)
        count = max(1, math.ceil(max(abs(b-a) for a, b in zip(start, end)) / max_step))
        for step in range(count + 1):
            fraction = step / count
            q = [a + fraction*(b-a) for a, b in zip(start, end)]
            if not self._state_valid(side, q, peer):
                return False
        return True

    def drag_avoidance_route(self, request, side, current, peer):
        """Use drag only when the direct joint segment collides and the exact
        reduced/rounded drag waypoint polyline passes MoveIt's state checker."""
        validate_trial(request, side, current, allow_path=True)
        trajectory = request.trajectory
        order = [trajectory.joint_names.index(f'{side}_{i}_Joint') for i in range(1, 7)]
        points = [(p.time_from_start.sec + p.time_from_start.nanosec/1e9,
                   tuple(p.positions[i] for i in order)) for p in trajectory.points]
        endpoint = points[-1][1]
        direct_safe = self._segment_valid(side, current, endpoint, peer)
        if direct_safe:
            self.get_logger().info(
                f'{side}: direct joint segment is collision-free; using endpoint send_goal '
                '(MoveIt intermediate points will not be replayed)')
            return 'endpoint'

        if not self._state_valid(side, points[0][1], peer):
            raise ValueError('Measured start state is invalid in current MoveIt collision scene')
        select_waypoints, round_joint_path = self.drag_waypoint_tools
        drag_points = round_joint_path(
            select_waypoints(points, self.hybrid_drag_step, self.hybrid_drag_deviation),
            self.hybrid_drag_corner_trim)
        if len(drag_points) > 61:
            raise ValueError('Collision-avoiding drag path exceeds 60 commands; replan shorter')
        previous = current
        for _, target in drag_points[1:]:
            if not self._segment_valid(side, previous, target, peer):
                raise ValueError('Reduced/rounded drag path collides in MoveIt scene; refusing motion')
            previous = target
        self.get_logger().warning(
            f'{side}: direct joint segment collides; validated drag detour with '
            f'{len(drag_points)-1} packets using current MoveIt scene')
        return 'drag'

    def on_state(self, msg):
        names = [f'{side}_{i}_Joint' for side in ('left', 'right') for i in range(1, 7)]
        if len(msg.name) != 12 or len(msg.position) != 12 or set(msg.name) != set(names):
            self.latest = None
            return
        q = dict(zip(msg.name, msg.position))
        if not all(math.isfinite(v) for v in q.values()):
            self.latest = None
            return
        stamp = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        self.latest = (time.monotonic(), stamp, q)

    def positions(self, side, max_age=1.5):
        row = self.latest
        if row is None or time.monotonic()-row[0] > max_age:
            raise ValueError('Both measured arms must be fresh')
        age = (self.get_clock().now().nanoseconds-row[1])/1e9
        if not -.25 <= age <= max_age:
            raise ValueError('Combined state stale or clocks differ')
        q = row[2]
        peer = 'right' if side == 'left' else 'left'
        return ([q[f'{side}_{i}_Joint'] for i in range(1, 7)],
                [q[f'{peer}_{i}_Joint'] for i in range(1, 7)])

    def on_gripper(self, side, msg):
        if 0 <= msg.data <= 100:
            self.gripper_feedback[side] = (time.monotonic(), msg.data)
        else:
            self.gripper_feedback.pop(side, None)

    def require_gripper_feedback(self, side):
        sample = self.gripper_feedback.get(side)
        if sample is None or time.monotonic()-sample[0] > 5:
            raise ValueError(f'{side}: no fresh valid gripper feedback (0..100); check Pi gripper_type')

    def _combined_positions(self):
        left, right = self.positions('left')
        return tuple(left + right)

    def _combined_valid(self, values):
        if self.validity_client is None or not self.validity_client.service_is_ready():
            raise ValueError('MoveIt state validity unavailable for combined motion')
        request = GetStateValidity.Request()
        request.group_name = 'both_arms'
        request.robot_state.is_diff = True
        request.robot_state.joint_state.name = list(ALL_JOINTS)
        request.robot_state.joint_state.position = list(values)
        # 2026-11-20: move_group busy with the background next-object plan can
        # drop/delay one reply. Resend once (same state) before failing; the
        # check itself is unchanged and a missing answer still raises.
        for wait_s in (2.0, 2.0):
            future = self.validity_client.call_async(request)
            deadline = time.monotonic() + wait_s
            while rclpy.ok() and not future.done() and time.monotonic() < deadline:
                time.sleep(.005)
            if future.done() and future.result() is not None:
                break
            future.cancel()
            self.get_logger().warning('Combined validity reply missing; resending once')
        if not future.done() or future.result() is None:
            raise ValueError('Combined collision check timed out or failed')
        response = future.result()
        if not response.valid:
            contacts = [(item.contact_body_1, item.contact_body_2)
                        for item in response.contacts[:8]]
            self.last_invalid_combined = (tuple(values), contacts)
        return bool(response.valid)

    def _combined_valid_many(self, states, cache, chunk=8):
        """Batched validity: send up to `chunk` requests at once.

        Same MoveIt check as _combined_valid; only the waiting is shared.
        Stops at the first chunk containing an invalid state.
        """
        if self.validity_client is None or not self.validity_client.service_is_ready():
            raise ValueError('MoveIt state validity unavailable for combined motion')
        todo = []
        seen = set()
        for q in states:
            key = tuple(q)
            if key in cache:
                if not cache[key]:
                    return False
            elif key not in seen:
                seen.add(key)
                todo.append(key)
        for begin in range(0, len(todo), chunk):
            futures = []
            for key in todo[begin:begin+chunk]:
                request = GetStateValidity.Request()
                request.group_name = 'both_arms'
                request.robot_state.is_diff = True
                request.robot_state.joint_state.name = list(ALL_JOINTS)
                request.robot_state.joint_state.position = list(key)
                futures.append((key, self.validity_client.call_async(request)))
            for attempt in range(2):
                deadline = time.monotonic() + 6.0
                while (rclpy.ok() and time.monotonic() < deadline and
                       not all(future.done() for _, future in futures)):
                    time.sleep(.002)
                missing = [i for i, (_, f) in enumerate(futures)
                           if not f.done() or f.result() is None]
                if not missing or attempt:
                    break
                self.get_logger().warning(
                    f'Combined validity: {len(missing)}/{len(futures)} replies missing; resending')
                for i in missing:
                    key, old = futures[i]
                    old.cancel()
                    request = GetStateValidity.Request()
                    request.group_name = 'both_arms'
                    request.robot_state.is_diff = True
                    request.robot_state.joint_state.name = list(ALL_JOINTS)
                    request.robot_state.joint_state.position = list(key)
                    futures[i] = (key, self.validity_client.call_async(request))
            ok = True
            for key, future in futures:
                if not future.done() or future.result() is None:
                    raise ValueError('Combined collision check timed out or failed')
                response = future.result()
                cache[key] = bool(response.valid)
                if not response.valid:
                    ok = False
                    self.last_invalid_combined = (key, [
                        (item.contact_body_1, item.contact_body_2)
                        for item in response.contacts[:8]])
            if not ok:
                return False
        return True

    def goal_combined(self, request):
        with self.guard:
            try:
                if self.active or self.faulted:
                    raise ValueError('Execution gate busy or fault latched')
                measured = self._combined_positions()
                points = ordered_points(request.trajectory, measured)
                for side in ('left', 'right'):
                    if not self.sdk_clients[side, 'arm'].server_is_ready():
                        raise ValueError(f'{side} Pi drag controller unavailable')
                    if not self.drag_hybrid_clients[side].server_is_ready():
                        raise ValueError(f'{side} Pi endpoint controller unavailable')
                self.combined_points = points
                self.active = True
                return GoalResponse.ACCEPT
            except Exception as exc:
                self.combined_points = None
                self.combined_sections = None
                self.get_logger().warning(f'Combined plan rejected before motion: {exc}')
                return GoalResponse.REJECT

    @staticmethod
    def _section_goal(side, path):
        names = [f'{side}_{i}_Joint' for i in range(1, 7)]
        offset = 0 if side == 'left' else 6
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = names
        elapsed = 0.0
        previous = None
        for combined in path:
            current = combined[offset:offset+6]
            if previous is not None:
                elapsed += max(0.01, max(abs(a-b) for a, b in zip(previous, current)) / .06)
            nanoseconds = round(elapsed * 1_000_000_000)
            sec, nanosec = divmod(nanoseconds, 1_000_000_000)
            goal.trajectory.points.append(JointTrajectoryPoint(
                positions=list(current),
                time_from_start=Duration(sec=sec, nanosec=nanosec)))
            previous = current
        if elapsed > 120.0:
            raise ValueError('Combined continuous route exceeds Pi trajectory duration limit')
        return goal

    def _combined_route(self, mode, path, valid=None, allow_endpoint=True):
        """Use one endpoint per arm if safe; otherwise one continuous drag."""
        valid = self._combined_valid if valid is None else valid
        start, target = path[0], path[-1]
        # An SDK endpoint does not promise the MoveIt detour. After the
        # observed near-contact, keep a visibly curved plan on its checked
        # waypoints even when its straight shortcut passes the scene checker.
        if (allow_endpoint and
                chord_deviation(path, 0, len(path)-1) <= math.radians(3.0) and
                independent_path_valid([start, target], valid)):
            return 'endpoint', [start, target]
        path = dense_path(path, self.hybrid_drag_step)
        select_waypoints, round_joint_path = self.drag_waypoint_tools
        reduced = {}
        for side, offset in (('left', 0), ('right', 6)):
            if mode not in ('both', side):
                reduced[side] = [start[offset:offset+6]]
                continue
            source = []
            elapsed = 0.0
            previous = None
            for combined in path:
                current = combined[offset:offset+6]
                if previous is not None:
                    elapsed += max(.01, max(abs(a-b) for a, b in zip(previous, current))/.06)
                source.append((elapsed, current))
                previous = current
            chosen = round_joint_path(
                select_waypoints(source, self.hybrid_drag_step, self.hybrid_drag_deviation),
                self.hybrid_drag_corner_trim)
            if len(chosen) > 61:
                raise ValueError(f'{side} continuous drag requires over 60 packets')
            reduced[side] = [q for _, q in chosen]
        if not independent_arms_valid(reduced['left'], reduced['right'], valid):
            raise ValueError('Reduced continuous drag route collides in MoveIt scene')
        return 'drag', path

    @staticmethod
    def _active_mode(path):
        start = path[0]
        left = any(max(abs(a-b) for a, b in zip(start[:6], q[:6])) > 1e-4
                   for q in path[1:])
        right = any(max(abs(a-b) for a, b in zip(start[6:], q[6:])) > 1e-4
                    for q in path[1:])
        if left and right:
            return 'both'
        if left:
            return 'left'
        if right:
            return 'right'
        raise ValueError('Combined route has no moving arm')

    def _minimal_drag_routes(self, mode, path, valid):
        """Keep only the necessary middle detour as one continuous drag."""
        if (chord_deviation(path, 0, len(path)-1) <= math.radians(3.0) and
                independent_path_valid([path[0], path[-1]], valid)):
            shortcut = [path[0], path[-1]]
            return [(self._active_mode(shortcut), 'endpoint', shortcut)]
        last = len(path)-1
        stride = max(1, math.ceil(last/12))
        candidates = sorted(set([0, last] + list(range(0, last+1, stride))))
        prefix, suffix = [0], [last]
        max_deviation = math.radians(3.0)  # Existing single-arm trial bound.
        for index in candidates[1:-1]:
            if (chord_deviation(path, 0, index) <= max_deviation and
                    independent_path_valid([path[0], path[index]], valid)):
                prefix.append(index)
            if (chord_deviation(path, index, last) <= max_deviation and
                    independent_path_valid([path[index], path[last]], valid)):
                suffix.append(index)
        travel = [0.0]
        for start, target in zip(path, path[1:]):
            travel.append(travel[-1] + max(abs(a-b) for a, b in zip(start, target)))
        choices = sorted((travel[j]-travel[i], (i > 0)+(j < last), i, j)
                         for i in prefix for j in suffix if i <= j)
        for _, _, begin, end in choices:
            routes = []
            if begin > 0:
                prefix_path = [path[0], path[begin]]
                routes.append((self._active_mode(prefix_path), 'endpoint', prefix_path))
            if begin < end:
                try:
                    middle_route, middle_path = self._combined_route(
                        mode, path[begin:end+1], valid)
                except ValueError as exc:
                    if ('continuous drag requires over 60 packets' in str(exc) or
                            'Reduced continuous drag route collides' in str(exc)):
                        continue
                    raise
                routes.append((self._active_mode(middle_path),
                               middle_route, middle_path))
            if end < last:
                suffix_path = [path[end], path[last]]
                routes.append((self._active_mode(suffix_path), 'endpoint', suffix_path))
            if routes:
                return routes
        raise ValueError('No valid minimal-drag decomposition of combined path')

    def _endpoints_as_drag(self, routes, valid):
        """Send checked endpoint chords as tracked drag goals.

        SDK native endpoint interpolation does not move the joints in sync,
        so the real motion can leave the checked straight joint chord. On
        2026-11-19 this entered the gripper padding near left_2_Link twice.
        Drag follows measured progress inside the Pi corridor instead. Moves
        under 5 deg stay endpoints: tiny drag goals stalled earlier.
        """
        converted = []
        for mode, route, path in routes:
            size = max(abs(a-b) for a, b in zip(path[0], path[-1]))
            if route != 'endpoint' or size < math.radians(5.0):
                converted.append((mode, route, path))
                continue
            try:
                new_route, new_path = self._combined_route(
                    mode, path, valid, allow_endpoint=False)
            except ValueError as exc:
                self.get_logger().warning(f'Endpoint kept (drag check failed): {exc}')
                converted.append((mode, route, path))
                continue
            converted.append((self._active_mode(new_path), new_route, new_path))
        return converted

    def _merged_routes(self, routes, valid):
        """Join consecutive routes into one continuous drag to avoid stops.

        Every joined chord was already checked. _combined_route re-checks the
        reduced Pi waypoints for independent arm progress; if that fails or
        needs over 60 packets, the original separate routes are kept.
        """
        joined = [routes[0][2][0]]
        for _, _, path in routes:
            joined.extend(path[1:])
        try:
            route, path = self._combined_route(self._active_mode(joined), joined, valid)
        except ValueError as exc:
            self.get_logger().warning(f'Route merge skipped; keeping {len(routes)} routes: {exc}')
            return routes
        self.get_logger().warning(f'Merged {len(routes)} routes into one continuous {route}')
        return [(self._active_mode(path), route, path)]

    def _cancel_combined(self):
        for remote in tuple(self.combined_remotes.values()):
            if remote is not None and remote.accepted:
                try:
                    remote.cancel_goal_async()
                except Exception as exc:
                    self.get_logger().error(f'Combined Pi cancel request failed: {exc}')
        for future in tuple(self.combined_pending.values()):
            def cancel_late(done):
                try:
                    remote = done.result()
                    if remote is not None and remote.accepted:
                        remote.cancel_goal_async()
                except Exception as exc:
                    self.get_logger().error(f'Late combined Pi cancel failed: {exc}')
            if future.done():
                cancel_late(future)
            else:
                future.add_done_callback(cancel_late)

    def _wait_combined_stable(self, timeout=10.0):
        """Wait for three successive fresh 12-joint samples with little motion."""
        deadline = time.monotonic() + timeout
        previous_stamp = None
        previous = None
        settled_samples = 0
        last_fresh = time.monotonic()
        while time.monotonic() < deadline:
            # 2026-11-20: one late Pi sample (idle right arm, 1.53 s) aborted a
            # LIFT here before any command was sent. Tolerate a bounded gap;
            # three fresh settled samples are still required.
            try:
                measured = self._combined_positions()
                last_fresh = time.monotonic()
            except ValueError as exc:
                if (not self._temporary_state_gap(exc) or
                        time.monotonic()-last_fresh > self.state_gap_grace_s):
                    raise
                settled_samples = 0
                time.sleep(.03)
                continue
            stamp = self.latest[1]
            if stamp != previous_stamp:
                if not self._combined_valid(measured):
                    raise RuntimeError('Measured arms entered a MoveIt collision state')
                if (previous is not None and
                        max(abs(a-b) for a, b in zip(measured, previous))
                        <= math.radians(0.5)):
                    settled_samples += 1
                else:
                    settled_samples = 0
                previous_stamp = stamp
                previous = measured
                if settled_samples >= 3:
                    return measured
            time.sleep(.05)
        raise RuntimeError('Both arms did not become stationary between sections')

    @staticmethod
    def _temporary_state_gap(exc):
        return str(exc) in ('Both measured arms must be fresh',
                            'Combined state stale or clocks differ')

    def _wait_combined_fresh(self, timeout=2.5):
        """Wait only for a bounded missing/stale state, never use old joints."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._combined_positions()
            except ValueError as exc:
                if not self._temporary_state_gap(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(.03)

    def _wait_combined_update(self, previous_stamp, timeout=2.5):
        """Wait for one new combined feedback sample after both Pi results."""
        started = time.monotonic()
        deadline = started + timeout
        while time.monotonic() < deadline:
            try:
                measured = self._combined_positions()
            except ValueError as exc:
                if not self._temporary_state_gap(exc):
                    raise
                time.sleep(.03)
                continue
            if self.latest[1] != previous_stamp and time.monotonic()-started >= .15:
                if not self._combined_valid(measured):
                    raise RuntimeError('Measured arms entered a MoveIt collision state')
                return measured
            time.sleep(.03)
        raise RuntimeError('No new measured arms after Pi route completion')

    def _confirm_recovery_stopped(self, timeout=10.0):
        """Require fresh, collision-free joints stable for two seconds."""
        deadline = time.monotonic()+timeout
        reference = None
        stable_since = None
        last_stamp = None
        samples = 0
        while time.monotonic() < deadline:
            measured = self._combined_positions()
            stamp = self.latest[1]
            if stamp == last_stamp:
                time.sleep(.05)
                continue
            if not self._combined_valid(measured):
                raise RuntimeError('Measured arms are still in MoveIt collision')
            now = time.monotonic()
            if (reference is None or
                    max(abs(a-b) for a, b in zip(measured, reference))
                    > math.radians(.5)):
                reference, stable_since, samples = measured, now, 1
            else:
                samples += 1
            last_stamp = stamp
            if samples >= 3 and now-stable_since >= 2.0:
                return measured
            time.sleep(.05)
        raise RuntimeError('Arms did not remain stationary for two seconds')

    def reset_execution_fault(self, request, response):
        """Clear only the PC latch after an operator verifies physical stop."""
        if not request.data:
            response.message = 'Inspect both physical arms, then request data=true'
            return response
        with self.guard:
            if self.active:
                response.message = 'An execution is still active; fault not cleared'
                return response
            if not self.faulted:
                response.success = True
                response.message = 'Execution gate is already unlocked; no motion sent'
                return response
        try:
            self._confirm_recovery_stopped()
            with self.guard:
                if self.active:
                    raise RuntimeError('A new execution became active')
                self.faulted = False
            response.success = True
            response.message = ('PC fault latch cleared after fresh stationary, '
                                'collision-free joint feedback; replan before motion')
            self.get_logger().warning(response.message)
        except Exception as exc:
            response.message = f'Fault remains latched: {exc}'
            self.get_logger().warning(response.message)
        return response

    def _execute_section(self, handle, mode, route, path):
        planned_start, target = path[0], path[-1]
        actual = self._wait_combined_fresh()
        if max(abs(a-b) for a, b in zip(actual, planned_start)) > math.radians(5):
            raise RuntimeError('Combined route start drifted more than 5 deg; replan')
        if not self._combined_valid(actual):
            raise RuntimeError('Measured route start is in MoveIt collision')
        checked_path = [actual] + list(path[1:])
        if mode == 'left':
            checked_path = [q[:6] + actual[6:] for q in checked_path]
        elif mode == 'right':
            checked_path = [actual[:6] + q[6:] for q in checked_path]
        # The full route was checked before any Pi command. Rechecking every
        # independent arm-progress pair here repeated hundreds of MoveIt
        # service calls at each boundary. Use the measured start and keep the
        # bounded drift check above; the live collision monitor remains on.
        command_target = checked_path[-1]
        sides = ('left', 'right') if mode == 'both' else (mode,)
        if route == 'drag':
            # Replacing the planned start with the measured start can make
            # the first segment longer than the Pi drag step (up to 5 deg
            # start drift + one 5 deg step). The Pi reducer then rejects the
            # whole goal before motion ("A planned segment exceeds the SDK
            # waypoint step"). Split only that first segment; the remaining
            # checked waypoints are unchanged.
            head = dense_path(checked_path[:2], 0.95*self.hybrid_drag_step)
            checked_path = head + checked_path[2:]
            select_waypoints, round_joint_path = self.drag_waypoint_tools
            for side in sides:
                goal = self._section_goal(side, checked_path)
                source = [(p.time_from_start.sec + p.time_from_start.nanosec/1e9,
                           tuple(p.positions)) for p in goal.trajectory.points]
                try:
                    chosen = round_joint_path(
                        select_waypoints(source, self.hybrid_drag_step,
                                         self.hybrid_drag_deviation),
                        self.hybrid_drag_corner_trim)
                except ValueError as exc:
                    raise RuntimeError(f'{side} drag goal would be rejected by Pi: {exc}')
                if len(chosen) > 61:
                    raise RuntimeError(f'{side} drag goal would need over 60 Pi packets')
        previous_may_move = self.combined_may_move
        clients = (self.drag_hybrid_clients if route == 'endpoint'
                   else {side: self.sdk_clients[side, 'arm'] for side in sides})
        pending = {side: clients[side].send_goal_async(
            self._section_goal(side, checked_path)) for side in sides}
        self.combined_pending = pending
        self.combined_may_move = True
        deadline = time.monotonic() + 5.0
        while any(not future.done() for future in pending.values()):
            if handle.is_cancel_requested or time.monotonic() > deadline:
                raise RuntimeError('Combined Pi goal acceptance failed or canceled')
            time.sleep(.02)
        remotes = {side: future.result() for side, future in pending.items()}
        self.combined_remotes = remotes
        self.combined_pending = {}
        rejected = [side for side, remote in remotes.items()
                    if remote is None or not remote.accepted]
        if rejected:
            self.combined_may_move = (previous_may_move or any(
                remote is not None and remote.accepted for remote in remotes.values()))
            raise RuntimeError(f'{", ".join(rejected)} Pi rejected the combined section')
        results = {side: remote.get_result_async() for side, remote in remotes.items()}
        deadline = time.monotonic() + (320.0 if route == 'drag' else 130.0)
        last_fresh_state = time.monotonic()
        while any(not future.done() for future in results.values()):
            for side, future in results.items():
                if future.done():
                    reply = future.result()
                    if (reply.status != GoalStatus.STATUS_SUCCEEDED or
                            reply.result.error_code != FollowJointTrajectory.Result.SUCCESSFUL):
                        raise RuntimeError(f'{side} Pi {route} route failed: '
                                           f'{reply.result.error_string}')
            if handle.is_cancel_requested:
                raise RuntimeError('Combined motion canceled')
            if time.monotonic() > deadline:
                raise RuntimeError(f'Combined Pi {route} route timed out')
            try:
                measured = self._combined_positions()
                last_fresh_state = time.monotonic()
            except ValueError as exc:
                if (not self._temporary_state_gap(exc) or
                        time.monotonic()-last_fresh_state > self.state_gap_grace_s):
                    raise
                time.sleep(.03)
                continue
            if not self._combined_valid(measured):
                raise RuntimeError('Measured arms entered a MoveIt collision state')
            time.sleep(.05)
        for side, future in results.items():
            reply = future.result()
            if (reply.status != GoalStatus.STATUS_SUCCEEDED or
                    reply.result.error_code != FollowJointTrajectory.Result.SUCCESSFUL):
                raise RuntimeError(f'{side} Pi {route} route failed: {reply.result.error_string}')
        # Pi reports success after its encoder goal has settled. One new PC
        # feedback sample lets both Pi callbacks release their busy locks
        # before the next route, without the old three-sample dwell.
        completed_stamp = self.latest[1] if self.latest is not None else None
        measured = self._wait_combined_update(completed_stamp)
        if max(abs(a-b) for a, b in zip(measured, command_target)) > math.radians(5.0):
            raise RuntimeError('Measured arms are outside the Pi goal tolerance')
        self.combined_remotes = {}

    def execute_combined(self, handle):
        result = FollowJointTrajectory.Result()
        self.combined_may_move = False
        self.last_invalid_combined = None
        try:
            points = self.combined_points
            if not points:
                raise RuntimeError('Combined plan reservation missing')
            measured = self._wait_combined_stable()
            if max(abs(a-b) for a, b in zip(measured, points[0])) > math.radians(5.0):
                raise RuntimeError('Combined start changed before section preflight; replan')
            preflight_started = time.monotonic()
            self.get_logger().warning(
                'Checking combined shortcuts and drag detours; no arm command sent yet')
            checked_states = {}
            def valid(q):
                key = tuple(q)
                if key not in checked_states:
                    checked_states[key] = self._combined_valid(q)
                return checked_states[key]
            valid.many = lambda states: self._combined_valid_many(states, checked_states)
            target = points[-1]
            if independent_path_valid([measured, target], valid):
                left_moves = max(abs(a-b) for a, b in zip(measured[:6], target[:6])) > 1e-4
                right_moves = max(abs(a-b) for a, b in zip(measured[6:], target[6:])) > 1e-4
                mode = ('both' if left_moves and right_moves else
                        'left' if left_moves else 'right')
                routes = [(mode, 'endpoint', [measured, target])]
            else:
                sections = safe_sections(points, valid)
                blocks = continuous_sections(sections, valid)
                routes = [route for mode, path in blocks
                          for route in self._minimal_drag_routes(mode, path, valid)]
            if self.endpoint_as_drag:
                routes = self._endpoints_as_drag(routes, valid)
            if self.merge_endpoint_routes and len(routes) > 1:
                routes = self._merged_routes(routes, valid)
            for mode, _, path in routes:
                for side in (('left', 'right') if mode == 'both' else (mode,)):
                    goal = self._section_goal(side, path)
                    if len(goal.trajectory.points) > 2000:
                        raise ValueError(f'{side} continuous route exceeds Pi point limit')
            self.combined_sections = routes
            simultaneous = sum(mode == 'both' for mode, _, _ in routes)
            drags = sum(route == 'drag' for _, route, _ in routes)
            self.get_logger().warning(
                f'Combined preflight passed: {len(routes)} continuous routes, '
                f'{drags} drag detours, {simultaneous} simultaneous; '
                f'elapsed={time.monotonic()-preflight_started:.1f}s')
            for index, (mode, route, path) in enumerate(routes, 1):
                if handle.is_cancel_requested:
                    raise RuntimeError('Combined motion canceled before next route')
                self.get_logger().info(f'Combined route {index}/{len(routes)}: {mode} {route}')
                self._execute_section(handle, mode, route, path)
            handle.succeed()
            result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
            result.error_string = f'Completed {len(routes)} checked combined routes'
            return result
        except Exception as exc:
            self._cancel_combined()
            if ('Measured arms entered a MoveIt collision state' in str(exc) and
                    self.last_invalid_combined is not None):
                joints, contacts = self.last_invalid_combined
                self.get_logger().error(
                    'Collision-triggering measured joints_deg='
                    f'{[round(math.degrees(q), 2) for q in joints]}; '
                    f'contacts={contacts or "not supplied by MoveIt"}')
            self.faulted = self.faulted or self.combined_may_move
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc) + '; Pi cancellation does not prove physical stop'
            if handle.is_active:
                if handle.is_cancel_requested:
                    handle.canceled()
                else:
                    handle.abort()
            self.get_logger().error(result.error_string)
            return result
        finally:
            self.combined_remotes = {}
            self.combined_pending = {}
            self.combined_points = None
            self.combined_sections = None
            self.combined_may_move = False
            with self.guard:
                self.active = False

    def goal(self, request, side, kind='arm'):
        with self.guard:
            try:
                if self.active or self.faulted:
                    raise ValueError('Busy or fault latched; inspect robot, then call reset_execution_fault')
                if self.coordinated_execution and kind == 'arm':
                    raise ValueError('Single-arm motion disabled during coordinated execution')
                current, peer = self.positions(side)
                route = (self.hybrid_route(request, side, current, peer)
                         if kind == 'arm' and self.trajectory_backend == 'sdk_hybrid'
                         else self.drag_avoidance_route(request, side, current, peer)
                         if kind == 'arm' and self.trajectory_backend == 'sdk_drag_hybrid'
                         else 'path')
                client = (self.hybrid_clients[side] if self.trajectory_backend == 'sdk_hybrid' and route == 'endpoint'
                          else self.drag_hybrid_clients[side] if self.trajectory_backend == 'sdk_drag_hybrid' and route == 'endpoint'
                          else self.sdk_clients[side, kind])
                if not client.server_is_ready():
                    raise ValueError(f'{side} Pi {kind} SDK {route} action server is not available')
                if kind == 'gripper':
                    self.require_gripper_feedback(side)
                    validate_gripper(request, side)
                else:
                    validate_trial(request, side, current, allow_path=self.trajectory_backend in ('sdk_path', 'sdk_refresh_trial', 'sdk_hybrid', 'sdk_drag', 'sdk_drag_hybrid'))
                self.selected_route = route
                self.active = True
                return GoalResponse.ACCEPT
            except ValueError as exc:
                self.get_logger().warning(f'Execution rejected without forwarding: {exc}')
                return GoalResponse.REJECT

    def execute(self, handle, side, kind='arm'):
        result = FollowJointTrajectory.Result()
        self.remote = None
        self.pending_remote = None
        self.stop_late_goal = False
        sent = False
        try:
            arm_start, peer_start = self.positions(side)
            if kind == 'gripper':
                self.require_gripper_feedback(side)
                validate_gripper(handle.request, side)
            else:
                validate_trial(handle.request, side, arm_start, allow_path=self.trajectory_backend in ('sdk_path', 'sdk_refresh_trial', 'sdk_hybrid', 'sdk_drag', 'sdk_drag_hybrid'))
                if self.trajectory_backend == 'sdk_hybrid' and self.selected_route == 'endpoint':
                    if self.hybrid_route(handle.request, side, arm_start, peer_start) != 'endpoint':
                        raise ValueError('Endpoint clearance changed before dispatch; replan')
                if self.trajectory_backend == 'sdk_drag_hybrid':
                    route_now = self.drag_avoidance_route(handle.request, side, arm_start, peer_start)
                    if route_now != self.selected_route:
                        raise ValueError('Hybrid route changed before dispatch; replan')
            if handle.is_cancel_requested:
                handle.canceled()
                result.error_code = 0
                return result
            client = (self.hybrid_clients[side] if kind == 'arm' and
                      self.trajectory_backend == 'sdk_hybrid' and self.selected_route == 'endpoint'
                      else self.drag_hybrid_clients[side] if kind == 'arm' and
                      self.trajectory_backend == 'sdk_drag_hybrid' and self.selected_route == 'endpoint'
                      else self.sdk_clients[side, kind])
            remote_goal = handle.request
            if (kind == 'arm' and self.trajectory_backend == 'sdk_drag_hybrid' and
                    self.selected_route == 'endpoint'):
                # The endpoint route intentionally sends only the planned start
                # and final joint targets. MoveIt detours are discarded only
                # after the direct segment passed scene collision sampling.
                remote_goal = copy.deepcopy(handle.request)
                remote_goal.trajectory.points = [
                    copy.deepcopy(handle.request.trajectory.points[0]),
                    copy.deepcopy(handle.request.trajectory.points[-1])]
            self.pending_remote = client.send_goal_async(
                remote_goal, feedback_callback=lambda msg: handle.publish_feedback(msg.feedback))
            sent = True

            def late_response(future):
                if self.stop_late_goal:
                    try:
                        remote = future.result()
                        if remote.accepted:
                            remote.cancel_goal_async()
                    except Exception as exc:
                        self.get_logger().error(f'Late goal cancellation failed: {exc}')

            self.pending_remote.add_done_callback(late_response)
            planned_seconds = (handle.request.trajectory.points[-1].time_from_start.sec +
                               handle.request.trajectory.points[-1].time_from_start.nanosec/1e9)
            timeout = (max(120., min(380., 3.*planned_seconds+20.))
                       if kind == 'arm' and self.trajectory_backend in ('sdk_path', 'sdk_refresh_trial', 'sdk_hybrid', 'sdk_drag', 'sdk_drag_hybrid') else 120.)
            deadline = time.monotonic()+timeout
            response_deadline = time.monotonic()+5
            remote_result = None
            last_fresh_state = time.monotonic()
            while rclpy.ok():
                # Gripper SDK queries share serial bandwidth with arm-state polling.
                # Tolerate a bounded short gap only while arms must remain stationary.
                # Goal acceptance remains strict. During arm motion, wait briefly
                # for a new combined sample instead of aborting on one missed read.
                try:
                    own, peer = self.positions(side, max_age=3.0 if kind == 'gripper' else 1.5)
                    last_fresh_state = time.monotonic()
                except ValueError as exc:
                    if (kind != 'arm' or
                            time.monotonic()-last_fresh_state > 2.5 or
                            str(exc) not in ('Both measured arms must be fresh',
                                             'Combined state stale or clocks differ')):
                        raise
                    if handle.is_cancel_requested:
                        raise RuntimeError('User canceled during temporary joint-state gap')
                    if time.monotonic() > deadline:
                        raise RuntimeError('SDK result timeout during joint-state gap')
                    time.sleep(.03)
                    continue
                if kind == 'gripper' and max(abs(a-b) for a, b in zip(own, arm_start)) > math.radians(3.0):
                    raise RuntimeError('Arm moved more than 3 deg during gripper execution')
                if max(abs(a-b) for a, b in zip(peer, peer_start)) > math.radians(3.0):
                    raise RuntimeError('Other arm moved more than 3 deg during selected-arm execution')
                if handle.is_cancel_requested:
                    raise RuntimeError('User canceled; remote stop requested')
                if time.monotonic() > deadline:
                    raise RuntimeError('SDK result timeout')
                if self.remote is None:
                    if self.pending_remote.done():
                        self.remote = self.pending_remote.result()
                        if not self.remote.accepted:
                            raise RuntimeError('Pi rejected SDK goal; no movement confirmed')
                        remote_result = self.remote.get_result_async()
                    elif time.monotonic() > response_deadline:
                        raise RuntimeError('Pi goal acknowledgement timeout; state uncertain')
                if remote_result is not None and remote_result.done():
                    response = remote_result.result()
                    result = response.result
                    if response.status == GoalStatus.STATUS_SUCCEEDED and result.error_code == 0:
                        handle.succeed()
                    else:
                        self.faulted = True
                        handle.abort()
                    return result
                time.sleep(.03)
            raise RuntimeError('ROS shutdown during SDK request')
        except Exception as exc:
            self.stop_late_goal = True
            if sent:
                self.faulted = True
            if self.remote is not None and self.remote.accepted:
                self.remote.cancel_goal_async()
            elif self.pending_remote is not None and self.pending_remote.done():
                late_response(self.pending_remote)
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc)+'; cancellation requests do not verify physical stop'
            if handle.is_active:
                if handle.is_cancel_requested:
                    handle.canceled()
                else:
                    handle.abort()
            self.get_logger().error(result.error_string)
            return result
        finally:
            with self.guard:
                self.active = False
                self.selected_route = None


def main():
    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node = ExecutionGate()
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.stop_late_goal = True
            if node.active and node.combined_server is not None:
                node._cancel_combined()
            if node.remote is not None and node.active and node.remote.accepted:
                try:
                    node.remote.cancel_goal_async()
                except Exception as exc:
                    node.get_logger().error(f'Shutdown cancellation failed: {exc}; check physical robot')
            executor.shutdown(timeout_sec=3)
            node.destroy_node()
            node.file_lock.close()
        if rclpy.ok():
            rclpy.shutdown()
