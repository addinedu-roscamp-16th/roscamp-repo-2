#!/usr/bin/env python3
"""Opt-in sparse SDK waypoint follower for a MoveIt joint trajectory.

Each retained waypoint is sent once and checked against measured joints.
Optional one-point lookahead queues the next command before arrival. This
approximates the planned joint path; it is not timed trajectory tracking or a
proof of collision-free continuous SDK motion. Never run beside another Pi
serial bridge for the same arm.
"""
import math
import time

import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import GoalResponse
from rclpy.executors import MultiThreadedExecutor
from trajectory_msgs.msg import JointTrajectoryPoint

from hardware_bridge import HardwareBridge
from sdk_goal_bridge import SDKGoalBridge
from trajectory_guard import LIMITS_DEG
from trajectory_waypoints import (select_waypoints, segment_deviation, segment_progress,
                                  effective_goal_tolerance_deg)


class SDKPathBridge(SDKGoalBridge):
    def __init__(self, action_name='/{side}/sdk_goal_controller/follow_joint_trajectory',
                 lookahead_default=False, step_default=4.0):
        super().__init__(node_name='jetcobot_sdk_path_bridge',
                         execution_label='SDK SPARSE WAYPOINT PATH: one command per selected waypoint',
                         action_name=action_name)
        self.queue_stop_unconfirmed = False
        try:
            self.declare_parameter('path_step_deg', step_default)
            self.declare_parameter('path_deviation_deg', 0.5)
            self.declare_parameter('waypoint_tolerance_deg', 2.0)
            self.declare_parameter('min_command_interval_s', 0.75)
            self.declare_parameter('path_sdk_speed', 10)
            self.declare_parameter('queue_lookahead', lookahead_default)
            self.declare_parameter('queue_lookahead_depth', 1)
            self.path_step_deg = float(self.get_parameter('path_step_deg').value)
            self.path_deviation_deg = float(self.get_parameter('path_deviation_deg').value)
            self.waypoint_tolerance_deg = float(self.get_parameter('waypoint_tolerance_deg').value)
            self.min_command_interval_s = float(self.get_parameter('min_command_interval_s').value)
            self.path_sdk_speed = int(self.get_parameter('path_sdk_speed').value)
            self.queue_lookahead = bool(self.get_parameter('queue_lookahead').value)
            self.queue_lookahead_depth = int(self.get_parameter('queue_lookahead_depth').value)
            if self.queue_lookahead_depth not in (1, 2):
                raise ValueError('queue_lookahead_depth must be 1 or 2')
            if self.queue_lookahead and self.queue_lookahead_depth == 2 and not callable(getattr(self.mc, 'clear_queue', None)):
                raise ValueError('Two-point lookahead requires SDK clear_queue support')
            if not 0.5 <= self.min_command_interval_s <= 3.0:
                raise ValueError('min_command_interval_s must be in [0.5,3]')
            if not 1 <= self.path_sdk_speed <= 100:
                raise ValueError('path_sdk_speed must be in [1,100]')
            if not 0 < self.waypoint_tolerance_deg <= 5.0:
                raise ValueError('waypoint_tolerance_deg must be in (0,5]')
            # select_waypoints validates spacing/deviation without touching hardware.
            select_waypoints([(0., (0.,)*6), (1., (0.,)*6)],
                             self.path_step_deg, self.path_deviation_deg)
        except Exception:
            if self.mode_lease is not None:
                self.mode_lease.restore()
            self.destroy_node()
            raise
        self.get_logger().warning(
            f'PATH MODE: step <= {self.path_step_deg:g} deg, geometric deviation '
            f'<= {self.path_deviation_deg:g} deg, one SDK command per waypoint. '
            f'lookahead depth={self.queue_lookahead_depth if self.queue_lookahead else 0}; '
            f'SDK speed={self.path_sdk_speed}; '
            'Continuous path and no-vibration behavior require hardware verification.')

    def stop_arm(self):
        super().stop_arm()
        if self.queue_lookahead and self.queue_lookahead_depth == 2:
            try:
                result = self.mc.clear_queue()
                if result != 1:
                    self.queue_stop_unconfirmed = True
                    self.get_logger().error(
                        f'SDK queue clear unconfirmed after stop (reply={result!r}); '
                        'rejecting new arm goals until bridge restart and robot inspection')
                else:
                    self.get_logger().warning('SDK queued commands cleared after stop request; reply=1')
            except Exception as exc:
                self.queue_stop_unconfirmed = True
                self.get_logger().error(
                    f'SDK queue clearing failed after stop: {exc}; '
                    'rejecting new arm goals until bridge restart and robot inspection')

    def goal(self, request):
        if getattr(self, 'queue_stop_unconfirmed', False):
            self.get_logger().error('Path goal rejected: previous SDK queue clear was unconfirmed')
            return GoalResponse.REJECT
        if self.fresh_mode != 0 or self.required_fresh_mode != 0:
            self.get_logger().error('Path mode requires native_interpolation:=true (SDK mode 0)')
            return GoalResponse.REJECT
        return super().goal(request)

    def validated(self, request):
        # Bypass sdk_goal_target: a waypoint follower must accept curved paths.
        points = HardwareBridge.validated(self, request)
        if len(points) < 2:
            raise ValueError('Path mode requires at least two trajectory points')
        selected = select_waypoints(points, self.path_step_deg, self.path_deviation_deg)
        if len(selected) > 61:
            raise ValueError('Path needs more than 60 SDK waypoint commands; replan shorter')
        return points

    def execute(self, handle):
        result = FollowJointTrajectory.Result()
        sent = False
        try:
            points = self.validated(handle.request)
            selected = select_waypoints(points, self.path_step_deg, self.path_deviation_deg)
            initial = self.current()
            joint_span_deg = math.degrees(max(abs(a-b) for a, b in zip(initial, selected[-1][1])))
            path_travel_deg = math.degrees(sum(
                max(abs(a-b) for a, b in zip(q0, q1))
                for (_, q0), (_, q1) in zip(points, points[1:])))
            final_tolerance_deg = effective_goal_tolerance_deg(
                initial, selected[-1][1], self.goal_tolerance_deg)
            count = 0
            last_send_time = None
            sent_at = {}
            depth = getattr(self, 'queue_lookahead_depth', 1) if self.queue_lookahead else 0
            total = len(selected)-1
            self.get_logger().warning(
                f'PATH START: {len(points)} planned points -> {total} SDK commands; '
                f'planned duration={points[-1][0]:.1f}s; joint span={joint_span_deg:.2f}deg, '
                f'joint travel={path_travel_deg:.2f}deg, final tolerance={final_tolerance_deg:.2f}deg; '
                f'lookahead depth={depth}; SDK speed={getattr(self, "path_sdk_speed", 10)}; '
                f'step={self.path_step_deg:g}deg; geometric deviation={self.path_deviation_deg:g}deg; '
                f'waypoint tolerance={self.waypoint_tolerance_deg:g}deg; '
                f'min send interval={self.min_command_interval_s:g}s. Timing is not replayed.')
            for index, ((t0, previous), (t1, target)) in enumerate(zip(selected, selected[1:]), 1):
                if handle.is_cancel_requested:
                    if sent:
                        self.stop_arm()
                    handle.canceled()
                    result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                    result.error_string = 'Canceled; SDK stop requested'
                    return result
                # SDK mode 0 processes commands in FIFO order. Keep at most the
                # current target plus the configured number of future targets
                # sent. Depth 1 retains the previously tested behavior.
                for send_index in range(index, min(total, index+depth)+1):
                    if send_index in sent_at:
                        continue
                    while last_send_time is not None and time.monotonic()-last_send_time < self.min_command_interval_s:
                        if handle.is_cancel_requested:
                            self.stop_arm()
                            handle.canceled()
                            result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                            result.error_string = 'Canceled before queued SDK waypoint'
                            return result
                        self.current_sample()
                        time.sleep(0.05)
                    previous_send_time = last_send_time
                    send_time = time.monotonic()
                    sent = True
                    self.mc.send_angles([round(math.degrees(q), 2) for q in selected[send_index][1]],
                                        getattr(self, 'path_sdk_speed', 10), _async=True)
                    sent_at[send_index] = send_time
                    last_send_time = send_time
                    count += 1
                    gap = 'first command' if previous_send_time is None else f'gap={send_time-previous_send_time:.2f}s'
                    target_deg = [round(math.degrees(q), 2) for q in selected[send_index][1]]
                    self.get_logger().info(
                        f'PATH SEND {send_index}/{total}; queued {send_index-index} ahead; {gap}; '
                        f'planned_t={selected[send_index][0]:.2f}s; target_deg={target_deg}')
                command_time = sent_at[index]
                next_target = selected[index+1][1] if depth >= 1 and index < total else None
                after_target = selected[index+2][1] if depth >= 2 and index+1 < total else None
                deadline = max(command_time + max(8.0, min(25.0, 4.0*(t1-t0)+5.0)),
                               time.monotonic()+8.0)
                tolerance = math.radians(final_tolerance_deg if index == len(selected)-1
                                         else self.waypoint_tolerance_deg)
                consecutive = 0
                last_stamp = -math.inf
                best_error_deg = math.inf
                next_fraction = None
                next_deviation_deg = None
                after_fraction = None
                after_deviation_deg = None
                while rclpy.ok():
                    if handle.is_cancel_requested:
                        self.stop_arm()
                        handle.canceled()
                        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
                        result.error_string = 'Canceled; SDK stop requested'
                        return result
                    stamp, actual = self.current_sample()
                    if any(abs(a-b) > math.radians(self.max_move_deg+2.0)
                           for a, b in zip(actual, initial)):
                        raise RuntimeError('Actual joint left planned movement envelope')
                    for q, (low, high) in zip(actual, LIMITS_DEG):
                        if not math.radians(low-2) <= q <= math.radians(high+2):
                            raise RuntimeError('Actual joint exceeded hardware range')
                    current_deviation = segment_deviation(actual, previous, target)
                    if next_target is not None:
                        next_fraction, next_deviation = segment_progress(actual, target, next_target)
                        next_deviation_deg = math.degrees(next_deviation)
                        path_deviation = min(current_deviation, next_deviation)
                    else:
                        path_deviation = current_deviation
                    if after_target is not None:
                        after_fraction, after_deviation = segment_progress(actual, next_target, after_target)
                        after_deviation_deg = math.degrees(after_deviation)
                        path_deviation = min(path_deviation, after_deviation)
                    if path_deviation > math.radians(6.0):
                        raise RuntimeError(f'Joint left planned waypoint corridor at command {index}; deviation={math.degrees(path_deviation):.2f}deg')
                    error_deg = math.degrees(max(abs(a-b) for a, b in zip(actual, target)))
                    within = error_deg <= math.degrees(tolerance)
                    # A queued next command may blend through this waypoint
                    # without settling on it. Advance only after fresh measured
                    # progress into the next planned segment, inside the same
                    # intermediate joint-error tolerance.
                    passed_on_path = (next_target is not None and
                                      stamp >= sent_at[index+1] and
                                      ((next_fraction >= 0.20 and next_deviation <= tolerance) or
                                       (after_target is not None and stamp >= sent_at[index+2] and
                                        after_fraction >= 0.20 and after_deviation <= tolerance)))
                    if stamp >= command_time:
                        best_error_deg = min(best_error_deg, error_deg)
                    if stamp > last_stamp:
                        consecutive = consecutive+1 if stamp >= command_time and (within or passed_on_path) else 0
                        last_stamp = stamp
                        self.get_logger().info(
                            f'PATH SAMPLE {index}/{total}; actual_deg='
                            f'{[round(math.degrees(q), 2) for q in actual]}; '
                            f'target_error={error_deg:.2f}deg; '
                            f'corridor_error={math.degrees(path_deviation):.2f}deg; '
                            f'next_progress={next_fraction}; after_progress={after_fraction}; '
                            f'feedback_age={time.monotonic()-stamp:.2f}s')
                    feedback = FollowJointTrajectory.Feedback()
                    feedback.header.stamp = self.get_clock().now().to_msg()
                    feedback.joint_names = list(self.arm_names)
                    feedback.desired = JointTrajectoryPoint(positions=list(target))
                    feedback.actual = JointTrajectoryPoint(positions=list(actual))
                    feedback.error = JointTrajectoryPoint(positions=[d-a for d, a in zip(target, actual)])
                    handle.publish_feedback(feedback)
                    # Advance on one fresh intermediate reading so SDK motion can
                    # blend toward the next target; require two at the final goal.
                    needed = 2 if index == len(selected)-1 else 1
                    if consecutive >= needed:
                        self.get_logger().info(f'PATH waypoint {index}/{len(selected)-1} '
                                               f'{"passed on next segment" if passed_on_path and not within else "reached"}; '
                                               f'target error={error_deg:.2f}deg, '
                                               f'next progress={next_fraction if next_fraction is not None else 0.0:.2f}, '
                                               f'next deviation={next_deviation_deg if next_deviation_deg is not None else 0.0:.2f}deg, '
                                               f'after progress={after_fraction if after_fraction is not None else 0.0:.2f}, '
                                               f'after deviation={after_deviation_deg if after_deviation_deg is not None else 0.0:.2f}deg')
                        break
                    if time.monotonic() > deadline:
                        errors = [round(math.degrees(a-b), 2) for a, b in zip(actual, target)]
                        raise RuntimeError(
                            f'Waypoint {index}/{len(selected)-1} did not reach/pass planned path; '
                            f'error_deg={errors}, max={error_deg:.2f}, best={best_error_deg:.2f}, '
                            f'tolerance={math.degrees(tolerance):.2f}, '
                            f'feedback_age={time.monotonic()-stamp:.2f}s, '
                            f'lookahead_depth={depth}, '
                            f'next_progress={next_fraction}, next_deviation_deg={next_deviation_deg}, '
                            f'after_progress={after_fraction}, after_deviation_deg={after_deviation_deg}')
                    time.sleep(0.1)
                else:
                    raise RuntimeError('ROS shutdown during SDK path')
            result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
            result.error_string = (f'SDK sparse path completed; {count} commands, measured waypoint progress. '
                                   'Continuous path/timing not verified.')
            handle.succeed()
            self.get_logger().info(result.error_string)
            return result
        except Exception as exc:
            if sent:
                self.stop_arm()
            result.error_code = FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string = str(exc)
            if handle.is_active:
                handle.abort()
            self.get_logger().error(f'SDK path failed: {exc}')
            return result
        finally:
            with self.guard:
                self.busy = False


def main():
    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = SDKPathBridge()
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
                    node.get_logger().info('Original SDK motion mode restored and verified')
                except Exception as exc:
                    node.get_logger().error(f'KEEP sdk_mode_restore.json for recovery: {exc}')
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
