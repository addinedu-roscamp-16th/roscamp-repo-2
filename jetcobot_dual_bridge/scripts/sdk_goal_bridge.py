#!/usr/bin/env python3
"""SDK point-to-point adapter. Does NOT execute MoveIt's intermediate path/time.

The separate sdk_goal_controller endpoint and UI identify this explicitly.
Preview detours are rejected; this is not dual-arm collision-safe execution.
"""
import math
import threading
from pathlib import Path
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer, GoalResponse, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from trajectory_msgs.msg import JointTrajectoryPoint
from std_msgs.msg import Int32
from std_srvs.srv import SetBool, Trigger
from example_interfaces.srv import AddTwoInts

from hardware_bridge import HardwareBridge
from trajectory_guard import ARM, LIMITS_DEG, sdk_goal_target
from mode_lease import ModeLease


HARDWARE_LIMIT_MARGIN_DEG = 2.0  # Existing joint-limit and movement-envelope margin.


class SDKGoalBridge(HardwareBridge):
    def __init__(self, node_name='jetcobot_sdk_goal_bridge',
                 execution_label='SDK GOAL ONLY: one native target; preview path/time NOT replayed',
                 action_name='/{side}/sdk_goal_controller/follow_joint_trajectory'):
        super().__init__(
            node_name=node_name,
            action_name=action_name,
            execution_label=execution_label)
        self.declare_parameter('execution_mode', 'sdk_goal_only')
        self.declare_parameter('native_interpolation', False)
        self.mode_lease=None
        if bool(self.get_parameter('native_interpolation').value):
            if not self.motion_enabled:
                raise RuntimeError('Native-mode comparison requires explicit enable_motion=true')
            lease=ModeLease(self.mc,Path(__file__).resolve().parent/'sdk_mode_restore.json')
            try:
                self.fresh_mode=lease.acquire(0)
            except BaseException:
                self.destroy_node()
                raise
            self.mode_lease=lease
            self.required_fresh_mode=0
            self.get_logger().warning(
                f'EXPERIMENT: native interpolation mode 0, original mode {lease.original}; '
                'normal shutdown restores original mode. No movement command sent.')
        # The capture PC requests manual positioning without opening a second serial client.
        mode = self.mc.is_free_mode()
        servos = self.mc.is_all_servo_enable()
        if mode not in (0, 1) or servos not in (0, 1):
            raise RuntimeError(f'Cannot verify initial free/servo state: {mode!r}, {servos!r}')
        self.manual_mode = bool(mode) or servos == 0
        self.handeye_mode_service = self.create_service(
            SetBool, f'/{self.side}/handeye_free_mode', self.set_handeye_free_mode)
        self.linear_descend_service = self.create_service(
            Trigger, f'/{self.side}/sdk_linear_descend_60mm', self.sdk_linear_descend,
            callback_group=ReentrantCallbackGroup())
        self.linear_descend_distance_service = self.create_service(
            AddTwoInts, f'/{self.side}/sdk_linear_descend_tenth_mm',
            self.sdk_linear_descend_distance, callback_group=ReentrantCallbackGroup())
        self.linear_xy_service = self.create_service(
            AddTwoInts, f'/{self.side}/sdk_linear_xy_tenth_mm', self.sdk_linear_xy_step,
            callback_group=ReentrantCallbackGroup())
        self.declare_parameter('slow_xy_speed', 1)
        self.slow_xy_speed = int(self.get_parameter('slow_xy_speed').value)
        if not 1 <= self.slow_xy_speed <= 10:
            raise ValueError('slow_xy_speed must be 1..10')
        self.slow_xy_cancel = threading.Event()
        self.slow_xy_active = False
        self.native_xy_cancel = threading.Event()
        self.native_xy_active = False
        self.slow_joint_active = False
        self.slow_joint_cancel = threading.Event()
        self.descend_active = False
        self.descend_cancel = threading.Event()
        self.declare_parameter('endpoint_sdk_speed', 30)
        self.endpoint_sdk_speed = int(self.get_parameter('endpoint_sdk_speed').value)
        if not 1 <= self.endpoint_sdk_speed <= 50:
            raise ValueError('endpoint_sdk_speed must be 1..50')
        self.declare_parameter('slow_joint_speed', 1)
        self.slow_joint_speed = int(self.get_parameter('slow_joint_speed').value)
        if not 1 <= self.slow_joint_speed <= 10:
            raise ValueError('slow_joint_speed must be 1..10')
        # Endpoint success waits this long with <0.2 deg change inside the goal
        # tolerance. 0 = first fresh sample inside tolerance (shortest pause).
        self.declare_parameter('endpoint_settle_s', 0.5)
        self.endpoint_settle_s = float(self.get_parameter('endpoint_settle_s').value)
        if not 0.0 <= self.endpoint_settle_s <= 2.0:
            raise ValueError('endpoint_settle_s must be 0..2')
        self.slow_joint_server = ActionServer(
            self, FollowJointTrajectory,
            f'/{self.side}/sdk_slow_goal_controller/follow_joint_trajectory',
            execute_callback=self.execute_slow_goal, goal_callback=self.slow_goal,
            cancel_callback=lambda handle: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())
        self.slow_xy_service = self.create_service(
            AddTwoInts, f'/{self.side}/sdk_linear_xy_slow_tenth_mm',
            self.sdk_linear_xy_slow_step, callback_group=ReentrantCallbackGroup())
        self.slow_xy_stop_service = self.create_service(
            Trigger, f'/{self.side}/sdk_linear_xy_stop',
            self.stop_slow_xy, callback_group=ReentrantCallbackGroup())
        self.gripper_action = ActionServer(
            self, FollowJointTrajectory,
            f'/{self.side}/gripper_group_controller/follow_joint_trajectory',
            execute_callback=self.execute_gripper,
            goal_callback=self.gripper_goal,
            cancel_callback=lambda handle: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())

    def sdk_linear_xy_step(self, request, response):
        # AddTwoInts transports signed X/Y in 0.1 mm units; sum=1 means success.
        dx, dy = request.a / 10.0, request.b / 10.0
        distance = math.hypot(dx, dy)
        if not 1.0 <= distance <= 80.0:
            self.get_logger().error(f'Rejected native XY correction {dx:.1f}, {dy:.1f} mm')
            response.sum = -1
            return response
        with self.guard:
            if self.busy or not self.motion_enabled or self.manual_mode or self.fresh_mode != 0:
                self.get_logger().error('Native XY correction unavailable: bridge busy, manual, disabled, or wrong mode')
                response.sum = -1
                return response
            self.busy = True
            self.native_xy_active = True
            self.native_xy_cancel.clear()
        sent = False
        try:
            self.current_sample()
            if self.mc.is_all_servo_enable() != 1 or self.mc.get_fresh_mode() != 0:
                raise RuntimeError('Servos or SDK interpolation mode 0 unavailable')
            if self.mc.get_reference_frame() != 0 or self.mc.get_end_type() != 0:
                raise RuntimeError('SDK base reference and flange end type are required')
            start = self.mc.get_coords()
            if not isinstance(start, (list, tuple)) or len(start) != 6 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in start):
                raise RuntimeError(f'Invalid SDK Cartesian start pose: {start!r}')
            target = [float(v) for v in start]
            target[0] += dx
            target[1] += dy
            if not all(-281.45 <= target[i] <= 281.45 for i in (0, 1)):
                raise RuntimeError(f'Native XY target outside myCobot 280 range: {target[:2]}')
            command = target.copy()
            if self.side == 'right':
                # Retain the established right-arm XY command compensation.
                # A stored first-step Z/orientation reference made later fine
                # targets unreachable and the SDK repeatedly reported no move.
                command[2] += 6.0
                command[3] = max(-179.9, min(179.9, command[3] + 3.4))
                command[4] = max(-179.9, min(179.9, command[4] + 0.7))
            if not -70.0 <= command[2] <= 412.67:
                raise RuntimeError(f'Native XY command Z outside myCobot 280 range: {command[2]:.1f} mm')
            self.get_logger().info(f'SDK MOVEL XY prediction: delta={[dx,dy]}, '
                                   f'start={start}, desired={target}, command={command}')
            sent = True
            self.mc.send_coords(command, 10, 1)
            started_at = time.monotonic()
            deadline = started_at + 45.0
            stopped_since = None
            while rclpy.ok():
                if self.native_xy_cancel.is_set():
                    raise RuntimeError('Native XY stopped by operator')
                moving = self.mc.is_moving()
                now = time.monotonic()
                if moving == 0:
                    if stopped_since is None:
                        stopped_since = now
                    # Ignore an immediate idle reply while the command starts.
                    if now - stopped_since >= 0.75 and now - started_at >= 1.0:
                        break
                elif moving == 1:
                    stopped_since = None
                else:
                    raise RuntimeError(f'Invalid SDK moving status: {moving!r}')
                if now > deadline:
                    raise RuntimeError('SDK MoveL XY still moving after 45 s')
                time.sleep(0.25)
            else:
                raise RuntimeError('ROS shutdown during SDK MoveL XY prediction')
            actual = self.mc.get_coords()
            if not isinstance(actual, (list, tuple)) or len(actual) != 6 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in actual):
                raise RuntimeError(f'Invalid SDK Cartesian feedback: {actual!r}')
            if distance >= 3.0 and math.dist(actual[:2], start[:2]) < 0.5:
                raise RuntimeError('SDK MoveL XY stopped without measurable XY motion')
            position_error_mm = math.dist(actual[:3], target[:3])
            if position_error_mm > 10.0:
                self.get_logger().warning(
                    f'SDK MoveL XY position differs from target by {position_error_mm:.1f} mm; '
                    f'target={target[:3]}, actual={actual[:3]}')
            response.sum = 1
            self.get_logger().info(
                f'SDK MoveL XY stopped; actual delta='
                f'{[round(actual[i]-start[i],2) for i in range(3)]}; '
                f'position error={position_error_mm:.1f} mm')
            return response
        except Exception as exc:
            if sent:
                try:
                    self.mc.stop()
                except Exception as stop_exc:
                    self.get_logger().error(f'SDK stop after XY MoveL failure failed: {stop_exc}')
            response.sum = -1
            self.get_logger().error(f'Linear XY prediction failed: {exc}')
            return response
        finally:
            with self.guard:
                self.busy = False
                self.native_xy_active = False
                self.native_xy_cancel.clear()

    def stop_slow_xy(self, request, response):
        with self.guard:
            active = bool(self.native_xy_active or self.slow_xy_active or
                          self.slow_joint_active or self.descend_active)
            if active:
                self.native_xy_cancel.set()
                self.slow_xy_cancel.set()
                self.slow_joint_cancel.set()
                self.descend_cancel.set()
        response.success = active
        if active:
            try:
                # Stop from this service callback, without waiting for a pose poll.
                self.mc.stop()
                response.message = 'SDK motion stop sent'
            except Exception as exc:
                response.success = False
                response.message = f'SDK stop failed: {exc}'
                self.get_logger().error(response.message)
        else:
            response.message = 'No stoppable SDK motion is active'
        return response

    def sdk_linear_xy_slow_step(self, request, response):
        # One finite SDK increment per nonzero base axis. Each increment has its
        # own distance target; S calls mc.stop() from the stop-service callback.
        dx, dy = request.a / 10.0, request.b / 10.0
        distance = math.hypot(dx, dy)
        if not 1.0 <= distance <= 30.0:
            self.get_logger().error(f'Rejected slow XY correction {dx:.1f}, {dy:.1f} mm')
            response.sum = -1
            return response
        with self.guard:
            if self.busy or not self.motion_enabled or self.manual_mode or self.fresh_mode != 0:
                response.sum = -1
                return response
            self.busy = True
            self.slow_xy_active = True
            self.slow_xy_cancel.clear()
        sent = False
        try:
            self.current_sample()
            if self.mc.is_all_servo_enable() != 1 or self.mc.get_fresh_mode() != 0:
                raise RuntimeError('Servos or SDK interpolation mode 0 unavailable')
            if self.mc.get_reference_frame() != 0 or self.mc.get_end_type() != 0:
                raise RuntimeError('SDK base reference and flange end type are required')
            start = self.mc.get_coords()
            if not isinstance(start, (list, tuple)) or len(start) != 6 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in start):
                raise RuntimeError(f'Invalid SDK Cartesian start pose: {start!r}')
            target = [float(v) for v in start]
            target[0] += dx
            target[1] += dy
            if not all(-281.45 <= target[i] <= 281.45 for i in (0, 1)):
                raise RuntimeError('Slow XY target outside myCobot 280 range')
            self.get_logger().warning(
                f'SLOW XY FINITE INCREMENT: delta={[dx, dy]} mm, speed={self.slow_xy_speed}, '
                f'start={start}, target={target}')
            for axis in (0, 1):
                if self.slow_xy_cancel.is_set():
                    raise InterruptedError('Slow XY stop requested')
                actual = self.mc.get_coords()
                remaining = target[axis] - actual[axis]
                if abs(remaining) <= 0.5:
                    continue
                other = 1 - axis
                other_start = actual[other]
                axis_start = actual[axis]
                self.get_logger().info(
                    f'SLOW XY increment axis={axis+1}, distance={remaining:.1f} mm, '
                    f'speed={self.slow_xy_speed}')
                self.mc.jog_increment_coord(axis + 1, remaining, self.slow_xy_speed)
                sent = True
                deadline = time.monotonic() + 30.0
                stable_since = None
                while rclpy.ok():
                    if self.slow_xy_cancel.is_set():
                        raise InterruptedError('Slow XY stop requested')
                    actual = self.mc.get_coords()
                    if not isinstance(actual, (list, tuple)) or len(actual) != 6 or not all(
                            isinstance(v, (int, float)) and math.isfinite(v) for v in actual):
                        raise RuntimeError(f'Invalid SDK Cartesian feedback: {actual!r}')
                    progress = (actual[axis] - axis_start) * (1 if remaining > 0 else -1)
                    z_delta = actual[2] - start[2]
                    angular_error = max(abs((actual[i] - start[i] + 180.0) % 360.0 - 180.0)
                                        for i in (3, 4, 5))
                    if (progress < -3.0 or progress > abs(remaining) + 5.0 or
                            abs(actual[other] - other_start) > 5.0 or
                            abs(z_delta) > 5.0 or angular_error > 8.0):
                        raise RuntimeError(f'Slow XY increment drifted from axis/Z/orientation: {actual!r}')
                    if abs(actual[axis] - target[axis]) <= 3.0:
                        if stable_since is None:
                            stable_since = time.monotonic()
                        if time.monotonic() - stable_since >= 0.5:
                            break
                    else:
                        stable_since = None
                    if time.monotonic() > deadline:
                        raise RuntimeError(f'Slow XY increment did not reach axis {axis+1}: {actual!r}')
                    self.slow_xy_cancel.wait(0.1)
                else:
                    raise RuntimeError('ROS shutdown during slow XY increment')
            if self.slow_xy_cancel.is_set():
                raise InterruptedError('Slow XY stop requested')
            actual = self.mc.get_coords()
            if (math.hypot(actual[0]-target[0], actual[1]-target[1]) > 5.0 or
                    abs(actual[2]-start[2]) > 5.0 or
                    max(abs((actual[i]-start[i]+180.0)%360.0-180.0)
                        for i in (3,4,5)) > 8.0):
                raise RuntimeError(f'Slow XY increment endpoint differs from target: {actual!r}')
            response.sum = 1
            self.get_logger().info(f'SLOW XY increment complete: actual={actual!r}')
            return response
        except Exception as exc:
            if sent:
                try:
                    self.mc.stop()
                except Exception as stop_exc:
                    self.get_logger().error(f'SDK stop after slow XY failed: {stop_exc}')
            response.sum = -1
            self.get_logger().error(f'Slow XY failed/stopped: {exc}')
            return response
        finally:
            with self.guard:
                self.slow_xy_active = False
                self.busy = False
            self.slow_xy_cancel.clear()

    def sdk_linear_descend(self, request, response):
        return self._sdk_linear_descend_mm(60.0, response)

    def sdk_linear_descend_distance(self, request, response):
        distance_mm = request.a / 10.0
        if request.b != 0 or not -10.0 <= distance_mm <= 100.0:
            response.sum = -1
            self.get_logger().error(f'Invalid descent distance: {distance_mm:g} mm')
            return response
        result = self._sdk_linear_descend_mm(distance_mm, Trigger.Response(),
                                             compensate_right=True)
        response.sum = 1 if result.success else -1
        return response

    def _sdk_linear_descend_mm(self, distance_mm, response, compensate_right=False):
        # Native mode=1 interpolates a Cartesian line in the robot base frame.
        with self.guard:
            if self.busy or not self.motion_enabled or self.manual_mode or self.fresh_mode != 0:
                response.message = 'SDK bridge is busy, manual, disabled, or not in interpolation mode 0'
                return response
            self.busy = True
            self.descend_active = True
            self.descend_cancel.clear()
        sent = False
        try:
            self.current_sample()  # Refuse motion without fresh measured joints.
            if self.mc.is_all_servo_enable() != 1:
                raise RuntimeError('Servos are not enabled')
            if self.mc.get_fresh_mode() != 0:
                raise RuntimeError('SDK interpolation mode 0 is required for linear motion')
            if self.mc.get_reference_frame() != 0 or self.mc.get_end_type() != 0:
                raise RuntimeError('SDK base reference and flange end type are required')
            start = self.mc.get_coords()
            if not isinstance(start, (list, tuple)) or len(start) != 6 or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in start):
                raise RuntimeError(f'Invalid SDK Cartesian start pose: {start!r}')
            target = [float(v) for v in start]
            target[2] -= distance_mm
            command = target.copy()
            # Right SDK MoveL landed 6.0 mm below its commanded flange Z on
            # 2026-11-17 (two ~20 mm descents). Command higher, verify against
            # the original requested endpoint, and leave the left arm unchanged.
            if self.side == 'right' and compensate_right:
                command[2] += 6.0
                if 15.0 <= distance_mm <= 45.0:
                    # Right Pi feedback from two 2026-11-18 grasps (21.6 and
                    # 25.4 mm down): flange XY moved about (-1.8, -2.3) mm and
                    # Euler angles about (-1.8, +2.4, -0.6) deg despite a
                    # fixed-XY/orientation request. Counter those repeatable
                    # endpoint errors without changing the desired TCP target.
                    command[0] += 1.8
                    command[1] += 2.4
                    command[3] = min(179.9, command[3] + 1.8)
                    command[4] -= 2.4
                    command[5] += 0.6
            if not all(-281.45 <= command[i] <= 281.45 for i in (0, 1)):
                raise RuntimeError(f'Native command XY outside myCobot 280 range: {command[:2]}')
            if not -70.0 <= command[2] <= 412.67:
                raise RuntimeError(f'Native command Z outside myCobot 280 range: {command[2]:.1f} mm')
            self.get_logger().warning(f'SDK LINEAR VERTICAL {distance_mm:g} mm (+down): '
                                      f'start={start}, desired={target}, command={command}, speed=10')
            sent = True
            self.mc.send_coords(command, 10, 1)
            deadline = time.monotonic() + 45.0
            stable_since = None
            last = None
            while rclpy.ok():
                if self.descend_cancel.is_set():
                    raise InterruptedError('S stop requested during descent')
                # get_coords is the direct SDK feedback for this Cartesian motion.
                # A concurrent get_angles poll can lag while the serial bus is busy.
                actual = self.mc.get_coords()
                if not isinstance(actual, (list, tuple)) or len(actual) != 6 or not all(
                        isinstance(v, (int, float)) and math.isfinite(v) for v in actual):
                    raise RuntimeError(f'Invalid SDK Cartesian feedback: {actual!r}')
                angular_error = [abs((actual[i] - start[i] + 180.0) % 360.0 - 180.0)
                                 for i in (3, 4, 5)]
                if (abs(actual[0] - start[0]) > 25.0 or
                        abs(actual[1] - start[1]) > 25.0 or max(angular_error) > 12.0):
                    raise RuntimeError(f'Linear descent drifted from XY/orientation: {actual!r}')
                z_tolerance_mm = min(10.0, max(2.0, abs(distance_mm) * .5))
                reached = (abs(actual[0] - target[0]) <= 12.0 and
                           abs(actual[1] - target[1]) <= 12.0 and
                           abs(actual[2] - target[2]) <= z_tolerance_mm and
                           max(angular_error) <= 5.0)
                if reached:
                    if last is None or max(abs(actual[i] - last[i]) for i in range(3)) > 1.0:
                        stable_since = time.monotonic()
                    last = actual
                    if time.monotonic() - stable_since >= 0.5:
                        response.success = True
                        response.message = f'SDK MoveL {distance_mm:g} mm completed; actual={actual}'
                        self.get_logger().info(response.message)
                        return response
                else:
                    stable_since = None
                    last = None
                if time.monotonic() > deadline:
                    raise RuntimeError(f'SDK MoveL did not reach target in 45 s; last={actual!r}')
                time.sleep(0.25)
            raise RuntimeError('ROS shutdown during SDK MoveL')
        except Exception as exc:
            if sent:
                try:
                    self.mc.stop()
                except Exception as stop_exc:
                    self.get_logger().error(f'SDK stop after MoveL failure failed: {stop_exc}')
            response.message = str(exc)
            self.get_logger().error(f'Linear descent failed: {exc}')
            return response
        finally:
            with self.guard:
                self.descend_active = False
                self.busy = False
            self.descend_cancel.clear()

    def set_handeye_free_mode(self, request, response):
        desired = bool(request.data)
        with self.guard:
            if self.busy:
                response.message = 'SDK action is active; wait until it finishes'
                return response
            self.busy = True
        try:
            if desired:
                # Match the robot's known working manual-release command.
                self.mc.release_all_servos()
            else:
                self.mc.set_free_mode(0)
                self.mc.focus_all_servos()
            # The SDK mode flag and servo power are separate on this controller.
            # Verify actual servo power; report the mode flag for diagnosis only.
            time.sleep(1.0)
            deadline = time.monotonic() + 2.0
            while True:
                servos = self.mc.is_all_servo_enable()
                if servos == (0 if desired else 1):
                    break
                if time.monotonic() >= deadline:
                    actual = self.mc.is_free_mode()
                    raise RuntimeError(
                        f'Servo verification failed: free_mode={actual!r}, servos_enabled={servos!r}')
                time.sleep(0.1)
            self.manual_mode = desired
            response.success = True
            response.message = 'manual/free mode' if desired else 'automatic/servo mode'
            self.get_logger().warning(f'Hand-eye capture switched to {response.message}; no joint target sent')
        except Exception as exc:
            # Unknown torque state must not admit an arm or gripper goal.
            self.manual_mode = True
            response.message = str(exc)
            self.get_logger().error(f'Hand-eye mode change failed: {exc}')
        finally:
            with self.guard:
                self.busy = False
        return response

    def goal(self, request):
        if self.manual_mode:
            self.get_logger().warning('Arm goal rejected while hand-eye manual mode is active')
            return GoalResponse.REJECT
        return super().goal(request)

    def slow_goal(self, request):
        # The drag-hybrid subclass validates its drag path with sparse SDK
        # waypoints. This action sends one slow endpoint, so use endpoint
        # validation instead of applying the drag waypoint-step limit.
        with self.guard:
            if (self.manual_mode or not self.motion_enabled or self.busy or
                    self.fresh_mode != self.required_fresh_mode):
                self.get_logger().warning('Slow joint goal rejected: motion disabled, busy, manual mode, or SDK mode differs')
                return GoalResponse.REJECT
            try:
                SDKGoalBridge.validated(self, request)
            except Exception as exc:
                self.get_logger().error(f'Slow joint goal rejected without motion: {exc}')
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    def gripper_target(self, request):
        trajectory = request.trajectory
        if trajectory.header.stamp.sec or trajectory.header.stamp.nanosec:
            raise ValueError('Only immediate gripper trajectories are supported')
        if list(trajectory.joint_names) != [self.gripper_name]:
            raise ValueError('Gripper trajectory must contain only gripper_controller')
        if not trajectory.points:
            raise ValueError('Gripper trajectory has no points')
        value = trajectory.points[-1].positions
        if len(value) != 1 or not math.isfinite(value[0]):
            raise ValueError('Gripper target must contain one finite position')
        target = float(value[0])
        if not -0.83001 <= target <= 0.53501:
            raise ValueError('Gripper target is outside the configured open/closed range')
        return target

    def gripper_goal(self, request):
        with self.guard:
            if not self.motion_enabled or self.busy or self.manual_mode or self.fresh_mode != self.required_fresh_mode:
                self.get_logger().warning('Gripper goal rejected: motion disabled, busy, manual mode, or SDK mode mismatch')
                return GoalResponse.REJECT
            try:
                self.gripper_target(request)
            except Exception as exc:
                self.get_logger().error(f'Gripper goal rejected without motion: {exc}')
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    @staticmethod
    def normalize_gripper_value(raw):
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values = [float(raw)]
        elif isinstance(raw, (list, tuple)):
            values = [float(value) for value in raw
                      if isinstance(value, (int, float)) and not isinstance(value, bool)]
        else:
            return None
        values = [value for value in values if math.isfinite(value) and 0 <= value <= 100]
        return sum(values) / len(values) if values else None

    def execute_gripper(self, handle):
        result = FollowJointTrajectory.Result()
        sent = False
        try:
            target = self.gripper_target(handle.request)
            # The URDF/SRDF endpoints are illustrative but map explicitly to
            # the known SDK parallel-gripper range: 0=closed, 100=open.
            sdk_value = round(100 * (target + 0.83) / (0.535 + 0.83))
            sdk_value = min(100, max(0, sdk_value))
            self.get_logger().warning(
                f'GRIPPER COMMAND: firmware-default setter, target={sdk_value}/100; '
                'MoveIt gripper trajectory duration is not replayed.')
            if handle.is_cancel_requested:
                handle.canceled()
                result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                result.error_string = 'Canceled before gripper command'
                return result
            sent = True
            # Match the user's known-working JetCobot example exactly:
            # set_gripper_value(value, speed), leaving type selection to firmware.
            self.mc.set_gripper_value(sdk_value, 50)
            deadline = time.monotonic() + 12.0
            stable_since = None
            last_value = None
            stopped_samples = 0
            while rclpy.ok():
                if handle.is_cancel_requested:
                    try:
                        self.mc.gripper_stop()
                    except Exception as exc:
                        self.get_logger().error(f'Gripper stop failed: {exc}')
                    handle.canceled()
                    result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                    result.error_string = 'Gripper canceled; stop requested'
                    return result
                measured = self.normalize_gripper_value(self.read_gripper_value())
                if measured is not None:
                    self.gripper_sample = measured
                    self.gripper_time = time.monotonic()
                    self.gripper.publish(Int32(data=round(measured)))
                else:
                    self.gripper_sample = None
                    self.gripper.publish(Int32(data=-1))
                moving = self.mc.is_gripper_moving()
                # A grasp can stop before the empty-gripper endpoint (0/100).
                # Accept a close command only after a valid, sufficiently closed
                # reading stays stable; firmware may keep reporting moving=1 at contact.
                close_contact = sdk_value == 0 and measured is not None and measured <= 50
                open_reached = sdk_value != 0 and measured is not None and abs(measured - sdk_value) <= 5
                if close_contact or open_reached:
                    if stable_since is None or last_value is None or abs(measured - last_value) > 2:
                        stable_since = time.monotonic()
                    last_value = measured
                    stopped_samples = stopped_samples + 1 if moving == 0 else 0
                    close_confirmed = close_contact and time.monotonic() - stable_since >= 1.5
                    open_confirmed = open_reached and time.monotonic() - stable_since >= 0.75 and stopped_samples >= 2
                    if close_confirmed or open_confirmed:
                        if close_confirmed and moving != 0:
                            self.mc.gripper_stop()
                        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                        result.error_string = (f'Gripper stable at SDK value {measured:.0f}/100 '
                                               f'(target {sdk_value}/100; moving={moving!r})')
                        handle.succeed()
                        self.get_logger().info(result.error_string)
                        return result
                else:
                    stable_since = None
                    last_value = measured
                    stopped_samples = 0
                if time.monotonic() > deadline:
                    raise RuntimeError(f'Gripper did not confirm target {sdk_value}/100; '
                                       f'last reading={measured!r}, moving={moving!r}')
                time.sleep(0.25)
            raise RuntimeError('ROS shutdown during gripper command')
        except Exception as exc:
            if sent:
                try:
                    self.mc.gripper_stop()
                except Exception as stop_exc:
                    self.get_logger().error(f'Gripper stop failed: {stop_exc}')
            result.error_code = FollowJointTrajectory.Result.GOAL_TOLERANCE_VIOLATED
            result.error_string = str(exc)
            if handle.is_active:
                handle.abort()
            self.get_logger().error(str(exc))
            return result
        finally:
            with self.guard:
                self.busy = False

    def validated(self, request):
        points=super().validated(request)
        sdk_goal_target(points)
        return points

    def execute(self, handle):
        # 2026-11-24: was a fixed 10; endpoint moves were the slow part of every cycle.
        return self._execute_sdk_goal(handle, self.endpoint_sdk_speed)

    def execute_slow_goal(self, handle):
        self.slow_joint_active = True
        self.slow_joint_cancel.clear()
        try:
            return self._execute_sdk_goal(handle, self.slow_joint_speed, slow=True)
        finally:
            self.slow_joint_active = False
            self.slow_joint_cancel.clear()

    def _execute_sdk_goal(self, handle, speed, slow=False):
        result=FollowJointTrajectory.Result()
        sent=False
        try:
            points=SDKGoalBridge.validated(self, handle.request)
            target=sdk_goal_target(points)
            initial=self.current()
            active=[i for i,(a,b) in enumerate(zip(initial,target)) if abs(a-b)>math.radians(.2)]
            self.get_logger().warning(
                f'SDK GOAL MOVE speed={speed}: native SDK chooses motion; MoveIt preview path/time are NOT executed.')
            if handle.is_cancel_requested or (slow and self.slow_joint_cancel.is_set()):
                handle.canceled()
                result.error_code=0
                result.error_string='Canceled before motion command'
                return result
            if active:
                sent=True
                if len(active)==1:
                    i=active[0]
                    self.mc.send_angle(i+1,round(math.degrees(target[i]),2),speed,_async=True)
                else:
                    self.mc.send_angles([round(math.degrees(q),2) for q in target],speed,_async=True)
            sent_time=time.monotonic()
            deadline=sent_time+(90.0 if slow else max(20.,min(120.,points[-1][0]*3+10.)))
            stable_since=None
            stable_reference=None
            while rclpy.ok():
                if handle.is_cancel_requested or (slow and self.slow_joint_cancel.is_set()):
                    if sent:self.stop_arm()
                    handle.canceled()
                    result.error_code=0
                    result.error_string='SDK goal canceled; stop requested'
                    return result
                sample=self.motion_sample()
                if sample is None:
                    time.sleep(.1)
                    continue
                stamp,actual=sample
                if any(abs(a-b)>math.radians(self.max_move_deg+HARDWARE_LIMIT_MARGIN_DEG)
                       for a,b in zip(actual,initial)):
                    raise RuntimeError('Actual position left SDK goal movement envelope')
                for value,(low,high) in zip(actual,LIMITS_DEG):
                    if not math.radians(low-HARDWARE_LIMIT_MARGIN_DEG)<=value<=math.radians(high+HARDWARE_LIMIT_MARGIN_DEG):
                        raise RuntimeError('Actual feedback exceeds hardware limits')
                within=all(abs(a-b)<=self.goal_tolerance for a,b in zip(actual,target))
                if stamp>=sent_time and within:
                    if stable_since is None or any(abs(a-b)>math.radians(.2)
                                                   for a,b in zip(actual,stable_reference)):
                        stable_since=stamp
                        stable_reference=actual
                    if stamp-stable_since>=self.endpoint_settle_s:
                        errors=[round(math.degrees(a-b),3) for a,b in zip(actual,target)]
                        result.error_code=0
                        result.error_string=(f'SDK GOAL reached within {self.goal_tolerance_deg:g}deg; '
                                             f'errors_deg={errors}; SDK commands={int(sent)}; NOT path-tracking success')
                        self.get_logger().info(result.error_string)
                        handle.succeed()
                        return result
                else:
                    stable_since=None
                    stable_reference=None
                if time.monotonic()>deadline:
                    raise RuntimeError('SDK goal actual position did not settle in time')
                feedback=FollowJointTrajectory.Feedback()
                feedback.header.stamp=self.get_clock().now().to_msg()
                feedback.joint_names=list(self.arm_names)
                feedback.desired=JointTrajectoryPoint(positions=list(target))
                feedback.actual=JointTrajectoryPoint(positions=list(actual))
                feedback.error=JointTrajectoryPoint(positions=[b-a for a,b in zip(actual,target)])
                handle.publish_feedback(feedback)
                time.sleep(.1)
            raise RuntimeError('ROS shutdown during SDK goal move')
        except Exception as exc:
            if sent:self.stop_arm()
            result.error_code=FollowJointTrajectory.Result.GOAL_TOLERANCE_VIOLATED
            result.error_string=str(exc)
            if handle.is_active:handle.abort()
            self.get_logger().error(str(exc))
            return result
        finally:
            with self.guard:self.busy=False


def main():
    rclpy.init();node=None;executor=MultiThreadedExecutor(num_threads=3)
    try:
        node=SDKGoalBridge();executor.add_node(node);executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            if node.busy:node.stop_arm()
            executor.shutdown(timeout_sec=3)
            if node.mode_lease is not None:
                try:
                    node.mode_lease.restore()
                    node.get_logger().info('Original SDK motion mode restored and verified')
                except Exception as exc:
                    node.get_logger().error(f'KEEP sdk_mode_restore.json for recovery: {exc}')
            node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
