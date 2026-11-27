
import cv2
import numpy as np
import rclpy

from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Int32


# =========================
# 설정
# =========================

CAMERA_MATRIX_PATH = (
    "/home/ane/pinky_pro/aruco_calibration/camera_params.npz"
)

MARKER_ID = 2

# 실제 출력한 마커의 검은 테두리 바깥쪽 한 변 길이
MARKER_LENGTH = 0.03  # 30 mm = 0.03 m

CAMERA_PORT = 4000

GST_PIPELINE = (
    'udpsrc port=4000 '
    'caps="application/x-rtp,media=video,'
    'encoding-name=H264,payload=96" ! '
    'rtph264depay ! h264parse ! avdec_h264 ! '
    'videoconvert ! '
    'video/x-raw,format=BGR,width=640,height=480 ! '
    'appsink drop=true sync=false'
)


class ArucoDetector(Node):

    def __init__(self):
        super().__init__("aruco_detector")

        self.pose_pub = self.create_publisher(
            PoseStamped,
            "/aruco/marker_pose",
            10
        )

        self.id_pub = self.create_publisher(
            Int32,
            "/aruco/marker_id",
            10
        )

        # 카메라 보정값 불러오기
        data = np.load(CAMERA_MATRIX_PATH)

        self.camera_matrix = data["camera_matrix"]
        self.dist_coeffs = data["dist_coeffs"]

        self.dictionary = cv2.aruco.getPredefinedDictionary(
            cv2.aruco.DICT_4X4_50
        )

        # OpenCV 4.6 호환 legacy API
        self.parameters = cv2.aruco.DetectorParameters_create()

        self.cap = cv2.VideoCapture(
            GST_PIPELINE,
            cv2.CAP_GSTREAMER
        )

        if not self.cap.isOpened():
            raise RuntimeError("카메라 스트림을 열 수 없습니다.")

        self.timer = self.create_timer(0.03, self.process_frame)

        self.get_logger().info("ArUco detector started")
        self.get_logger().info(f"Target marker ID: {MARKER_ID}")

    def process_frame(self):

        ret, frame = self.cap.read()

        if not ret:
            self.get_logger().warning("Camera frame unavailable")
            return

        # 화면 표시용 영상은 미리 180도 회전
        display_frame = cv2.rotate(frame, cv2.ROTATE_180)

        # 중요:
        # 카메라 보정 당시와 동일한 방향의 원본 프레임으로
        # 검출 및 solvePnP를 수행한다.
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        corners, ids, rejected = cv2.aruco.detectMarkers(
            gray,
            self.dictionary,
            parameters=self.parameters
        )

        if ids is not None:

            cv2.aruco.drawDetectedMarkers(
                frame,
                corners,
                ids
            )

            for i, marker_id in enumerate(ids.flatten()):

                if marker_id != MARKER_ID:
                    continue

                obj_points = np.array([
                    [-MARKER_LENGTH / 2,  MARKER_LENGTH / 2, 0],
                    [ MARKER_LENGTH / 2,  MARKER_LENGTH / 2, 0],
                    [ MARKER_LENGTH / 2, -MARKER_LENGTH / 2, 0],
                    [-MARKER_LENGTH / 2, -MARKER_LENGTH / 2, 0]
                ], dtype=np.float32)

                image_points = corners[i].reshape(4, 2)

                success, rvec, tvec = cv2.solvePnP(
                    obj_points,
                    image_points,
                    self.camera_matrix,
                    self.dist_coeffs,
                    flags=cv2.SOLVEPNP_IPPE_SQUARE
                )

                if not success:
                    continue

                # 카메라 좌표계 기준 위치
                x = float(tvec[0][0])
                y = float(tvec[1][0])
                z = float(tvec[2][0])

                distance = float(np.linalg.norm(tvec))

                msg = PoseStamped()
                msg.header.stamp = self.get_clock().now().to_msg()

                # 아직 카메라 외부 파라미터를 로봇 좌표계에
                # 연결하지 않았으므로 camera_optical_frame으로 표시
                msg.header.frame_id = "camera_optical_frame"

                msg.pose.position.x = x
                msg.pose.position.y = y
                msg.pose.position.z = z

                # 회전값은 다음 단계에서 별도로 연결
                rotation, _ = cv2.Rodrigues(rvec)

                # 주차 정렬용 오차 계산
                forward_distance = z

                # 실제 로봇 기준 왼쪽 양수, 오른쪽 음수
                lateral_error = -x

                # 마커 중심과 로봇 정면 사이의 각도
                bearing_error = np.degrees(
                    np.arctan2(lateral_error, forward_distance)
                )

                # 마커의 방향 벡터
                normal = rotation[:, 2]

                # 정면에서 0도
                # 시계 방향 양수, 반시계 방향 음수
                # 원본 카메라 영상 180도 반전 고려
                heading_error = np.degrees(
                    np.arctan2(normal[0], -normal[2])
                )

                
                # OpenCV 회전행렬 -> Quaternion
                trace = np.trace(rotation)

                if trace > 0:
                    s = np.sqrt(trace + 1.0) * 2

                    qw = 0.25 * s
                    qx = (rotation[2, 1] - rotation[1, 2]) / s
                    qy = (rotation[0, 2] - rotation[2, 0]) / s
                    qz = (rotation[1, 0] - rotation[0, 1]) / s

                else:
                    if rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
                        s = np.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2

                        qw = (rotation[2, 1] - rotation[1, 2]) / s
                        qx = 0.25 * s
                        qy = (rotation[0, 1] + rotation[1, 0]) / s
                        qz = (rotation[0, 2] + rotation[2, 0]) / s

                    elif rotation[1, 1] > rotation[2, 2]:
                        s = np.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2

                        qw = (rotation[0, 2] - rotation[2, 0]) / s
                        qx = (rotation[0, 1] + rotation[1, 0]) / s
                        qy = 0.25 * s
                        qz = (rotation[1, 2] + rotation[2, 1]) / s

                    else:
                        s = np.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2

                        qw = (rotation[1, 0] - rotation[0, 1]) / s
                        qx = (rotation[0, 2] + rotation[2, 0]) / s
                        qy = (rotation[1, 2] + rotation[2, 1]) / s
                        qz = 0.25 * s

                msg.pose.orientation.x = float(qx)
                msg.pose.orientation.y = float(qy)
                msg.pose.orientation.z = float(qz)
                msg.pose.orientation.w = float(qw)


                self.pose_pub.publish(msg)

                id_msg = Int32()
                id_msg.data = int(marker_id)
                self.id_pub.publish(id_msg)

                cv2.drawFrameAxes(
                    frame,
                    self.camera_matrix,
                    self.dist_coeffs,
                    rvec,
                    tvec,
                    MARKER_LENGTH * 0.5
                )

                cv2.putText(
                    display_frame,
                    f"ID: {marker_id}",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    display_frame,
                    f"Forward: {forward_distance:.3f}m",
                    (20, 65),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    display_frame,
                    f"Lateral: {lateral_error:+.3f}m",
                    (20, 95),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    display_frame,
                    f"Heading: {heading_error:+.1f}deg",
                    (20, 125),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    display_frame,
                    f"Bearing: {bearing_error:+.1f}deg",
                    (20, 155),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

        

        cv2.imshow("ArUco Detector", display_frame)
        cv2.waitKey(1)

    def destroy_node(self):

        if hasattr(self, "cap"):
            self.cap.release()

        cv2.destroyAllWindows()

        super().destroy_node()


def main(args=None):

    rclpy.init(args=args)

    node = ArucoDetector()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
