import sys
import math
import time

# ==========================================
# 1. GStreamer 지원 시스템 OpenCV 로드
# ==========================================

sys.path.insert(0, '/usr/lib/python3/dist-packages')
import cv2
sys.path.remove('/usr/lib/python3/dist-packages')

# ==========================================
# 2. 가상환경 패키지 로드
# ==========================================

import torch
import rclpy

from ultralytics import YOLO
from rclpy.node import Node
from std_msgs.msg import Float32
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import PointStamped, Twist
from rclpy.qos import qos_profile_sensor_data


# ==========================================
# 3. 설정
# ==========================================

PORT = 4000
CAMERA_HFOV_DEG = 62.0
MODEL_PATH = "/home/ane/pinky_pro/amr/runs/yolo11n_person_amr_cart/weights/best.pt"

YOLO_IMAGE_SIZE = 640
YOLO_CONFIDENCE = 0.5

# 카메라가 실제로 보는 전방 영역만 LiDAR 후보로 사용
CAMERA_HALF_FOV_DEG = CAMERA_HFOV_DEG / 2.0

# LiDAR 군집화 설정
CLUSTER_DISTANCE_THRESHOLD = 0.15  # 인접 포인트 간 거리 차이(m)
CLUSTER_MIN_POINTS = 2
CLUSTER_MAX_GAP_DEG = 1.5          # 군집 내 인접 측정점의 최대 각도 간격

# YOLO와 LiDAR 장애물 방향 매칭 시 Bounding Box 바깥 허용 각도
YOLO_MATCH_MARGIN_DEG = 3.0

# 센서 데이터 유효시간
DETECTION_TIMEOUT = 1.0
SCAN_TIMEOUT = 1.0

# 시간적 안정화 설정
EMA_ALPHA = 0.3

# 타깃 연속성 유지 설정
TRACKING_ANGLE_THRESHOLD_DEG = 8.0
TRACKING_DISTANCE_THRESHOLD = 0.30

# 새로운 장애물이 기존 타깃보다 충분히 가까우면 타깃 변경
TARGET_SWITCH_DISTANCE_MARGIN = 0.07

# 장애물 회피 설정
LINEAR_SPEED = 0.05
AVOID_ANGULAR_SPEED = 0.35
AVOID_DISTANCE = 0.15       # 이 거리보다 가까우면 회피 시작
RELEASE_DISTANCE = 0.20    # 이 거리 이상이면 회피 종료

# 사람 정지 거리 (m)
PERSON_STOP_DISTANCE = 0.20

device = 0 if torch.cuda.is_available() else "cpu"

print("OpenCV:", cv2.__version__)
print("GStreamer:", cv2.videoio_registry.hasBackend(cv2.CAP_GSTREAMER))
print("YOLO Device:", device)


def normalize_angle(angle):
    """각도를 [-pi, pi] 범위로 정규화."""
    return math.atan2(math.sin(angle), math.cos(angle))


def robot_to_lidar_angle(robot_angle):
    """
    로봇 기준 각도 -> LiDAR 원본 각도.
    """
    return normalize_angle(-robot_angle - math.pi)


def lidar_to_robot_angle(lidar_angle):
    """LiDAR 원본 각도 -> 로봇 기준 각도.
    전방 0도, 오른쪽(시계 방향) 양수, 왼쪽 음수.
    """
    return normalize_angle(-(lidar_angle + math.pi))


def camera_x_to_angle(x_pixel, image_width):
    """영상의 x 픽셀 위치를 카메라 중심 기준 각도로 변환."""
    normalized_x = (2.0 * x_pixel / image_width) - 1.0
    return math.degrees(
        math.atan(
            normalized_x * math.tan(math.radians(CAMERA_HFOV_DEG / 2.0))
        )
    )


