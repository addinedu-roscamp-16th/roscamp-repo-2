#!/usr/bin/env python3
"""Opt-in small-motion arm trajectory bridge; no gripper movement or homing."""
import math
import threading
import time

import rclpy
from rclpy.action import ActionServer, GoalResponse, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint

from read_joint_states import ReadOnlyState
from trajectory_guard import ARM, validate, interpolate, CommandGate


class HardwareBridge(ReadOnlyState):
    def __init__(self, node_name='jetcobot_hardware_bridge',
                 action_name='/{side}/arm_group_controller/follow_joint_trajectory',
                 execution_label='SAMPLED PATH: <=4Hz, 0.5deg deadband; not a real-time servo driver'):
        super().__init__(node_name, default_state_hz=4.0)
        self.declare_parameter('enable_motion', False)
        self.declare_parameter('max_move_deg', 180.0)
        self.declare_parameter('goal_tolerance_deg', 5.0)
        self.max_move_deg = float(self.get_parameter('max_move_deg').value)
        self.goal_tolerance_deg = float(self.get_parameter('goal_tolerance_deg').value)
        if not math.isfinite(self.max_move_deg) or not 0 < self.max_move_deg <= 180:
            raise ValueError('max_move_deg must be in (0,180] degrees')
        if not math.isfinite(self.goal_tolerance_deg) or not 0 < self.goal_tolerance_deg <= 5:
            raise ValueError('goal_tolerance_deg must be in (0,5] degrees')
        self.goal_tolerance = math.radians(self.goal_tolerance_deg)
        # Extra seconds an ACTIVE motion waits through a joint-state gap
        # before failing. Goal acceptance stays strict (1.5 s). No new
        # command is sent while blind; an SDK endpoint continues to its
        # already-sent target, a drag waits at its last packet target.
        self.declare_parameter('feedback_grace_s', 0.0)
        self.feedback_grace_s = float(self.get_parameter('feedback_grace_s').value)
        if not 0.0 <= self.feedback_grace_s <= 3.0:
            raise ValueError('feedback_grace_s must be 0..3')
        self.motion_enabled = bool(self.get_parameter('enable_motion').value)
        # Read existing mode only. Never silently change persistent/SDK modes.
        self.fresh_mode = self.mc.get_fresh_mode()
        self.required_fresh_mode = 1
        self.busy = False
        self.guard = threading.Lock()
        self.server = ActionServer(
            self, FollowJointTrajectory,
            action_name.format(side=self.side),
            execute_callback=self.execute,
            goal_callback=self.goal,
            cancel_callback=lambda handle: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())
        self.get_logger().warning(
            f'ARM MOTION ENABLED={self.motion_enabled}; max +/-{self.max_move_deg:g}deg per joint per goal, '
            f'goal tolerance={self.goal_tolerance_deg:g}deg; '
            f'SDK speed=10, fresh_mode={self.fresh_mode}; {execution_label}; '
            'no startup motion or gripper commands.')

    def current_sample(self):
        sample = self.latest_arm
        if sample is None or time.monotonic()-sample[0] > 1.5:
            raise ValueError('Actual arm feedback missing or stale (>1.5 seconds)')
        return sample

    def current(self):
        return self.current_sample()[1]

    def motion_sample(self):
        """Fresh sample, None during a bounded gap, or ValueError."""
        sample = self.latest_arm
        age = None if sample is None else time.monotonic()-sample[0]
        if age is not None and age <= 1.5:
            return sample
        if age is not None and age <= 1.5+self.feedback_grace_s:
            return None
        raise ValueError(f'Actual arm feedback missing or stale '
                         f'(>{1.5+self.feedback_grace_s:g} seconds)')

    def validated(self, request, start_tolerance_rad=0.02):
        if request.trajectory.header.stamp.sec or request.trajectory.header.stamp.nanosec:
            raise ValueError('Only immediate (zero header stamp) trajectories are supported')
        raw=[(p.time_from_start.sec+p.time_from_start.nanosec/1e9,list(p.positions))
             for p in request.trajectory.points]
        return validate(list(request.trajectory.joint_names), raw, self.current(),
                        self.max_move_deg, arm_names=self.arm_names,
                        start_tolerance_rad=start_tolerance_rad)

    def goal(self, request):
        with self.guard:
            if not self.motion_enabled or self.busy or self.fresh_mode != self.required_fresh_mode:
                self.get_logger().warning(f'Goal rejected: motion disabled, busy, or fresh_mode is not {self.required_fresh_mode}')
                return GoalResponse.REJECT
            try:
                self.validated(request)
            except Exception as exc:
                self.get_logger().error(f'Goal rejected without motion: {exc}')
                return GoalResponse.REJECT
            self.busy=True
        return GoalResponse.ACCEPT

    def stop_arm(self):
        # Never release servos: that can let the arm fall under gravity.
        try:
            self.mc.stop()
        except Exception as exc:
            self.get_logger().error(f'SDK stop failed: {exc}; use the physical stop/power control')

    def goal_tolerance_for(self, initial, target):
        return self.goal_tolerance

    def execute(self, handle):
        result=FollowJointTrajectory.Result()
        sent=False
        try:
            points=self.validated(handle.request)
            initial=self.current()
            gate=CommandGate(initial)
            effective_tolerance=self.goal_tolerance_for(initial, points[-1][1])
            command_count=0
            last_command_time=-math.inf
            start=time.monotonic()
            deadline=points[-1][0]+10.0
            while rclpy.ok():
                if handle.is_cancel_requested:
                    if sent:self.stop_arm()
                    handle.canceled()
                    result.error_code=FollowJointTrajectory.Result.SUCCESSFUL
                    result.error_string='Canceled; SDK stop requested'
                    return result
                elapsed=time.monotonic()-start
                actual_stamp,actual=self.current_sample()
                desired=interpolate(points,min(elapsed,points[-1][0]))
                if any(abs(a-b)>0.12 for a,b in zip(actual,desired)):
                    raise RuntimeError('Tracking error exceeded 0.12rad')
                final=elapsed >= points[-1][0]
                if gate.select(desired,elapsed,final=final):
                    sent=True
                    self.mc.send_angles([round(math.degrees(q),2) for q in desired],10,_async=True)
                    command_count+=1
                    last_command_time=time.monotonic()
                if final and gate.final_handled:
                    fresh=actual_stamp>=last_command_time
                    if fresh and all(abs(a-b)<=effective_tolerance for a,b in zip(actual,points[-1][1])):
                        handle.succeed()
                        result.error_code=FollowJointTrajectory.Result.SUCCESSFUL
                        errors=[round(math.degrees(a-b),3) for a,b in zip(actual,points[-1][1])]
                        result.error_string=f'Within {math.degrees(effective_tolerance):g}deg tolerance; actual-goal errors_deg={errors}; commands={command_count}'
                        self.get_logger().info(result.error_string)
                        return result
                    if elapsed>deadline:
                        raise RuntimeError('Goal not reached in actual feedback')
                # This remains an approximate SDK-based path executor, not a
                # real-time servo interface. Keep sampling the full planned
                # path; do not replace it with one final-pose command.
                feedback=FollowJointTrajectory.Feedback()
                feedback.header.stamp=self.get_clock().now().to_msg()
                feedback.joint_names=list(self.arm_names)
                feedback.desired=JointTrajectoryPoint(positions=list(desired))
                feedback.actual=JointTrajectoryPoint(positions=list(actual))
                feedback.error=JointTrajectoryPoint(positions=[d-a for d,a in zip(desired,actual)])
                handle.publish_feedback(feedback)
                time.sleep(0.05)
            raise RuntimeError('ROS shutdown during trajectory')
        except Exception as exc:
            if sent:self.stop_arm()
            result.error_code=FollowJointTrajectory.Result.PATH_TOLERANCE_VIOLATED
            result.error_string=str(exc)
            if handle.is_active:handle.abort()
            self.get_logger().error(f'Trajectory failed: {exc}')
            return result
        finally:
            with self.guard:self.busy=False


def main():
    rclpy.init();node=None;executor=MultiThreadedExecutor(num_threads=3)
    try:
        node=HardwareBridge();executor.add_node(node);executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            if node.busy:node.stop_arm()
            executor.shutdown(timeout_sec=3)
            node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
