#!/usr/bin/env python3
"""Isolated SDK mode-1 trial with an ahead target on a MoveIt joint path."""
from pathlib import Path
import math
import time

import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.executors import MultiThreadedExecutor
from trajectory_msgs.msg import JointTrajectoryPoint

from hardware_bridge import HardwareBridge
from mode_lease import ModeLease
from path_lookahead import path_geometry, position_at, project_forward
from trajectory_waypoints import effective_goal_tolerance_deg


class SDKRefreshTrialBridge(HardwareBridge):
    def __init__(self):
        super().__init__(
            node_name='jetcobot_sdk_refresh_trial_bridge',
            action_name='/{side}/sdk_refresh_trial_controller/follow_joint_trajectory',
            execution_label='MODE 1 TRIAL: geometric lookahead on MoveIt path')
        self.declare_parameter('native_refresh_trial', False)
        self.declare_parameter('timed_stream_trial', False)
        self.declare_parameter('refresh_interval_s', 0.6)
        self.declare_parameter('lookahead_deg', 6.0)
        self.declare_parameter('path_corridor_deg', 8.0)
        self.declare_parameter('refresh_sdk_speed', 10)
        self.refresh_interval = float(self.get_parameter('refresh_interval_s').value)
        self.lookahead = math.radians(float(self.get_parameter('lookahead_deg').value))
        self.path_corridor = math.radians(float(self.get_parameter('path_corridor_deg').value))
        self.sdk_speed = int(self.get_parameter('refresh_sdk_speed').value)
        self.timed_stream_trial = bool(self.get_parameter('timed_stream_trial').value)
        if not 0.4 <= self.refresh_interval <= 2.0:
            raise ValueError('refresh_interval_s must be in [0.4, 2.0]')
        if not math.radians(2) <= self.lookahead <= math.radians(15):
            raise ValueError('lookahead_deg must be in [2, 15]')
        if not math.radians(2) <= self.path_corridor <= math.radians(10):
            raise ValueError('path_corridor_deg must be in [2, 10]')
        if not 1 <= self.sdk_speed <= 30:
            raise ValueError('refresh_sdk_speed must be in [1, 30]')
        self.mode_lease = None
        try:
            if not self.motion_enabled or not bool(self.get_parameter('native_refresh_trial').value):
                raise ValueError('Trial requires enable_motion:=true and native_refresh_trial:=true')
            lease = ModeLease(self.mc, Path(__file__).resolve().parent/'sdk_mode_restore.json')
            self.fresh_mode = lease.acquire(1)
            self.mode_lease = lease
            if self.timed_stream_trial and not hasattr(self.mc, 'send_timed_angles'):
                raise RuntimeError('timed_stream_trial needs the MyCobot280 4.0.7 SDK patch')
            self.get_logger().warning(
                f'SDK REFRESH TRIAL: mode 1, interval={self.refresh_interval:g}s, '
                f'lookahead={math.degrees(self.lookahead):g}deg, '
                f'corridor={math.degrees(self.path_corridor):g}deg, speed={self.sdk_speed}; '
                f'timed_stream_trial={self.timed_stream_trial}; '
                'one arm in clear space only. Original mode recorded for restoration.')
        except BaseException:
            if self.mode_lease is not None:
                self.mode_lease.restore()
            self.destroy_node()
            raise

    def goal_tolerance_for(self, initial, target):
        # A 5-degree tolerance must not confirm a <=5-degree trial before motion.
        return math.radians(effective_goal_tolerance_deg(
            initial, target, self.goal_tolerance_deg))

    def execute(self, handle):
        if self.timed_stream_trial:
            return self.execute_timed_stream(handle)
        result = FollowJointTrajectory.Result()
        sent = False
        try:
            points = self.validated(handle.request)
            initial = self.current()
            positions, cumulative = path_geometry(points)
            tolerance = self.goal_tolerance_for(initial, positions[-1])
            progress = 0.0
            last_command_time = -math.inf
            last_command = initial
            last_progress_time = time.monotonic()
            start = last_progress_time
            deadline = start + max(30.0, points[-1][0]*3.0+10.0)
            final_sent = False
            final_fresh_count = 0
            last_sample_stamp = -math.inf
            commands = 0
            self.get_logger().warning(
                f'REFRESH START: {len(points)} planned points, path length '
                f'{math.degrees(cumulative[-1]):.1f} joint-space deg; '
                'planned timing is not replayed')
            while rclpy.ok():
                if handle.is_cancel_requested:
                    if sent:
                        self.stop_arm()
                    handle.canceled()
                    result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                    result.error_string = 'Canceled; SDK stop requested'
                    return result
                now = time.monotonic()
                stamp, actual = self.current_sample()
                new_progress, deviation = project_forward(positions, cumulative, actual, progress)
                if deviation > self.path_corridor:
                    raise RuntimeError(
                        f'Actual arm left planned joint corridor: {math.degrees(deviation):.2f}deg '
                        f'> {math.degrees(self.path_corridor):.2f}deg')
                if new_progress > progress+math.radians(0.2):
                    last_progress_time = now
                progress = new_progress
                target = position_at(positions, cumulative, progress+self.lookahead)
                at_final_target = progress+self.lookahead >= cumulative[-1]-1e-9
                if now-last_command_time >= self.refresh_interval:
                    change = max(abs(a-b) for a, b in zip(target, last_command))
                    if (not final_sent and (change >= math.radians(0.5) or at_final_target)):
                        self.mc.send_angles([round(math.degrees(q), 2) for q in target],
                                            self.sdk_speed, _async=True)
                        sent = True
                        commands += 1
                        last_command = target
                        last_command_time = time.monotonic()
                        if at_final_target:
                            final_sent = True
                        self.get_logger().info(
                            f'REFRESH SEND {commands}: progress={math.degrees(progress):.1f}/'
                            f'{math.degrees(cumulative[-1]):.1f}deg, '
                            f'corridor={math.degrees(deviation):.2f}deg, '
                            f'final={at_final_target}')
                if final_sent and stamp > last_command_time and stamp != last_sample_stamp:
                    if max(abs(a-b) for a, b in zip(actual, positions[-1])) <= tolerance:
                        final_fresh_count += 1
                    else:
                        final_fresh_count = 0
                    if final_fresh_count >= 2:
                        handle.succeed()
                        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                        result.error_string = (
                            f'Final measured within {math.degrees(tolerance):.2f}deg; '
                            f'commands={commands}')
                        self.get_logger().info(result.error_string)
                        return result
                last_sample_stamp = stamp
                if now >= deadline:
                    raise RuntimeError('Timed out before measured final goal')
                if sent and now-last_progress_time > 10.0 and not final_sent:
                    raise RuntimeError('No measured path progress for 10 seconds')
                feedback = FollowJointTrajectory.Feedback()
                feedback.header.stamp = self.get_clock().now().to_msg()
                feedback.joint_names = list(self.arm_names)
                feedback.desired = JointTrajectoryPoint(positions=list(target))
                feedback.actual = JointTrajectoryPoint(positions=list(actual))
                feedback.error = JointTrajectoryPoint(
                    positions=[d-a for d, a in zip(target, actual)])
                handle.publish_feedback(feedback)
                time.sleep(0.05)
            raise RuntimeError('ROS shutdown during trajectory')
        except Exception as exc:
            if sent:
                self.stop_arm()
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc)
            if handle.is_active:
                handle.abort()
            self.get_logger().error(f'Refresh trajectory failed: {exc}')
            return result
        finally:
            with self.guard:
                self.busy = False

    def execute_timed_stream(self, handle):
        """Trial of timed SDK streaming; physical smoothing remains unverified."""
        result = FollowJointTrajectory.Result()
        sent = False
        last_send_time = -math.inf
        try:
            points = self.validated(handle.request)
            initial = self.current()
            positions, cumulative = path_geometry(points)
            tolerance = self.goal_tolerance_for(initial, positions[-1])
            progress = 0.0
            self.get_logger().warning(
                f'TIMED STREAM START: {len(points)} planned points, '
                f'duration={points[-1][0]:.2f}s; 2.0s target lead, '
                '6-8 joint-space deg measured lead; '
                'each SDK command still uses send_angles')

            def check(target_deg, elapsed):
                nonlocal progress
                _, actual = self.current_sample()
                progress, deviation = project_forward(positions, cumulative, actual, progress)
                if deviation > self.path_corridor:
                    raise RuntimeError(
                        f'Actual arm left planned corridor: {math.degrees(deviation):.2f}deg')
                target = tuple(math.radians(value) for value in target_deg)
                target_progress, _ = project_forward(
                    positions, cumulative, target, progress)
                target_progress = min(cumulative[-1], max(
                    progress+math.radians(6.0),
                    min(target_progress, progress+math.radians(8.0))))
                target = position_at(positions, cumulative, target_progress)
                if max(abs(a-b) for a, b in zip(actual, target)) > math.radians(10.0):
                    raise RuntimeError('Timed target more than 10 degrees from actual joint state')
                feedback = FollowJointTrajectory.Feedback()
                feedback.header.stamp = self.get_clock().now().to_msg()
                feedback.joint_names = list(self.arm_names)
                feedback.desired = JointTrajectoryPoint(positions=list(target))
                feedback.actual = JointTrajectoryPoint(positions=list(actual))
                feedback.error = JointTrajectoryPoint(
                    positions=[d-a for d, a in zip(target, actual)])
                handle.publish_feedback(feedback)
                return tuple(math.degrees(value) for value in target)

            def on_send(target, elapsed, count, final):
                nonlocal sent, last_send_time
                sent = True
                last_send_time = time.monotonic()
                self.get_logger().info(
                    f'TIMED SEND {count}: elapsed={elapsed:.2f}s, '
                    f'progress={math.degrees(progress):.1f}/'
                    f'{math.degrees(cumulative[-1]):.1f}deg, final={final}')

            sdk_points = [(stamp, tuple(math.degrees(q) for q in joints))
                          for stamp, joints in points]
            commands = self.mc.send_timed_angles(
                sdk_points, self.sdk_speed, period_s=self.refresh_interval,
                lead_s=2.0,
                should_stop=lambda: handle.is_cancel_requested or not rclpy.ok(),
                check=check, on_send=on_send)
            fresh_count = 0
            last_sample_stamp = -math.inf
            deadline = time.monotonic()+10.0
            while rclpy.ok() and time.monotonic() < deadline:
                if handle.is_cancel_requested:
                    raise InterruptedError('timed trajectory canceled')
                stamp, actual = self.current_sample()
                progress, deviation = project_forward(positions, cumulative, actual, progress)
                if deviation > self.path_corridor:
                    raise RuntimeError('Actual arm left planned corridor after final send')
                if stamp > last_send_time and stamp != last_sample_stamp:
                    fresh_count = (fresh_count+1 if
                                   max(abs(a-b) for a, b in zip(actual, positions[-1])) <= tolerance
                                   else 0)
                    if fresh_count >= 2:
                        handle.succeed()
                        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                        result.error_string = f'Timed stream reached measured final goal; commands={commands}'
                        self.get_logger().info(result.error_string)
                        return result
                last_sample_stamp = stamp
                time.sleep(0.05)
            raise RuntimeError('Timed stream final goal not measured within 10 seconds')
        except InterruptedError as exc:
            if sent:
                self.stop_arm()
            handle.canceled()
            result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
            result.error_string = f'{exc}; SDK stop requested'
            return result
        except Exception as exc:
            if sent:
                self.stop_arm()
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc)
            if handle.is_active:
                handle.abort()
            self.get_logger().error(f'Timed stream failed: {exc}')
            return result
        finally:
            with self.guard:
                self.busy = False


def main():
    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = SDKRefreshTrialBridge()
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
                    node.get_logger().info('Original SDK motion mode restored')
                except Exception as exc:
                    node.get_logger().error(f'KEEP sdk_mode_restore.json for recovery: {exc}')
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