class SensorFusion(Node):

    def __init__(self):
        super().__init__('sensor_fusion')

        # ==================================
        # 센서 데이터
        # ==================================

        self.scan_data = None
        self.last_scan_time = 0.0
        self.target_valid = False

        # EMA 필터 상태
        self.filtered_angle = None
        self.filtered_distance = None

        # 타깃 추적 상태
        self.previous_target_angle = None
        self.previous_target_distance = None

        # 사람 정지
        self.person_stop_active = False

        # ==================================
        # ROS 2 구독
        # ==================================

        self.scan_sub = self.create_subscription(
            LaserScan,
            '/scan',
            self.scan_callback,
            qos_profile_sensor_data
        )

        # ==================================
        # ROS 2 발행
        # 기존 토픽 이름을 유지해 기존 구독 코드와의 호환성 보존
        # /yolo/target_angle에는 최종 선택된 장애물의 로봇 기준 각도를 발행
        # ==================================

        self.angle_pub = self.create_publisher(
            Float32,
            '/yolo/target_angle',
            10
        )

        self.distance_pub = self.create_publisher(
            Float32,
            '/sensor_fusion/target_distance',
            10
        )

        self.point_pub = self.create_publisher(
            PointStamped,
            '/sensor_fusion/target_point',
            10
        )

        # /cmd_vel 발행 및 회피 상태
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.avoid_state = 'FORWARD'
        self.avoid_direction = 0.0

        # person 감지 시 최우선 정지 상태
        self.person_stop_active = False
        self.last_person_log_time = 0.0

        self.get_logger().info(
            'LiDAR-first Sensor Fusion + Obstacle Avoidance Started'
        )

    # ======================================
    # /cmd_vel 속도 명령 및 장애물 회피 제어
    # ======================================

    def publish_cmd(self, linear, angular):
        cmd = Twist()
        cmd.linear.x = float(linear)
        cmd.angular.z = float(angular)
        self.cmd_pub.publish(cmd)

    def control_obstacle(self, angle, distance):
        # 로봇 기준: 왼쪽 음수, 오른쪽 양수
        if self.avoid_state == 'FORWARD':
            if 0.0 < distance < AVOID_DISTANCE:
                # 왼쪽 장애물은 오른쪽 회전, 오른쪽 장애물은 왼쪽 회전
                self.avoid_direction = -1.0 if angle < 0.0 else 1.0
                self.avoid_state = 'AVOID'

                direction_name = 'LEFT' if self.avoid_direction > 0.0 else 'RIGHT'
                self.get_logger().warn(
                    f'[AVOID START] Angle: {math.degrees(angle):.1f} deg | '
                    f'Distance: {distance:.2f} m | Turn: {direction_name}'
                )

        elif self.avoid_state == 'AVOID':
            if distance >= RELEASE_DISTANCE:
                self.avoid_state = 'FORWARD'
                self.avoid_direction = 0.0
                self.get_logger().info('[AVOID END] Obstacle is far enough')

        if self.avoid_state == 'AVOID':
            self.publish_cmd(0.0, AVOID_ANGULAR_SPEED * self.avoid_direction)
        else:
            self.publish_cmd(LINEAR_SPEED, 0.0)

    def obstacle_lost(self):
        # 현재 전방 카메라 시야(+/-31도)에서 장애물이 사라진 경우
        if self.avoid_state == 'AVOID':
            self.get_logger().info(
                '[AVOID END] Obstacle no longer detected in front FOV'
            )
        self.avoid_state = 'FORWARD'
        self.avoid_direction = 0.0
        self.publish_cmd(LINEAR_SPEED, 0.0)

    # ======================================
    # LiDAR 데이터 수신
    # ======================================

    def scan_callback(self, msg):
        self.scan_data = msg
        self.last_scan_time = time.monotonic()

    # ======================================
    # 잘못된 거리 데이터 발행
    # ======================================

    def publish_invalid_distance(self):
        if not self.target_valid:
            return

        msg = Float32()
        msg.data = 0.0
        self.distance_pub.publish(msg)

        # EMA 필터 초기화
        self.filtered_angle = None
        self.filtered_distance = None

        # 타깃 추적 정보 초기화
        self.previous_target_angle = None
        self.previous_target_distance = None

        self.target_valid = False

        self.get_logger().warn(
            'LiDAR Target Invalid (distance = 0.0)'
        )

    # ======================================
    # 1) LiDAR 전체 스캔에서 전방 장애물 군집 생성
    # ======================================

    def extract_lidar_clusters(self, scan):
        if not scan.ranges or scan.angle_increment == 0.0:
            return []

        points = []

        for i, distance in enumerate(scan.ranges):
            if not math.isfinite(distance):
                continue

            if distance < scan.range_min or distance > scan.range_max:
                continue

            raw_angle = scan.angle_min + i * scan.angle_increment
            robot_angle = lidar_to_robot_angle(raw_angle)
            robot_angle_deg = math.degrees(robot_angle)

            # 카메라 시야각 안쪽의 전방 장애물만 후보로 사용
            if abs(robot_angle_deg) > CAMERA_HALF_FOV_DEG:
                continue

            points.append({
                'raw_angle': raw_angle,
                'robot_angle': robot_angle,
                'distance': float(distance)
            })

        if not points:
            return []

        # 로봇 기준 각도 순으로 정렬: 왼쪽(-) -> 오른쪽(+)
        points.sort(key=lambda p: p['robot_angle'])

        clusters = []
        current_cluster = [points[0]]

        for point in points[1:]:
            previous = current_cluster[-1]

            range_difference = abs(
                point['distance'] - previous['distance']
            )
            angle_gap_deg = abs(math.degrees(
                point['robot_angle'] - previous['robot_angle']
            ))

            if (
                range_difference <= CLUSTER_DISTANCE_THRESHOLD
                and angle_gap_deg <= CLUSTER_MAX_GAP_DEG
            ):
                current_cluster.append(point)
            else:
                if len(current_cluster) >= CLUSTER_MIN_POINTS:
                    clusters.append(current_cluster)
                current_cluster = [point]

        if len(current_cluster) >= CLUSTER_MIN_POINTS:
            clusters.append(current_cluster)

        return clusters

    # ======================================
    # 2) 군집 중심 각도 / 거리 계산
    # ======================================

    @staticmethod
    def get_cluster_info(cluster):
        angle = math.atan2(
            sum(math.sin(p['robot_angle']) for p in cluster),
            sum(math.cos(p['robot_angle']) for p in cluster)
        )

        # 장애물 표면까지의 보수적인 거리: 군집 내 최소 거리
        distance = min(p['distance'] for p in cluster)

        return angle, distance

    # ======================================
    # 3) LiDAR 최단거리 장애물과 YOLO 결과 매칭
    # ======================================

    def apply_temporal_filter(self, current_angle, current_distance):

        # 최초 측정이면 현재값으로 초기화
        if self.filtered_angle is None:
            self.filtered_angle = current_angle
            self.filtered_distance = current_distance

        else:
            # 각도 EMA (원형 각도 계산)
            alpha = EMA_ALPHA

            sin_angle = (
                (1 - alpha) * math.sin(self.filtered_angle)
                + alpha * math.sin(current_angle)
            )

            cos_angle = (
                (1 - alpha) * math.cos(self.filtered_angle)
                + alpha * math.cos(current_angle)
            )

            self.filtered_angle = math.atan2(
                sin_angle,
                cos_angle
            )

            # 거리 필터
            # 장애물이 가까워지면 즉시 반영
            if current_distance < self.filtered_distance:
                self.filtered_distance = current_distance

            # 장애물이 멀어지면 EMA 적용
            else:
                self.filtered_distance = (
                    alpha * current_distance
                    + (1 - alpha) * self.filtered_distance
                )

        return self.filtered_angle, self.filtered_distance

    # ======================================
    # 타깃 연속성 판단
    # ======================================

    def is_same_target(self, current_angle, current_distance):

        # 이전 타깃이 없으면 새로운 타깃으로 판단
        if (
            self.previous_target_angle is None
            or self.previous_target_distance is None
        ):
            return False

        previous_angle_deg = math.degrees(
            self.previous_target_angle
        )

        current_angle_deg = math.degrees(
            current_angle
        )

        # 각도 차이 계산
        angle_difference = abs(
            math.degrees(
                normalize_angle(
                    current_angle - self.previous_target_angle
                )
            )
        )

        # 거리 차이 계산
        distance_difference = abs(
            current_distance - self.previous_target_distance
        )

        # 각도와 거리 모두 허용 범위 안이면 동일 타깃
        return (
            angle_difference <= TRACKING_ANGLE_THRESHOLD_DEG
            and distance_difference <= TRACKING_DISTANCE_THRESHOLD
        )

    def match_yolo_detection(self, obstacle_angle, detections):
        """
        LiDAR가 선정한 장애물 각도가 YOLO Bounding Box의 각도 범위에
        들어가는지 확인한다. 매칭 실패 시 None을 반환한다.
        """
        obstacle_angle_deg = math.degrees(obstacle_angle)
        margin = YOLO_MATCH_MARGIN_DEG

        matched = []

        for detection in detections:
            left_angle = detection['left_angle']
            right_angle = detection['right_angle']

            if (
                left_angle - margin
                <= obstacle_angle_deg
                <= right_angle + margin
            ):
                matched.append(detection)

        if not matched:
            return None

        # 겹치는 박스가 여러 개면 confidence가 가장 높은 탐지 결과 사용
        return max(matched, key=lambda d: d['confidence'])

    # ======================================
    # 4) 전체 센서 퓨전 처리
    # ======================================

    def process_fusion(self, detections):
        now = time.monotonic()

        # ==================================
        # YOLO 탐지 데이터 유효성 확인
        # ==================================
        detection_fresh = (
            len(detections) > 0
            and self.last_detection_time is not None
            and now - self.last_detection_time <= DETECTION_TIMEOUT
        )

        

        # LiDAR 데이터가 없거나 오래된 경우
        if (
            self.scan_data is None
            or now - self.last_scan_time > SCAN_TIMEOUT
        ):
            self.publish_invalid_distance()
            # LiDAR 데이터가 오래되면 안전을 위해 정지
            self.publish_cmd(0.0, 0.0)
            return

        scan = self.scan_data
        clusters = self.extract_lidar_clusters(scan)

        if not clusters:
            self.publish_invalid_distance()
            # 최신 스캔에서 전방 장애물이 보이지 않으면 직진
            self.obstacle_lost()
            return

        # 각 군집의 거리와 방향 계산
        obstacle_candidates = []

        for cluster in clusters:
            cluster_angle, cluster_distance = self.get_cluster_info(cluster)

            obstacle_candidates.append({
                'cluster': cluster,
                'angle': cluster_angle,
                'distance': cluster_distance
            })

        # ==================================
        # LiDAR + YOLO 사람 감지 거리 확인
        # ==================================

        person_obstacle = None

        if detection_fresh:

            for candidate in obstacle_candidates:

                # LiDAR 장애물 방향과 YOLO 객체 방향 비교
                matched_detection = self.match_yolo_detection(
                    candidate['angle'],
                    detections
                )

                # 같은 방향에 사람이 있고 거리가 20cm 이하인지 확인
                if (
                    matched_detection is not None
                    and matched_detection['class_name'].strip().lower() == 'person'
                    and candidate['distance'] <= PERSON_STOP_DISTANCE
                ):
                    person_obstacle = (candidate, matched_detection)
                    break


        # ==================================
        # 사람 정지 처리
        # ==================================

        if person_obstacle is not None:

            candidate, person_detection = person_obstacle

            self.person_stop_active = True

            # 로봇 즉시 정지
            self.publish_cmd(0.0, 0.0)

            # 1초 간격으로 로그 출력
            if now - self.last_person_log_time >= 1.0:

                self.get_logger().warn(
                    f'[PERSON STOP] '
                    f'Distance: {candidate["distance"]:.2f} m | '
                    f'Angle: {math.degrees(candidate["angle"]):.1f} deg | '
                    f'Confidence: {person_detection["confidence"]:.2f}'
                )

                self.last_person_log_time = now

            return


        # 사람 정지 조건 해제
        if self.person_stop_active:

            self.person_stop_active = False

            self.get_logger().info(
                '[PERSON CLEARED] Resume obstacle avoidance'
            )

        # ==================================
        # 타깃 연속성 기반 장애물 선택
        # ==================================

        # 가장 가까운 장애물
        nearest_obstacle = min(
            obstacle_candidates,
            key=lambda item: item['distance']
        )

        selected_obstacle = nearest_obstacle

        # 이전 타깃이 존재하면 연속성 확인
        if self.previous_target_angle is not None:

            previous_target = min(
                obstacle_candidates,
                key=lambda item: abs(
                    math.degrees(
                        normalize_angle(
                            item['angle'] - self.previous_target_angle
                        )
                    )
                )
            )

            angle_difference = abs(
                math.degrees(
                    normalize_angle(
                        previous_target['angle'] - self.previous_target_angle
                    )
                )
            )

            distance_difference = abs(
                previous_target['distance'] - self.previous_target_distance
            )

            same_target = (
                angle_difference <= TRACKING_ANGLE_THRESHOLD_DEG
                and distance_difference <= TRACKING_DISTANCE_THRESHOLD
            )

            if same_target:

                # 기존 타깃을 유지하되,
                # 새로운 장애물이 충분히 가까우면 타깃 전환
                if (
                    nearest_obstacle['distance']
                    < previous_target['distance'] - TARGET_SWITCH_DISTANCE_MARGIN
                ):
                    selected_obstacle = nearest_obstacle
                else:
                    selected_obstacle = previous_target

        # 선택된 장애물의 각도와 거리 가져오기
        obstacle_angle = selected_obstacle['angle']
        obstacle_distance = selected_obstacle['distance']

        # ==================================
        # 타깃 전환 여부 확인
        # ==================================

        target_switched = False

        if self.previous_target_angle is not None:

            angle_difference = abs(
                math.degrees(
                    normalize_angle(
                        obstacle_angle - self.previous_target_angle
                    )
                )
            )

            distance_difference = abs(
                obstacle_distance - self.previous_target_distance
            )

            if (
                angle_difference > TRACKING_ANGLE_THRESHOLD_DEG
                or distance_difference > TRACKING_DISTANCE_THRESHOLD
            ):
                target_switched = True

        # 새로운 타깃이면 기존 EMA 초기화
        if target_switched:
            self.filtered_angle = None
            self.filtered_distance = None

            self.get_logger().info(
                'Target Switched - EMA Reset'
            )

        # 시간적 안정화 적용
        obstacle_angle, obstacle_distance = self.apply_temporal_filter(
            obstacle_angle,
            obstacle_distance
        )

        # ==================================
        # 이전 타깃 정보 갱신
        # ==================================

        self.previous_target_angle = obstacle_angle
        self.previous_target_distance = obstacle_distance

        # YOLO 탐지 결과가 오래되었으면 객체 분류 없이 LiDAR 장애물로 처리
        matched_detection = None
        if detection_fresh:
            matched_detection = self.match_yolo_detection(
                obstacle_angle,
                detections
            )

        if matched_detection is not None:
            object_name = matched_detection['class_name']
            confidence = matched_detection['confidence']
            yolo_status = f'{object_name} ({confidence:.2f})'
        else:
            object_name = 'Unknown'
            yolo_status = 'Unclassified obstacle'

        # 최종 타깃 각도 발행: 로봇 기준 전방 0도, 오른쪽 양수
        angle_msg = Float32()
        angle_msg.data = float(math.degrees(obstacle_angle))
        self.angle_pub.publish(angle_msg)

        # 거리 발행
        distance_msg = Float32()
        distance_msg.data = float(obstacle_distance)
        self.distance_pub.publish(distance_msg)

        # 로봇 기준 좌표 계산
        point = PointStamped()
        point.header.stamp = self.get_clock().now().to_msg()
        point.header.frame_id = scan.header.frame_id

        # PointStamped는 scan.header.frame_id(LiDAR 프레임)이므로
        # 로봇 기준 각도가 아닌 LiDAR 원본 각도로 좌표를 계산
        obstacle_lidar_angle = robot_to_lidar_angle(obstacle_angle)
        point.point.x = obstacle_distance * math.cos(obstacle_lidar_angle)
        point.point.y = obstacle_distance * math.sin(obstacle_lidar_angle)
        point.point.z = 0.0

        self.point_pub.publish(point)
        self.target_valid = True

        # YOLO 클래스와 관계없이 LiDAR 장애물의 각도/거리로 회피
        self.control_obstacle(obstacle_angle, obstacle_distance)

        self.get_logger().info(
            f'Nearest LiDAR Obstacle | '
            f'Angle: {math.degrees(obstacle_angle):.1f} deg | '
            f'Distance: {obstacle_distance:.2f} m | '
            f'YOLO: {yolo_status} | '
            f'Cluster Points: {len(selected_obstacle["cluster"])}'
        )


