#!/usr/bin/env python3
"""Experimental MoveIt path follower using one-shot SET_ENCODERS_DRAG packets.

MoveIt joint points are reduced geometrically, then sent ahead of measured
progress. Planned timing is advisory; firmware velocity behavior is uncalibrated.
"""
import math
import time

import rclpy
from control_msgs.action import FollowJointTrajectory
from pymycobot.common import ProtocolCode
from rclpy.executors import MultiThreadedExecutor
from trajectory_msgs.msg import JointTrajectoryPoint

from drag_trajectory import smooth_drag_command
from hardware_bridge import HardwareBridge
from path_lookahead import path_geometry, project_forward
from sdk_goal_bridge import SDKGoalBridge
from trajectory_guard import LIMITS_DEG
from trajectory_waypoints import (select_waypoints, effective_goal_tolerance_deg,
                                  round_joint_path)


class SDKDragBridge(SDKGoalBridge):
    def __init__(self):
        super().__init__(node_name='jetcobot_sdk_drag_bridge',
                         execution_label='DRAG PATH TRIAL: MoveIt joint path to encoder/speed packets',
                         action_name='/{side}/sdk_drag_controller/follow_joint_trajectory')
        self.declare_parameter('drag_step_deg', 5.0)
        self.declare_parameter('drag_deviation_deg', 1.5)
        self.declare_parameter('drag_corner_trim_deg', 3.0)
        self.declare_parameter('drag_speed', 200)
        self.declare_parameter('drag_wrist_speed_scale', 0.8)
        self.declare_parameter('drag_send_interval_s', 0.4)
        self.declare_parameter('drag_lead_deg', 10.0)
        self.declare_parameter('drag_corridor_deg', 6.0)
        self.step_deg = float(self.get_parameter('drag_step_deg').value)
        self.deviation_deg = float(self.get_parameter('drag_deviation_deg').value)
        self.corner_trim_deg = float(self.get_parameter('drag_corner_trim_deg').value)
        self.speed = int(self.get_parameter('drag_speed').value)
        self.wrist_speed_scale = float(self.get_parameter('drag_wrist_speed_scale').value)
        self.interval = float(self.get_parameter('drag_send_interval_s').value)
        self.lead_deg = float(self.get_parameter('drag_lead_deg').value)
        self.corridor_deg = float(self.get_parameter('drag_corridor_deg').value)
        if not (0 < self.deviation_deg < self.step_deg <= 8 and
                0 <= self.corner_trim_deg <= 3.0 and
                20 <= self.speed <= 200 and 0.3 <= self.wrist_speed_scale <= 1.0 and
                0.4 <= self.interval <= 2.0 and
                self.step_deg <= self.lead_deg <= 12 and 3 <= self.corridor_deg <= 10):
            raise ValueError('Invalid SDK drag path parameters (drag_speed must be 20..200)')
        if not self.motion_enabled or self.fresh_mode != 0 or self.required_fresh_mode != 0:
            raise ValueError('Drag path trial requires enable_motion:=true and native_interpolation:=true')
        self.get_logger().warning(
            f'DRAG PATH TRIAL: step={self.step_deg:g}deg, deviation={self.deviation_deg:g}deg, '
            f'corner trim<={self.corner_trim_deg:g}deg, '
            f'speed value={self.speed}, wrist speed scale={self.wrist_speed_scale:g}, '
            f'interval>={self.interval:g}s, measured lead<={self.lead_deg:g}deg; '
            'MoveIt timing is not replayed. Start with one arm and a clear, short plan.')

    def validated(self, request):
        points = HardwareBridge.validated(
            self, request, start_tolerance_rad=math.radians(5.0))
        self.requested_goal_tolerance(request)
        if len(points) < 2:
            raise ValueError('Drag path needs at least two planned points')
        chosen = round_joint_path(
            select_waypoints(points, self.step_deg, self.deviation_deg),
            self.corner_trim_deg)
        if len(chosen) > 61:
            raise ValueError('Drag path needs more than 60 commands; replan shorter')
        return points

    def requested_goal_tolerance(self, request):
        """Optional per-goal tighter tolerance for coordinated short sections."""
        rows = request.goal_tolerance
        if not rows:
            return None
        names = set(self.arm_names)
        if len(rows) != 6 or {row.name for row in rows} != names:
            raise ValueError('Coordinated goal tolerance requires all six arm joints')
        values = [row.position for row in rows]
        if not all(math.isfinite(value) and 0 < value <= math.radians(5)
                   for value in values):
            raise ValueError('Coordinated goal tolerance must be 0..5 deg')
        return min(values)

    def execute(self, handle):
        result = FollowJointTrajectory.Result()
        sent = False
        try:
            points = self.validated(handle.request)
            chosen = round_joint_path(
                select_waypoints(points, self.step_deg, self.deviation_deg),
                self.corner_trim_deg)
            initial = self.current()
            positions, cumulative = path_geometry(points)
            final = points[-1][1]
            tolerance = math.radians(effective_goal_tolerance_deg(
                initial, final, self.goal_tolerance_deg))
            requested = self.requested_goal_tolerance(handle.request)
            if requested is not None:
                tolerance = min(tolerance, requested)
            next_index = 1
            last_send = -math.inf
            progress = 0.0
            last_stamp = -math.inf
            last_actual = None
            last_progress = None
            stable = 0
            count = 0
            deadline = time.monotonic() + max(30.0, min(300.0, 5.0*(len(chosen)-1)))
            self.get_logger().warning(
                f'DRAG START: {len(points)} planned points -> {len(chosen)-1} SDK packets; '
                f'planned duration={points[-1][0]:.2f}s; SDK timing follows measured progress.')
            while rclpy.ok():
                if handle.is_cancel_requested:
                    if sent:
                        self.stop_arm()
                    handle.canceled()
                    result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                    result.error_string = 'Canceled; SDK stop requested'
                    return result
                sample = self.motion_sample()
                if sample is None:
                    time.sleep(0.05)
                    continue
                stamp, actual = sample
                if any(abs(a-b) > math.radians(self.max_move_deg+2)
                       for a, b in zip(actual, initial)):
                    raise RuntimeError('Measured arm left movement envelope')
                for q, (low, high) in zip(actual, LIMITS_DEG):
                    if not math.radians(low-2) <= q <= math.radians(high+2):
                        raise RuntimeError('Measured arm exceeded joint limit')
                progress, deviation = project_forward(positions, cumulative, actual, progress)
                if deviation > math.radians(self.corridor_deg):
                    raise RuntimeError(f'Measured arm left planned joint corridor: {math.degrees(deviation):.2f}deg')

                now = time.monotonic()
                fresh_sample = stamp > last_stamp
                if fresh_sample and last_actual is not None:
                    dt = stamp - last_stamp
                    if dt > 1e-4:
                        joint_speed = [round(math.degrees(q-p)/dt, 2)
                                       for p, q in zip(last_actual, actual)]
                        path_speed = (math.degrees(progress-last_progress)/dt
                                      if last_progress is not None else 0.0)
                        self.get_logger().info(
                            f'DRAG FEEDBACK dt={dt:.2f}s; '
                            f'path_speed={path_speed:.2f}deg/s; '
                            f'joint_speed_deg_s={joint_speed}; '
                            f'path_progress={math.degrees(progress):.1f}/'
                            f'{math.degrees(cumulative[-1]):.1f}deg; '
                            f'corridor_error={math.degrees(deviation):.2f}deg')
                if fresh_sample:
                    last_actual = actual
                    last_progress = progress
                    last_stamp = stamp
                if (next_index < len(chosen) and now-last_send >= self.interval and
                        max(abs(a-b) for a, b in zip(actual, chosen[next_index][1]))
                        <= math.radians(self.lead_deg)):
                    target = chosen[next_index][1]
                    previous = chosen[next_index-1][1]
                    following = chosen[min(next_index+1, len(chosen)-1)][1]
                    encoders, speeds = smooth_drag_command(
                        previous, target, following,
                        progress/cumulative[-1], self.speed)
                    speeds = tuple(
                        max(1, round(value*self.wrist_speed_scale))
                        if 3 <= joint <= 5 and value > 0 else value
                        for joint, value in enumerate(speeds))
                    self.mc._mesg(ProtocolCode.SET_ENCODERS_DRAG, list(encoders), list(speeds), _async=True)
                    sent = True
                    last_send = time.monotonic()
                    count += 1
                    self.get_logger().info(
                        f'DRAG SEND {count}/{len(chosen)-1}; target_deg='
                        f'{[round(math.degrees(q), 2) for q in target]}; speeds={speeds}; '
                        f'progress={math.degrees(progress):.1f}/{math.degrees(cumulative[-1]):.1f}deg')
                    next_index += 1

                desired = chosen[min(next_index-1, len(chosen)-1)][1]
                feedback = FollowJointTrajectory.Feedback()
                feedback.header.stamp = self.get_clock().now().to_msg()
                feedback.joint_names = list(self.arm_names)
                feedback.desired = JointTrajectoryPoint(positions=list(desired))
                feedback.actual = JointTrajectoryPoint(positions=list(actual))
                feedback.error = JointTrajectoryPoint(positions=[d-a for d, a in zip(desired, actual)])
                handle.publish_feedback(feedback)
                if next_index == len(chosen) and stamp > last_send and fresh_sample:
                    stable = stable+1 if max(abs(a-b) for a, b in zip(actual, final)) <= tolerance else 0
                    if stable >= 2:
                        handle.succeed()
                        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                        result.error_string = f'Drag path final pose measured; packets={count}'
                        self.get_logger().info(result.error_string)
                        return result
                if now > deadline:
                    raise RuntimeError('Drag path did not reach measured final goal in time')
                time.sleep(0.05)
            raise RuntimeError('ROS shutdown during drag path')
        except Exception as exc:
            if sent:
                self.stop_arm()
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc)
            if handle.is_active:
                handle.abort()
            self.get_logger().error(f'Drag path failed: {exc}')
            return result
        finally:
            with self.guard:
                self.busy = False


def main():
    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = SDKDragBridge()
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            if node.busy:
                node.stop_arm()
            executor.shutdown(timeout_sec=3)
            if node.mode_lease is not None:
                try:
                    node.mode_lease.restore()
                except Exception as exc:
                    node.get_logger().error(f'Mode restoration failed: {exc}')
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
