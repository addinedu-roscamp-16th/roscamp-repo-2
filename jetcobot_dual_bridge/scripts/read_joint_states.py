#!/usr/bin/env python3
"""Query arm/gripper only. Display finger angles are estimates, not measurements."""
import math
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32, Float64MultiArray
from pymycobot.mycobot280 import MyCobot280


class ReadOnlyState(Node):
    def __init__(self, node_name='jetcobot_read_only_state', default_state_hz=2.0):
        super().__init__(node_name)
        self.declare_parameter('robot_side', '')
        self.side = str(self.get_parameter('robot_side').value)
        if self.side not in ('left', 'right') or self.get_namespace() != '/' + self.side:
            raise ValueError('Set robot_side:=left/right and matching -r __ns:=/left or /right')
        self.arm_names = tuple(f'{self.side}_{i}_Joint' for i in range(1, 7))
        self.gripper_name = f'{self.side}_gripper_controller'
        self.declare_parameter('port', '/dev/ttyJETCOBOT')
        # 0 means OMIT the SDK argument, matching this project's existing setter.
        self.declare_parameter('gripper_type', 0)
        self.declare_parameter('state_hz', default_state_hz)
        state_hz = float(self.get_parameter('state_hz').value)
        if not math.isfinite(state_hz) or not 1 <= state_hz <= 5:
            raise ValueError('state_hz must be between 1 and 5')
        self.gripper_type = int(self.get_parameter('gripper_type').value)
        if self.gripper_type not in (0, 1, 3, 4):
            raise ValueError('gripper_type must be 0 (firmware default), 1, 3 or 4')
        self.mc = MyCobot280(self.get_parameter('port').value, 1000000)
        self.mc.thread_lock = True
        self.publisher = self.create_publisher(JointState, f'/{self.side}/real_joint_states', 10)
        self.display = self.create_publisher(JointState, f'/{self.side}/display_joint_states', 10)
        self.gripper = self.create_publisher(Int32, f'/{self.side}/gripper_value', 10)
        self.sdk_pose_pub = self.create_publisher(Float64MultiArray, f'/{self.side}/sdk_coords', 10)
        # Servo temperatures (2026-11-20): J4 drooped once per day after heating
        # (left J4 53 C idle). Read only while idle, every 10 s: [unix_time, J1..J6 C].
        self.temp_pub = self.create_publisher(Float64MultiArray, f'/{self.side}/servo_temps', 10)
        self.next_temp_read = 0.0
        self.gripper_sample = None
        self.latest_arm = None
        self.gripper_time = 0.0
        self.next_gripper_read = 0.0
        self.timer = self.create_timer(1.0/state_hz, self.read)
        self.get_logger().info(f'READ ONLY: querying arm angles at {state_hz:g} Hz; no motion commands.'
                               if node_name == 'jetcobot_read_only_state'
                               else f'State reader started at {state_hz:g} Hz; execution policy follows below.')

    def read_gripper_value(self):
        if self.gripper_type == 0:
            return self.mc.get_gripper_value()
        return self.mc.get_gripper_value(self.gripper_type)

    def read(self):
        try:
            angles = self.mc.get_angles()
            if not isinstance(angles, (list, tuple)) or len(angles) != 6:
                self.get_logger().warning(f'Invalid arm reading: {angles!r}')
                return
            values = [float(value) for value in angles]
            if not all(math.isfinite(value) for value in values):
                self.get_logger().warning('Non-finite arm reading; skipping.')
                return
            self.latest_arm = (time.monotonic(), tuple(math.radians(value) for value in values))
            msg = JointState()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.name = [f'{i}_Joint' for i in range(1, 7)]
            msg.position = [math.radians(value) for value in values]
            self.publisher.publish(msg)
            now = time.monotonic()
            moving = bool(getattr(self, 'busy', False))
            # 2026-11-20: at most ONE extra serial query per tick (gripper, temps
            # or coords) so an idle tick never delays the next angle publish.
            aux = None
            if not moving:
                aux = ('gripper' if now >= self.next_gripper_read else
                       'temps' if now >= self.next_temp_read else 'coords')
            if aux == 'gripper':
                self.next_gripper_read = now + 2.0
                try:
                    value = self.read_gripper_value()
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
                        first = self.gripper_sample is None
                        self.gripper_sample = float(value)
                        self.gripper_time = time.monotonic()
                        self.gripper.publish(Int32(data=int(value)))
                        if first:
                            self.get_logger().info(f'Gripper read OK (type {self.gripper_type}): {value}; display angle uses approximate mapping only.')
                    else:
                        self.gripper_sample = None
                        self.gripper.publish(Int32(data=-1))
                        self.get_logger().warning(f'Invalid gripper reading: {value!r}')
                except Exception as exc:
                    self.gripper_sample = None
                    self.gripper.publish(Int32(data=-1))
                    self.get_logger().warning(f'Gripper read failed: {exc}')
            # Reuse this process's serial connection; do not open a second SDK client.
            if aux == 'coords':
                try:
                    coords = self.mc.get_coords()
                    if (isinstance(coords, (list, tuple)) and len(coords) == 6
                            and all(math.isfinite(float(v)) for v in coords)):
                        self.sdk_pose_pub.publish(Float64MultiArray(
                            data=[time.time()] + [float(v) for v in coords]))
                except Exception as exc:
                    self.get_logger().warning(f'SDK pose read failed: {exc}')
            if aux == 'temps':
                if True:
                    self.next_temp_read = now + 10.0
                    try:
                        temps = self.mc.get_servo_temps()
                        if (isinstance(temps, (list, tuple)) and len(temps) == 6 and
                                all(isinstance(v, (int, float)) for v in temps)):
                            self.temp_pub.publish(Float64MultiArray(
                                data=[time.time()] + [float(v) for v in temps]))
                            hot = [f'J{i+1}={v:g}C' for i, v in enumerate(temps) if v >= 55]
                            if hot:
                                self.get_logger().warning(
                                    f'SERVO HOT (>=55 C): {", ".join(hot)}; all={list(temps)}')
                    except Exception as exc:
                        self.get_logger().warning(f'Servo temperature read failed: {exc}')
            preview = JointState()
            preview.header = msg.header
            preview.name = list(msg.name)
            preview.position = list(msg.position)
            if self.gripper_sample is not None and (moving or time.monotonic() - self.gripper_time < 5.0):
                # Approximate illustration: SDK 0=closed, 100=open. The URDF
                # model is tuned to the stated ~50.5mm opening, not CAD data.
                # Preserve /real_joint_states as measured arm angles only.
                # Hold the last illustrative gripper pose during arm motion;
                # no gripper motion commands are supported by this bridge.
                preview.name.append('gripper_controller')
                preview.position.append(-0.83 + (0.535 + 0.83) * self.gripper_sample / 100.0)
            self.display.publish(preview)
        except Exception as exc:
            self.get_logger().error(f'Read failed: {exc}')


def main():
    rclpy.init()
    node = None
    try:
        node = ReadOnlyState()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