# ==========================================
# 5. 메인 실행
# ==========================================

def main():
    rclpy.init()
    node = SensorFusion()

    cap = None
    model = None

    # 가장 최근 YOLO 탐지 결과 및 탐지 시각
    detections = []
    node.last_detection_time = None

    try:
        # YOLO 모델 로드
        model = YOLO(MODEL_PATH)
        print("YOLO 모델 로드 완료")
        print("YOLO Model Path:", MODEL_PATH)
        print("YOLO Classes:", model.names)

        # GStreamer 영상 수신
        pipeline = (
            f'udpsrc port={PORT} '
            'caps="application/x-rtp,media=video,encoding-name=H264,'
            'payload=96,clock-rate=90000" ! '
            'rtpjitterbuffer latency=100 ! '
            'rtph264depay ! '
            'h264parse ! '
            'avdec_h264 ! '
            'videoconvert ! '
            'video/x-raw,format=BGR ! '
            'appsink drop=true max-buffers=1 sync=false'
        )

        cap = cv2.VideoCapture(
            pipeline,
            cv2.CAP_GSTREAMER
        )

        if not cap.isOpened():
            raise RuntimeError("GStreamer 영상 수신 실패")

        print("GStreamer 영상 수신 성공")
        print("LiDAR-first YOLO Sensor Fusion 시작")

        # ==================================
        # 실시간 처리 루프
        # ==================================

        while rclpy.ok():
            # ROS 2 LiDAR 콜백 처리
            rclpy.spin_once(node, timeout_sec=0)

            # 카메라 영상 수신
            ret, frame = cap.read()

            if ret:
                # 영상 180도 회전 보정
                frame = cv2.rotate(
                    frame,
                    cv2.ROTATE_180
                )

                height, width = frame.shape[:2]

                # YOLO 객체 탐지
                results = model.predict(
                    frame,
                    imgsz=YOLO_IMAGE_SIZE,
                    conf=YOLO_CONFIDENCE,
                    device=device,
                    verbose=False
                )

                result = results[0]
                display_frame = result.plot()

                # 모든 YOLO 탐지 결과를 저장
                detections = []

                if result.boxes is not None and len(result.boxes) > 0:
                    boxes_xyxy = result.boxes.xyxy.cpu().numpy()
                    confidences = result.boxes.conf.cpu().numpy()
                    class_ids = result.boxes.cls.cpu().numpy().astype(int)

                    for box, confidence, class_id in zip(
                        boxes_xyxy,
                        confidences,
                        class_ids
                    ):
                        x1, y1, x2, y2 = box

                        left_angle = camera_x_to_angle(x1, width)
                        right_angle = camera_x_to_angle(x2, width)

                        class_name = model.names.get(
                            int(class_id),
                            str(class_id)
                        )

                        detections.append({
                            'left_angle': min(left_angle, right_angle),
                            'right_angle': max(left_angle, right_angle),
                            'confidence': float(confidence),
                            'class_name': class_name
                        })

                    # YOLO 탐지 결과가 존재한 시각 갱신
                    node.last_detection_time = time.monotonic()

                # ==================================
                # LiDAR가 가장 가까운 장애물을 선정한 뒤
                # 해당 방향에 YOLO 탐지 객체가 있는지 확인
                # ==================================

                node.process_fusion(detections)

                cv2.imshow(
                    "LiDAR First + YOLO Confirmation",
                    display_frame
                )

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            else:
                # 영상이 잠시 끊겨도 LiDAR 콜백과 유효성 검사는 계속 처리
                node.process_fusion(detections)
                time.sleep(0.005)

    except KeyboardInterrupt:
        print("프로그램 종료")

    except Exception as e:
        print("오류:", e)

    finally:
        # 종료 시 로봇 정지
        node.publish_cmd(0.0, 0.0)

        if cap is not None:
            cap.release()

        cv2.destroyAllWindows()
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()