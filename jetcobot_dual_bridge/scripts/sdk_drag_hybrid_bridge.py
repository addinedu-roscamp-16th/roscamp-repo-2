#!/usr/bin/env python3
"""One serial client with separate endpoint and collision-checked drag actions."""
import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from sdk_drag_bridge import SDKDragBridge
from sdk_goal_bridge import SDKGoalBridge


class SDKDragHybridBridge(SDKDragBridge):
    def __init__(self):
        super().__init__()
        self.endpoint_server = ActionServer(
            self, FollowJointTrajectory,
            f'/{self.side}/sdk_endpoint_controller/follow_joint_trajectory',
            execute_callback=self.execute_endpoint,
            goal_callback=self.endpoint_goal,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=ReentrantCallbackGroup())
        self.get_logger().warning(
            'DRAG HYBRID: endpoint and drag actions share one serial client and busy lock; '
            'PC collision-scene gate selects exactly one action')

    def endpoint_goal(self, request):
        with self.guard:
            if (not self.motion_enabled or self.busy or self.manual_mode or
                    self.fresh_mode != self.required_fresh_mode):
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
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node = SDKDragHybridBridge()
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
