
import math
import time

import rclpy

from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions

from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import Odometry


class ArucoParking(Node):

    def __init__(self):
        super().__init__("aruco_parking")

        # =========================
        # ROS 통신
        # =========================

        self.pose_sub = self.create_subscription(
            PoseStamped,
            "/aruco/marker_pose",
            self.pose_callback,
            1
        )

        self.odom_sub = self.create_subscription(
            Odometry,
            "/odom",
            self.odom_callback,
            10
        )

        self.cmd_pub = self.create_publisher(
            Twist,
            "/cmd_vel",
            10
        )

        # =========================
        # 제어 파라미터
        # =========================

        self.KP_HEADING = 0.7
        self.KP_ROTATE = 1.2

        self.MAX_ANGULAR = 0.25
        self.MIN_ANGULAR = 0.05

        self.HEADING_TOLERANCE = 3.0
        self.LATERAL_TOLERANCE = 0.005

        self.ROTATE_ANGLE = 90.0
        self.ROTATE_TOLERANCE = 1.0

        self.ALIGN_TIME = 0.5
        self.ROTATE_SETTLE_TIME = 0.3

        self.MARKER_TIMEOUT = 0.3
        self.ODOM_TIMEOUT = 0.5
        self.ROTATE_TIMEOUT = 30.0

        self.EMA_ALPHA = 0.3

        # Lateral 이동 파라미터
        self.LINEAR_SPEED = 0.03
        self.MIN_LINEAR = 0.01

        self.KP_LINEAR = 1.0
        self.KP_MOVE_HEADING = 1.0

        self.MOVE_TOLERANCE = 0.003
        self.MOVE_TIMEOUT = 30.0
        self.MOVE_OFFSET = 0.007

        # 최종 Heading 정렬
        self.FINAL_HEADING_TOLERANCE = 3.0
        self.FINAL_ALIGN_TIME = 0.5
        self.FINAL_ALIGN_TIMEOUT = 15.0

        self.final_align_start_time = None

        # =========================
        # ArUco 상태 변수
        # =========================

        self.heading = None
        self.filtered_heading = None

        self.lateral = None
        self.filtered_lateral = None

        self.last_marker_time = None
        self.align_start_time = None

        # Forward 거리 조정
        self.TARGET_FORWARD = 0.15
        self.FORWARD_TOLERANCE = 0.005
        self.FORWARD_ALIGN_TIME = 0.5
        self.FORWARD_TIMEOUT = 30.0

        self.forward = None
        self.filtered_forward = None
        self.forward_start_time = None

        # 180도 회전
        self.FINAL_ROTATE_ANGLE = 180.0
        self.FINAL_ROTATE_OFFSET = 0
        self.FINAL_ROTATE_TOLERANCE = 1.0

        # 후진 주차
        self.REVERSE_DISTANCE = 0.05
        self.REVERSE_SPEED = 0.03
        self.REVERSE_TIMEOUT = 40.0

        self.REVERSE_ENABLED = True

        self.reverse_start_x = None
        self.reverse_start_y = None
        self.reverse_start_yaw = None
        self.reverse_start_time = None

        # =========================
        # Odom 상태 변수
        # =========================

        self.current_yaw = None
        self.last_odom_time = None

        self.rotate_start_yaw = None
        self.target_yaw = None

        self.rotate_start_time = None
        self.rotate_settle_start = None

        self.saved_lateral = None

        # 현재 Odom 위치
        self.current_x = None
        self.current_y = None

        # Lateral 이동 정보
        self.move_start_x = None
        self.move_start_y = None
        self.move_start_yaw = None

        self.move_target_distance = None
        self.move_start_time = None

        # =========================
        # 상태 머신
        # =========================

        self.state = "WAIT_MARKER"

        self.timer = self.create_timer(
            0.05,
            self.control_loop
        )

        self.get_logger().info(
            "ArUco Heading + 90 Degree Rotation Started"
        )

    # =========================
    # 각도 정규화
    # =========================

    def normalize_angle(self, angle):

        return math.atan2(
            math.sin(angle),
            math.cos(angle)
        )

    # =========================
    # EMA 필터
    # =========================

    def ema_filter(self, previous, current):

        if previous is None:
            return current

        return (
            self.EMA_ALPHA * current
            + (1.0 - self.EMA_ALPHA) * previous
        )

    # =========================
    # ArUco Pose 수신
    # =========================

    def pose_callback(self, msg):

        x = msg.pose.position.x
        z = msg.pose.position.z

        qx = msg.pose.orientation.x
        qy = msg.pose.orientation.y
        qz = msg.pose.orientation.z
        qw = msg.pose.orientation.w

        values = [x, z, qx, qy, qz, qw]

        if (
            not all(math.isfinite(v) for v in values)
            or z <= 0
        ):
            self.last_marker_time = None
            return

        norm = math.sqrt(
            qx*qx + qy*qy + qz*qz + qw*qw
        )

        if norm < 1e-6:
            self.last_marker_time = None
            return

        qx /= norm
        qy /= norm
        qz /= norm
        qw /= norm

        normal_x = 2.0 * (
            qx*qz + qw*qy
        )

        normal_z = 1.0 - 2.0 * (
            qx*qx + qy*qy
        )

        if normal_z >= 0:
            self.last_marker_time = None
            return

        self.heading = math.degrees(
            math.atan2(normal_x, -normal_z)
        )

        # 기존 카메라 좌표계 기준
        # 오른쪽 양수, 왼쪽 음수

        self.lateral = -x

        self.forward = z

        now = time.monotonic()

        if (
            self.last_marker_time is None
            or now - self.last_marker_time
            > self.MARKER_TIMEOUT
        ):
            self.filtered_heading = None
            self.filtered_lateral = None

        self.filtered_heading = self.ema_filter(
            self.filtered_heading,
            self.heading
        )

        self.filtered_lateral = self.ema_filter(
            self.filtered_lateral,
            self.lateral
        )


        if (
            self.last_marker_time is None
            or now - self.last_marker_time
            > self.MARKER_TIMEOUT
        ):
            self.filtered_forward = None

        self.filtered_forward = self.ema_filter(
            self.filtered_forward,
            self.forward
        )

        self.last_marker_time = now

    # =========================
    # Odom 수신
    # =========================

    def odom_callback(self, msg):

        q = msg.pose.pose.orientation

        values = [q.x, q.y, q.z, q.w]

        if not all(math.isfinite(v) for v in values):
            self.last_odom_time = None
            return

        norm = math.sqrt(sum(v*v for v in values))

        if norm < 1e-6:
            self.last_odom_time = None
            return

        x, y, z, w = [v / norm for v in values]

        siny = 2.0 * (
            w*z + x*y
        )

        cosy = 1.0 - 2.0 * (
            y*y + z*z
        )

        self.current_yaw = math.atan2(
            siny,
            cosy
        )

   

        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y

        if not all(math.isfinite(v) for v in [px, py]):
            self.last_odom_time = None
            return

        self.current_x = px
        self.current_y = py

        self.last_odom_time = time.monotonic()

    # =========================
    # 회전 속도 계산
    # =========================

    def calculate_angular(self, error, kp):

        angular = kp * error

        angular = max(
            -self.MAX_ANGULAR,
            min(self.MAX_ANGULAR, angular)
        )

        if 0 < abs(angular) < self.MIN_ANGULAR:

            angular = math.copysign(
                self.MIN_ANGULAR,
                angular
            )

        return angular

    # =========================
    # 상태 변경
    # =========================

    def change_state(self, new_state):

        if self.state != new_state:

            self.get_logger().info(
                f"{self.state} -> {new_state}"
            )

            self.state = new_state

    # =========================
    # 메인 제어 루프
    # =========================

    def control_loop(self):

        cmd = Twist()
        now = time.monotonic()

        # 이번 테스트에서는 전진 금지
        cmd.linear.x = 0.0

        # =========================
        # 정지 상태
        # =========================

        if self.state in ("HOLDING", "ABORTED"):

            self.cmd_pub.publish(cmd)
            return

        # =========================
        # Odom 유효성 확인
        # =========================

        if (
            self.last_odom_time is None
            or now - self.last_odom_time
            > self.ODOM_TIMEOUT
        ):

            if self.state in (
                "ROTATE_90",
                "MOVE_LATERAL",
                "RETURN_ROTATE",
                "FINAL_HEADING_ALIGN",
                "ADJUST_FORWARD",
                "ROTATE_180",
                "REVERSE_PARKING",
            ):
                self.change_state("ABORTED")
            else:
                self.change_state("WAIT_MARKER")
                self.align_start_time = None

            self.cmd_pub.publish(cmd)

            self.get_logger().warning(
                "ODOM UNAVAILABLE | STOP",
                throttle_duration_sec=1.0
            )

            return

        # =========================
        # ArUco 유효성 확인
        # =========================

        # 90도 회전 중에는 마커가
        # 보이지 않아도 Odom으로 제어

        if self.state not in (
            "ROTATE_90",
            "MOVE_LATERAL",
            "RETURN_ROTATE",
            "FINAL_HEADING_ALIGN",
            "ADJUST_FORWARD",
            "ROTATE_180",
            "REVERSE_PARKING",
        ):

            if (
                self.last_marker_time is None
                or now - self.last_marker_time
                > self.MARKER_TIMEOUT
            ):

                self.change_state("WAIT_MARKER")

                self.align_start_time = None
                self.filtered_heading = None
                self.filtered_lateral = None

                self.cmd_pub.publish(cmd)

                self.get_logger().info(
                    "MARKER UNAVAILABLE | STOP",
                    throttle_duration_sec=1.0
                )

                return

        # =========================
        # 1. 마커 대기
        # =========================

        if self.state == "WAIT_MARKER":

            self.change_state("HEADING_ALIGN")

        # =========================
        # 2. Heading 정렬
        # =========================

        if self.state == "HEADING_ALIGN":

            error = self.filtered_heading

            if abs(error) <= self.HEADING_TOLERANCE:

                cmd.angular.z = 0.0

                if abs(self.heading) <= self.HEADING_TOLERANCE:

                    if self.align_start_time is None:

                        self.align_start_time = now

                    elif (
                        now - self.align_start_time
                        >= self.ALIGN_TIME
                    ):

                        self.change_state(
                            "CHECK_LATERAL"
                        )

                else:
                    self.align_start_time = None

            else:

                self.align_start_time = None

                cmd.angular.z = self.calculate_angular(
                    -math.radians(error),
                    self.KP_HEADING
                )

        # =========================
        # 3. Lateral 방향 확인
        # =========================

        elif self.state == "CHECK_LATERAL":

            if (
                abs(self.filtered_heading)
                > self.HEADING_TOLERANCE
                or abs(self.heading)
                > self.HEADING_TOLERANCE
            ):

                self.align_start_time = None
                self.change_state("HEADING_ALIGN")

                self.cmd_pub.publish(cmd)
                return

            self.saved_lateral = (
                self.filtered_lateral
            )

            self.rotate_start_yaw = (
                self.current_yaw
            )

            if (
                self.saved_lateral
                > self.LATERAL_TOLERANCE
            ):

                # 오른쪽: 시계방향 90도
                self.target_yaw = self.normalize_angle(
                    self.rotate_start_yaw
                    - math.radians(self.ROTATE_ANGLE)
                )

                self.get_logger().info(
                    "LATERAL POSITIVE -> RIGHT 90 DEG"
                )

            elif (
                self.saved_lateral
                < -self.LATERAL_TOLERANCE
            ):

                # 왼쪽: 반시계방향 90도
                self.target_yaw = self.normalize_angle(
                    self.rotate_start_yaw
                    + math.radians(self.ROTATE_ANGLE)
                )

                self.get_logger().info(
                    "LATERAL NEGATIVE -> LEFT 90 DEG"
                )

            else:

                self.final_align_start_time = now
                self.align_start_time = None

                self.change_state("FINAL_HEADING_ALIGN")

                self.get_logger().info(
                    "LATERAL ALREADY CENTERED"
                )

                self.cmd_pub.publish(cmd)
                return

            self.rotate_start_time = now
            self.rotate_settle_start = None

            self.change_state("ROTATE_90")

        # =========================
        # 4. Odom 기반 90도 회전
        # =========================

        elif self.state == "ROTATE_90":

            if (
                now - self.rotate_start_time
                > self.ROTATE_TIMEOUT
            ):

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "ROTATION TIMEOUT | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            yaw_error = self.normalize_angle(
                self.target_yaw - self.current_yaw
            )

            rotated_angle = self.normalize_angle(
                self.current_yaw
                - self.rotate_start_yaw
            )

            if (
                abs(math.degrees(yaw_error))
                <= self.ROTATE_TOLERANCE
            ):

                cmd.angular.z = 0.0

                if self.rotate_settle_start is None:

                    self.rotate_settle_start = now

                elif (
                    now - self.rotate_settle_start
                    >= self.ROTATE_SETTLE_TIME
                ):

                    # Lateral 이동 시작 위치 저장
                    self.move_start_x = self.current_x
                    self.move_start_y = self.current_y

                    # 90도 회전 목표 방향 유지
                    self.move_start_yaw = self.target_yaw

                    # 이동할 거리 저장
                    self.move_target_distance = abs(
                        abs(self.saved_lateral) + self.MOVE_OFFSET
                    ) 

                    self.move_start_time = now

                    self.change_state("MOVE_LATERAL")

                    self.get_logger().info(
                        f"90 DEG ROTATION COMPLETE | "
                        f"Move Target: "
                        f"{self.move_target_distance:.3f} m"
                    )

            else:

                self.rotate_settle_start = None

                cmd.angular.z = self.calculate_angular(
                    yaw_error,
                    self.KP_ROTATE
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Rotated: "
                f"{math.degrees(rotated_angle):+.2f} deg | "
                f"Remaining: "
                f"{math.degrees(yaw_error):+.2f} deg | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )


        # =========================
        # 5. Lateral 거리만큼 전진
        # =========================

        elif self.state == "MOVE_LATERAL":

            # 이동 시간 초과 시 정지
            if (
                now - self.move_start_time
                > self.MOVE_TIMEOUT
            ):

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "MOVE TIMEOUT | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            # 이동 시작점 기준 위치 변화량
            dx = self.current_x - self.move_start_x
            dy = self.current_y - self.move_start_y

            # 목표 방향으로 실제 이동한 거리
            moved_distance = (
                math.cos(self.move_start_yaw) * dx
                + math.sin(self.move_start_yaw) * dy
            )

            # 남은 이동 거리
            remaining_distance = (
                self.move_target_distance
                - moved_distance
            )

            # 이동 중 방향 오차
            yaw_error = self.normalize_angle(
                self.move_start_yaw
                - self.current_yaw
            )

            # 방향이 크게 틀어지면 정지
            if abs(math.degrees(yaw_error)) > 10.0:

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "MOVE HEADING ERROR | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            # 목표 거리 도달
            if remaining_distance <= self.MOVE_TOLERANCE:

                cmd.linear.x = 0.0
                cmd.angular.z = 0.0

                # 처음 Heading 정렬을 마쳤던 방향으로 복귀
                self.target_yaw = self.rotate_start_yaw

                # 회전 시간 초기화
                self.rotate_start_time = now
                self.rotate_settle_start = None

                self.change_state("RETURN_ROTATE")

                self.get_logger().info(
                    f"LATERAL MOVE COMPLETE | "
                    f"Target: "
                    f"{self.move_target_distance:.3f} m | "
                    f"Moved: {moved_distance:.3f} m"
                )

            else:

                # 목표 지점에 가까워질수록 감속
                cmd.linear.x = min(
                    self.LINEAR_SPEED,
                    max(
                        self.MIN_LINEAR,
                        self.KP_LINEAR * remaining_distance
                    )
                )

                # 직진 중 Odom 방향 유지
                cmd.angular.z = max(
                    -0.15,
                    min(
                        0.15,
                        self.KP_MOVE_HEADING * yaw_error
                    )
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Target: "
                f"{self.move_target_distance:.3f} m | "
                f"Moved: {moved_distance:.3f} m | "
                f"Remaining: {remaining_distance:.3f} m | "
                f"Yaw Error: "
                f"{math.degrees(yaw_error):+.2f} deg | "
                f"V: {cmd.linear.x:.3f} | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )

        # =========================
        # 6. 마커 방향으로 복귀 회전
        # =========================

        elif self.state == "RETURN_ROTATE":

            # 회전 시간 초과 시 정지
            if (
                now - self.rotate_start_time
                > self.ROTATE_TIMEOUT
            ):

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "RETURN ROTATION TIMEOUT | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            # 제자리 회전
            cmd.linear.x = 0.0

            # 목표 방향과 현재 방향의 오차
            yaw_error = self.normalize_angle(
                self.target_yaw - self.current_yaw
            )

            # 목표 각도 도달
            if (
                abs(math.degrees(yaw_error))
                <= self.ROTATE_TOLERANCE
            ):

                cmd.angular.z = 0.0

                if self.rotate_settle_start is None:

                    self.rotate_settle_start = now

                elif (
                    now - self.rotate_settle_start
                    >= self.ROTATE_SETTLE_TIME
                ):

                    # 기존 Heading 필터 초기화
                    self.filtered_heading = None
                    self.filtered_lateral = None
                    self.last_marker_time = None

                    self.align_start_time = None
                    self.final_align_start_time = now

                    self.change_state(
                        "FINAL_HEADING_ALIGN"
                    )

                    self.get_logger().info(
                        "RETURN ROTATION COMPLETE "
                        "-> FINAL HEADING ALIGN"
                    )

            else:

                self.rotate_settle_start = None

                cmd.angular.z = self.calculate_angular(
                    yaw_error,
                    self.KP_ROTATE
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Target Yaw: "
                f"{math.degrees(self.target_yaw):+.2f} deg | "
                f"Current Yaw: "
                f"{math.degrees(self.current_yaw):+.2f} deg | "
                f"Remaining: "
                f"{math.degrees(yaw_error):+.2f} deg | "
                f"V: {cmd.linear.x:.3f} | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )


        # =========================
        # 7. 최종 Heading 정렬
        # =========================

        elif self.state == "FINAL_HEADING_ALIGN":

            # 전진 금지
            cmd.linear.x = 0.0

            # 최종 정렬 시간 제한
            if (
                now - self.final_align_start_time
                > self.FINAL_ALIGN_TIMEOUT
            ):

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "FINAL ALIGN TIMEOUT | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            # 마커 재인식 대기
            if (
                self.last_marker_time is None
                or now - self.last_marker_time
                > self.MARKER_TIMEOUT
                or self.filtered_heading is None
            ):

                self.align_start_time = None
                self.filtered_heading = None

                self.cmd_pub.publish(cmd)

                self.get_logger().info(
                    "WAITING FOR MARKER REACQUISITION",
                    throttle_duration_sec=1.0
                )

                return

            error = self.filtered_heading

            # Heading 허용 오차 이내
            if (
                abs(error)
                <= self.FINAL_HEADING_TOLERANCE
            ):

                cmd.angular.z = 0.0

                if (
                    abs(self.heading)
                    <= self.FINAL_HEADING_TOLERANCE
                ):

                    if self.align_start_time is None:

                        self.align_start_time = now

                    elif (
                        now - self.align_start_time
                        >= self.FINAL_ALIGN_TIME
                    ):

                        self.forward_start_time = now
                        self.align_start_time = None

                        self.change_state("ADJUST_FORWARD")

                        self.get_logger().info(
                            "FINAL HEADING ALIGN COMPLETE "
                            "-> ADJUST FORWARD"
                        )

                else:

                    self.align_start_time = None

            # Heading 보정 회전
            else:

                self.align_start_time = None

                cmd.angular.z = self.calculate_angular(
                    -math.radians(error),
                    self.KP_HEADING
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Heading: {self.heading:+.2f} deg | "
                f"Filtered: {error:+.2f} deg | "
                f"Lateral: "
                f"{self.filtered_lateral:+.3f} m | "
                f"V: {cmd.linear.x:.3f} | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )


        # =========================
        # 8. Forward 거리 조정
        # =========================

        elif self.state == "ADJUST_FORWARD":

            if (
                now - self.forward_start_time
                > self.FORWARD_TIMEOUT
            ):
                self.change_state("ABORTED")
                self.get_logger().warning(
                    "FORWARD TIMEOUT | STOP"
                )
                self.cmd_pub.publish(cmd)
                return

            # 마커 유실 시 정지
            if (
                self.last_marker_time is None
                or now - self.last_marker_time
                > self.MARKER_TIMEOUT
                or self.filtered_forward is None
                or self.filtered_heading is None
            ):
                self.align_start_time = None
                self.cmd_pub.publish(cmd)
                return

            # Forward 거리 오차
            distance_error = (
                self.filtered_forward
                - self.TARGET_FORWARD
            )

            heading_error = self.filtered_heading

            # Heading이 틀어지면 회전부터 보정
            if (
                abs(heading_error)
                > self.FINAL_HEADING_TOLERANCE
            ):
                self.align_start_time = None

                cmd.linear.x = 0.0

                cmd.angular.z = self.calculate_angular(
                    -math.radians(heading_error),
                    self.KP_HEADING
                )

            # 목표 거리 도달
            elif (
                abs(distance_error)
                <= self.FORWARD_TOLERANCE
            ):
                cmd.linear.x = 0.0
                cmd.angular.z = 0.0

                raw_distance_error = (
                    self.forward - self.TARGET_FORWARD
                )

                if (
                    abs(raw_distance_error)
                    > self.FORWARD_TOLERANCE
                    or abs(self.heading)
                    > self.FINAL_HEADING_TOLERANCE
                ):
                    self.align_start_time = None

                elif self.align_start_time is None:
                    self.align_start_time = now

                elif (
                    now - self.align_start_time
                    >= self.FORWARD_ALIGN_TIME
                ):
                    # 180도 회전 시작 방향 저장
                    self.rotate_start_yaw = self.current_yaw

                    self.target_yaw = self.normalize_angle(
                        self.rotate_start_yaw
                        + math.radians(
                            self.FINAL_ROTATE_ANGLE
                            + self.FINAL_ROTATE_OFFSET
                        )
                    )

                    self.rotate_start_time = now
                    self.rotate_settle_start = None

                    self.change_state("ROTATE_180")

                    self.get_logger().info(
                        "FORWARD ADJUST COMPLETE "
                        "-> ROTATE 180"
                    )

            else:
                self.align_start_time = None

                # 양수: 전진
                # 음수: 후진
                speed = min(
                    self.LINEAR_SPEED,
                    max(
                        self.MIN_LINEAR,
                        abs(distance_error)
                        * self.KP_LINEAR
                    )
                )

                cmd.linear.x = math.copysign(
                    speed,
                    distance_error
                )

                cmd.angular.z = 0.0

            self.get_logger().info(
                f"State: {self.state} | "
                f"Forward: "
                f"{self.filtered_forward:.3f} m | "
                f"Error: {distance_error:+.3f} m | "
                f"Heading: {heading_error:+.2f} deg | "
                f"V: {cmd.linear.x:+.3f}",
                throttle_duration_sec=0.5
            )

        # =========================
        # 9. Odom 기반 180도 회전
        # =========================

        elif self.state == "ROTATE_180":

            if (
                now - self.rotate_start_time
                > self.ROTATE_TIMEOUT
            ):
                self.change_state("ABORTED")
                self.get_logger().warning(
                    "180 ROTATION TIMEOUT | STOP"
                )
                self.cmd_pub.publish(cmd)
                return

            yaw_error = self.normalize_angle(
                self.target_yaw - self.current_yaw
            )

            cmd.linear.x = 0.0

            if (
                abs(math.degrees(yaw_error))
                <= self.FINAL_ROTATE_TOLERANCE
            ):
                cmd.angular.z = 0.0

                if self.rotate_settle_start is None:
                    self.rotate_settle_start = now

                elif (
                    now - self.rotate_settle_start
                    >= self.ROTATE_SETTLE_TIME
                ):
                    self.reverse_start_x = self.current_x
                    self.reverse_start_y = self.current_y

                    self.reverse_start_yaw = self.target_yaw
                    self.reverse_start_time = now

                    if self.REVERSE_ENABLED:

                        self.change_state(
                            "REVERSE_PARKING"
                        )

                    else:

                        self.change_state("HOLDING")

                        self.get_logger().warning(
                            "REVERSE DISABLED | STOP"
                        )

                    self.get_logger().info(
                        "180 DEG ROTATION COMPLETE"
                    )

            else:
                self.rotate_settle_start = None

                # 180도 부근에서 회전 방향이
                # 갑자기 반전되는 현상 방지
                if yaw_error < -math.pi / 2:
                    yaw_error += 2.0 * math.pi

                cmd.angular.z = self.calculate_angular(
                    yaw_error,
                    self.KP_ROTATE
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Remaining: "
                f"{math.degrees(yaw_error):+.2f} deg | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )

        # =========================
        # 10. Odom 기반 0.5m 후진
        # =========================

        elif self.state == "REVERSE_PARKING":

            if (
                not self.REVERSE_ENABLED
                or now - self.reverse_start_time
                > self.REVERSE_TIMEOUT
            ):
                self.change_state("ABORTED")
                self.get_logger().warning(
                    "REVERSE ABORTED | STOP"
                )
                self.cmd_pub.publish(cmd)
                return

            dx = self.current_x - self.reverse_start_x
            dy = self.current_y - self.reverse_start_y

            # 후진 방향으로 이동한 거리
            moved_distance = -(
                math.cos(self.reverse_start_yaw) * dx
                + math.sin(self.reverse_start_yaw) * dy
            )

            remaining_distance = (
                self.REVERSE_DISTANCE
                - moved_distance
            )

            yaw_error = self.normalize_angle(
                self.reverse_start_yaw
                - self.current_yaw
            )

            # 방향 오차가 너무 크면 정지
            if abs(math.degrees(yaw_error)) > 10.0:

                self.change_state("ABORTED")

                self.get_logger().warning(
                    "REVERSE HEADING ERROR | STOP"
                )

                self.cmd_pub.publish(cmd)
                return

            # 목표 거리 도달
            if (
                remaining_distance
                <= self.MOVE_TOLERANCE
            ):
                cmd.linear.x = 0.0
                cmd.angular.z = 0.0

                self.change_state("HOLDING")

                self.get_logger().info(
                    f"PARKING COMPLETE | "
                    f"Reversed: {moved_distance:.3f} m"
                )

            else:

                # 후진 및 목표 지점 감속
                cmd.linear.x = -min(
                    self.REVERSE_SPEED,
                    max(
                        self.MIN_LINEAR,
                        self.KP_LINEAR
                        * remaining_distance
                    )
                )

                # 후진 중 방향 유지
                cmd.angular.z = max(
                    -0.15,
                    min(
                        0.15,
                        self.KP_MOVE_HEADING * yaw_error
                    )
                )

            self.get_logger().info(
                f"State: {self.state} | "
                f"Target: {self.REVERSE_DISTANCE:.3f} m | "
                f"Moved: {moved_distance:.3f} m | "
                f"Remaining: {remaining_distance:.3f} m | "
                f"V: {cmd.linear.x:+.3f} | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )

        # =========================
        # 속도 명령 발행
        # =========================

        self.cmd_pub.publish(cmd)

        if self.state == "HEADING_ALIGN":

            self.get_logger().info(
                f"State: {self.state} | "
                f"Heading: {self.heading:+.2f} deg | "
                f"Filtered: "
                f"{self.filtered_heading:+.2f} deg | "
                f"Lateral: "
                f"{self.filtered_lateral:+.3f} m | "
                f"W: {cmd.angular.z:+.3f}",
                throttle_duration_sec=0.5
            )

    # =========================
    # 로봇 정지
    # =========================

    def stop_robot(self):

        self.cmd_pub.publish(Twist())


def main(args=None):

    rclpy.init(
        args=args,
        signal_handler_options=SignalHandlerOptions.NO
    )

    node = ArucoParking()

    try:

        rclpy.spin(node)

    except KeyboardInterrupt:

        pass

    finally:

        if rclpy.ok():

            for _ in range(3):

                node.stop_robot()
                time.sleep(0.05)

        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
