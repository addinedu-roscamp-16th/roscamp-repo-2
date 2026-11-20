#!/usr/bin/env python3
"""Legacy hybrid serial client with separate endpoint and sparse-path actions.

Run this instead of either standalone SDK bridge for the same Pi.
"""
import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from sdk_goal_bridge import SDKGoalBridge
from sdk_path_bridge import SDKPathBridge


class SDKHybridBridge(SDKPathBridge):
    def __init__(self):
        super().__init__(action_name='/{side}/sdk_path_controller/follow_joint_trajectory',
                         lookahead_default=True, step_default=6.0)
        self.endpoint_server = ActionServer(
            self, FollowJointTrajectory,
            f'/{self.side}/sdk_endpoint_controller/follow_joint_trajectory',
            execute_callback=self.execute_endpoint,
            goal_callback=self.endpoint_goal,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())
        self.get_logger().warning('HYBRID: endpoint and sparse path share one SDK serial client and one busy lock')

    def endpoint_goal(self, request):
        with self.guard:
            if (not self.motion_enabled or self.busy or self.manual_mode or
                    self.fresh_mode != 0 or self.required_fresh_mode != 0):
                return GoalResponse.REJECT
            try:
                SDKGoalBridge.validated(self, request)
            except Exception as exc:
                self.get_logger().error(f'Endpoint rejected: {exc}')
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    def execute_endpoint(self, handle):
        return SDKGoalBridge.execute(self, handle)


def main():
    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = SDKHybridBridge()
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
                    node.get_logger().error(f'SDK mode restoration failed: {exc}')
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
