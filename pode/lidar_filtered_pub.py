

import rclpy
import math
import copy

from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from rclpy.qos import qos_profile_sensor_data


class LidarDistance(Node):

    def __init__(self):
        super().__init__('lidar_filter')

        # 원본 라이다 구독
        self.subscription = self.create_subscription(
            LaserScan,
            '/scan',
            self.scan_callback,
            qos_profile_sensor_data
        )

        # 필터링된 라이다 발행
        self.filtered_publisher = self.create_publisher(
            LaserScan,
            '/scan_filtered',
            qos_profile_sensor_data
        )

        # 왼쪽 볼트 필터 범위 (로봇 기준)
        self.LEFT_ANGLE_MIN = -155.0
        self.LEFT_ANGLE_MAX = -140.0
        self.LEFT_RANGE_MIN = 0.04
        self.LEFT_RANGE_MAX = 0.06

        # 오른쪽 볼트 필터 범위 (로봇 기준)
        self.RIGHT_ANGLE_MIN = 138.0
        self.RIGHT_ANGLE_MAX = 153.0
        self.RIGHT_RANGE_MIN = 0.04
        self.RIGHT_RANGE_MAX = 0.062

        self.get_logger().info(
            'LiDAR Bolt Filter Started | '
            'Publishing: /scan_filtered'
        )

    def normalize_angle(self, angle):
        return math.atan2(
            math.sin(angle),
            math.cos(angle)
        )

    def is_bolt(self, angle_deg, distance):

        # 왼쪽 볼트
        left_bolt = (
            self.LEFT_ANGLE_MIN <= angle_deg <= self.LEFT_ANGLE_MAX
            and self.LEFT_RANGE_MIN <= distance <= self.LEFT_RANGE_MAX
        )

        # 오른쪽 볼트
        right_bolt = (
            self.RIGHT_ANGLE_MIN <= angle_deg <= self.RIGHT_ANGLE_MAX
            and self.RIGHT_RANGE_MIN <= distance <= self.RIGHT_RANGE_MAX
        )

        return left_bolt or right_bolt

    def scan_callback(self, msg):

        if not msg.ranges:
            return

        # 원본 메시지를 복사하여 필터링 메시지 생성
        filtered_msg = copy.deepcopy(msg)

        ranges = list(msg.ranges)

        valid_points = []
        filtered_count = 0

        for i, distance in enumerate(ranges):

            # 유효하지 않은 거리 데이터는 그대로 유지
            if not (
                math.isfinite(distance)
                and msg.range_min <= distance <= msg.range_max
            ):
                continue

            # 원본 라이다 각도
            angle = msg.angle_min + i * msg.angle_increment

            # 로봇 정면 기준 각도로 변환
            robot_angle = self.normalize_angle(
                -(angle + math.pi)
            )

            angle_deg = math.degrees(robot_angle)

            # 볼트 판별
            if self.is_bolt(angle_deg, distance):

                # 해당 포인트를 NaN으로 변경
                ranges[i] = float('nan')
                filtered_count += 1

                continue

            # 볼트가 아닌 유효 장애물 저장
            valid_points.append((robot_angle, distance))

        # 필터링된 ranges 적용
        filtered_msg.ranges = ranges

        # /scan_filtered 발행
        self.filtered_publisher.publish(filtered_msg)

        # 가장 가까운 장애물 탐색
        if not valid_points:

            self.get_logger().info(
                f'유효한 장애물 없음 | 필터링: {filtered_count}개'
            )

            return

        angle, distance = min(
            valid_points,
            key=lambda point: point[1]
        )

        angle_deg = math.degrees(angle)

        self.get_logger().info(
            f'가장 가까운 장애물 | '
            f'방향: {angle_deg:.1f}° | '
            f'거리: {distance:.3f} m | '
            f'필터링: {filtered_count}개'
        )


def main(args=None):

    rclpy.init(args=args)

    node = LidarDistance()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
