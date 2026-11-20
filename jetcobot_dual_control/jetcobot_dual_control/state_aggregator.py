"""Combine two measured arms; never republish missing/stale arm states as fresh."""
import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32, String

SIDES = ('left', 'right')
ARM_NAMES = tuple(f'{i}_Joint' for i in range(1, 7))


class StateCache:
    def __init__(self, stale_seconds=1.5):
        if not math.isfinite(stale_seconds) or stale_seconds <= 0:
            raise ValueError('stale_seconds must be positive')
        self.stale_seconds = stale_seconds
        self.arms = {}
        self.grippers = {}

    def arm(self, side, names, positions, stamp_ns, received):
        if side not in SIDES or len(names) != 6 or set(names) != set(ARM_NAMES) or len(positions) != 6:
            self.arms.pop(side, None)
            raise ValueError('Expected exactly six distinct, unprefixed arm joints')
        q = dict(zip(names, positions))
        values = [float(q[name]) for name in ARM_NAMES]
        if not all(math.isfinite(v) for v in values) or stamp_ns <= 0:
            self.arms.pop(side, None)
            raise ValueError('Invalid measured arm data')
        # Do not let repeated messages refresh a stalled source.
        previous = self.arms.get(side)
        if previous and stamp_ns <= previous['stamp_ns']:
            if stamp_ns < previous['stamp_ns']:
                self.arms.pop(side, None)
            raise ValueError('Repeated or backwards arm timestamp')
        self.arms[side] = dict(positions=values, stamp_ns=stamp_ns, received=received)

    def gripper(self, side, value, received):
        if side not in SIDES or not 0 <= value <= 100:
            self.grippers.pop(side, None)
            raise ValueError('Invalid gripper feedback')
        # Illustration only: this is not a calibrated physical finger measurement.
        self.grippers[side] = (-.83 + 1.365 * value / 100, received)

    def snapshot(self, now, ros_ns):
        names, values, display_names, display_values, stamps = [], [], [], [], []
        status = {}
        for side in SIDES:
            row = self.arms.get(side)
            age = None if row is None else now-row['received']
            source_age = None if row is None else (ros_ns-row['stamp_ns'])/1e9
            fresh = row is not None and 0 <= age <= self.stale_seconds and -.25 <= source_age <= self.stale_seconds
            grip = self.grippers.get(side)
            grip_fresh = grip is not None and now-grip[1] <= 5
            status[side] = {'fresh': fresh, 'receive_age_s': age, 'source_age_s': source_age,
                            'gripper_display': 'sdk_estimate' if grip_fresh else 'unavailable'}
            if fresh:
                side_names = [side + '_' + name for name in ARM_NAMES]
                names.extend(side_names)
                values.extend(row['positions'])
                display_names.extend(side_names)
                display_values.extend(row['positions'])
                # Missing feedback must not be advertised as a measured OPEN state.
                if grip_fresh:
                    display_names.append(side + '_gripper_controller')
                    display_values.append(grip[0])
                stamps.append(row['stamp_ns'])
        if not all(status[side]['fresh'] for side in SIDES):
            return None, status
        # Preserve the oldest contributing measurement time instead of stamping old data 'now'.
        return (min(stamps), names, values, display_names, display_values), status


class StateAggregator(Node):
    def __init__(self):
        super().__init__('state_aggregator')
        self.declare_parameter('stale_seconds', 1.5)
        self.cache = StateCache(float(self.get_parameter('stale_seconds').value))
        self.measured = self.create_publisher(JointState, '/jetcobot_dual/joint_states', 10)
        self.display = self.create_publisher(JointState, '/jetcobot_dual/display_joint_states', 10)
        self.status = self.create_publisher(String, '/jetcobot_dual/state_status', 10)
        self.subscriptions_owned = []
        for side in SIDES:
            self.subscriptions_owned.append(self.create_subscription(
                JointState, f'/{side}/real_joint_states',
                lambda msg, side=side: self.on_arm(side, msg), qos_profile_sensor_data))
            self.subscriptions_owned.append(self.create_subscription(
                Int32, f'/{side}/gripper_value',
                lambda msg, side=side: self.on_gripper(side, msg), qos_profile_sensor_data))
        self.timer = self.create_timer(.1, self.publish)
        self.last_ready = None

    def on_arm(self, side, msg):
        try:
            stamp = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
            self.cache.arm(side, list(msg.name), list(msg.position), stamp, time.monotonic())
        except ValueError as exc:
            self.get_logger().warning(f'{side}: {exc}')

    def on_gripper(self, side, msg):
        try:
            self.cache.gripper(side, msg.data, time.monotonic())
        except ValueError as exc:
            self.get_logger().warning(f'{side}: {exc}')

    def publish(self):
        snapshot, status = self.cache.snapshot(time.monotonic(), self.get_clock().now().nanoseconds)
        ready = snapshot is not None
        self.status.publish(String(data=json.dumps({'ready': ready, 'arms': status})))
        if ready != self.last_ready:
            if ready:
                self.get_logger().info('Both measured arms present.')
            else:
                missing = {side: {'receive_age_s': status[side]['receive_age_s'],
                                  'source_age_s': status[side]['source_age_s']}
                           for side in SIDES if not status[side]['fresh']}
                self.get_logger().warning(
                    f'Combined state withheld; stale arms={missing}')
            self.last_ready = ready
        if not ready:
            return
        stamp, names, values, display_names, display_values = snapshot
        measured = JointState()
        measured.header.stamp.sec = stamp//1_000_000_000
        measured.header.stamp.nanosec = stamp % 1_000_000_000
        measured.name, measured.position = names, values
        self.measured.publish(measured)
        display = JointState()
        display.header = measured.header
        display.name, display.position = display_names, display_values
        self.display.publish(display)


def main(args=None):
    rclpy.init(args=args)
    node = StateAggregator()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
