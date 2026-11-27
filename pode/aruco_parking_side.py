
import math
import threading
import queue
import cv2
import numpy as np
from std_msgs.msg import Int32
import time

import rclpy

from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions

from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data


# Camera settings (same raw orientation as calibration)
CAMERA_MATRIX_PATH = "/home/ane/pinky_pro/aruco_calibration/camera_params.npz"
MARKER_IDS = (1,)
MARKER_LENGTH = 0.03
CAMERA_PORT = 4000
SHOW_VIDEO = True
GST_PIPELINE = (
    f'udpsrc port={CAMERA_PORT} '
    'caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! '
    'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
    'video/x-raw,format=BGR,width=640,height=480 ! '
    'appsink max-buffers=1 drop=true sync=false'
)


def rotation_to_quaternion(r):
    trace = np.trace(r)
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        qw, qx, qy, qz = .25*s, (r[2,1]-r[1,2])/s, (r[0,2]-r[2,0])/s, (r[1,0]-r[0,1])/s
    elif r[0,0] > r[1,1] and r[0,0] > r[2,2]:
        s = np.sqrt(1+r[0,0]-r[1,1]-r[2,2])*2
        qw, qx, qy, qz = (r[2,1]-r[1,2])/s, .25*s, (r[0,1]+r[1,0])/s, (r[0,2]+r[2,0])/s
    elif r[1,1] > r[2,2]:
        s = np.sqrt(1+r[1,1]-r[0,0]-r[2,2])*2
        qw, qx, qy, qz = (r[0,2]-r[2,0])/s, (r[0,1]+r[1,0])/s, .25*s, (r[1,2]+r[2,1])/s
    else:
        s = np.sqrt(1+r[2,2]-r[0,0]-r[1,1])*2
        qw, qx, qy, qz = (r[1,0]-r[0,1])/s, (r[0,2]+r[2,0])/s, (r[1,2]+r[2,1])/s, .25*s
    return tuple(map(float, (qx,qy,qz,qw)))


class CameraWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.results = queue.Queue(maxsize=1)
        self.error = None
        self.selected_id = 1

    def run(self):
        cap = None
        try:
            with np.load(CAMERA_MATRIX_PATH) as data:
                matrix = data['camera_matrix']
                dist = data['dist_coeffs']
            dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            parameters = cv2.aruco.DetectorParameters_create()
            half = MARKER_LENGTH / 2
            obj = np.array([[-half,half,0],[half,half,0],[half,-half,0],[-half,-half,0]], dtype=np.float32)
            cap = cv2.VideoCapture(GST_PIPELINE, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                raise RuntimeError("Camera stream open failed")
            while not self.stop_event.is_set():
                ret, frame = cap.read()
                captured = time.monotonic()
                if not ret:
                    self.stop_event.wait(0.03)
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary, parameters=parameters)
                pose = None
                labels = []
                if ids is not None:
                    cv2.aruco.drawDetectedMarkers(frame, corners, ids)
                    for i, marker_id in enumerate(ids.flatten()):
                        if marker_id not in MARKER_IDS or (self.selected_id is not None and marker_id != self.selected_id):
                            continue
                        success, rvec, tvec = cv2.solvePnP(obj, corners[i].reshape(4,2), matrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
                        if not success:
                            continue
                        x,y,z = map(float, tvec.ravel())
                        rotation, _ = cv2.Rodrigues(rvec)
                        normal = rotation[:,2]
                        if not np.isfinite([x,y,z,*rotation.ravel()]).all() or z <= 0 or normal[2] >= 0:
                            continue
                        pose = (int(marker_id),x,y,z,*rotation_to_quaternion(rotation))
                        heading = np.degrees(np.arctan2(normal[0], -normal[2]))
                        bearing = np.degrees(np.arctan2(-x,z))
                        labels = [f'ID: {marker_id}', f'Forward: {z:.3f}m', f'Lateral: {-x:+.3f}m', f'Heading: {heading:+.1f}deg', f'Bearing: {bearing:+.1f}deg']
                        cv2.drawFrameAxes(frame, matrix, dist, rvec, tvec, half)
                        break
                display = cv2.rotate(frame, cv2.ROTATE_180)
                for i, label in enumerate(labels):
                    cv2.putText(display, label, (20,35+30*i), cv2.FONT_HERSHEY_SIMPLEX, .65, (0,255,0), 2)
                # 최신 프레임만 전달하여 처리 지연 누적 방지
                try:
                    self.results.get_nowait()
                except queue.Empty:
                    pass
                self.results.put_nowait((captured, display, pose))
        except Exception as exc:
            self.error = f'Camera worker failed: {exc}'
        finally:
            if cap is not None:
                cap.release()


class ArucoParking(Node):
    def __init__(self):
        super().__init__('aruco_approach')
        self.TARGET_FORWARD = 0.10
        self.WALL_GAP = 0.05
        self.ODOM_TIMEOUT = 0.5
        self.ROTATE_TOL = math.radians(1.0)
        self.MOVE_TOL = 0.002
        self.phase = "APPROACH"
        self.odom = None
        self.odom_stamp = 0.0
        self.phase_start = 0.0
        self.settle = None
        self.odom_sub = self.create_subscription(Odometry, "/odom", self.odom_callback, qos_profile_sensor_data)
        self.FORWARD_TOL = 0.005
        self.LATERAL_TOL = 0.005
        self.MAX_LINEAR = 0.03
        self.MAX_ANGULAR = 0.15
        self.MARKER_TIMEOUT = 0.3
        self.SEARCH_SPEED = 0.10
        self.SEARCH_FIRST_TIME = 3.0
        self.SEARCH_REVERSE_TIME = 6.0
        self.FRAME_TIMEOUT = 0.3
        self.last_direction = 1.0
        self.seen_marker = False
        self.last_forward = None
        self.search_start = None
        self.search_failed = False
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.camera = CameraWorker()
        self.camera.start()
        self.pose = None
        self.stamp = 0.0
        self.stable_since = None
        self.done = False
        self.timer = self.create_timer(0.05, self.control)

    def stop_robot(self):
        self.cmd_pub.publish(Twist())

    @staticmethod
    def wrap(a):
        return math.atan2(math.sin(a), math.cos(a))

    def odom_callback(self, msg):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        if not all(math.isfinite(v) for v in (p.x,p.y,q.x,q.y,q.z,q.w)):
            return
        n = math.sqrt(q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w)
        if n < 1e-6:
            return
        x,y,z,w = q.x/n,q.y/n,q.z/n,q.w/n
        yaw = math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
        self.odom = (p.x,p.y,yaw)
        self.odom_stamp = time.monotonic()

    def abort(self, reason):
        self.done = True
        self.stop_robot()
        self.get_logger().warn(reason)

    def enter(self, phase, now):
        self.phase = phase
        self.phase_start = now
        self.settle = None
        self.stop_robot()
        self.get_logger().info(phase)

    def begin_side(self, now):
        if self.odom is None or now-self.odom_stamp > self.ODOM_TIMEOUT:
            self.abort('ODOM UNAVAILABLE | STOP')
            return
        _,x,_,f,qx,qy,qz,qw = self.pose
        h = math.atan2(2*(qx*qz+qw*qy), -(1-2*(qx*qx+qy*qy)))
        self.saved_forward = f
        self.saved_heading = h
        self.wall_move = f*math.cos(h)-self.WALL_GAP
        self.side_move = abs(f*math.sin(h)) + 0.03
        if self.wall_move < 0:
            self.abort('WALL DISTANCE TOO SMALL | NO REVERSE')
            return
        # Positive heading means clockwise alignment; marker then lies left.
        self.side_sign = 1.0 if h >= 0 else -1.0
        self.wall_yaw = self.wrap(self.odom[2]-h)
        self.side_yaw = self.wrap(self.wall_yaw+self.side_sign*math.pi/2)
        self.get_logger().info(f'F={f:.3f} H={math.degrees(h):+.2f} WALL MOVE={self.wall_move:.3f} SIDE MOVE={self.side_move:.3f}')
        self.enter('ALIGN_WALL', now)

    def side_control(self, now):
        if self.odom is None or now-self.odom_stamp > self.ODOM_TIMEOUT:
            self.abort('ODOM UNAVAILABLE | STOP')
            return
        if now-self.phase_start > 40:
            self.abort('SIDE PARKING TIMEOUT | STOP')
            return
        x,y,yaw = self.odom
        cmd = Twist()
        if self.phase in ('ALIGN_WALL','TURN_SIDE'):
            target = self.wall_yaw if self.phase == 'ALIGN_WALL' else self.side_yaw
            error = self.wrap(target-yaw)
            if abs(error) <= self.ROTATE_TOL:
                if self.settle is None:
                    self.settle = now
                if now-self.settle >= 0.3:
                    self.move_start = (x,y)
                    self.enter('MOVE_WALL' if self.phase == 'ALIGN_WALL' else 'MOVE_SIDE',now)
                    return
            else:
                self.settle = None
                cmd.angular.z = math.copysign(min(0.15,max(0.04,abs(error)*0.8)),error)
        elif self.phase in ('MOVE_WALL','MOVE_SIDE'):
            wall = self.phase == 'MOVE_WALL'
            target = self.wall_yaw if wall else self.side_yaw
            distance = self.wall_move if wall else self.side_move
            dx,dy = x-self.move_start[0],y-self.move_start[1]
            remaining = distance-dx*math.cos(target)-dy*math.sin(target)
            error = self.wrap(target-yaw)
            if abs(error)>math.radians(10):
                self.abort('MOVE HEADING ERROR | STOP')
                return
            if remaining <= self.MOVE_TOL:
                if wall:
                    self.enter('TURN_SIDE',now)
                else:
                    self.done = True
                    self.stop_robot()
                    self.get_logger().info('SIDE PARKING COMPLETE')
                return
            cmd.linear.x = min(0.03,max(0.008,remaining*0.7))
            cmd.angular.z = max(-0.10,min(0.10,error))
        self.cmd_pub.publish(cmd)

    def control(self):
        now = time.monotonic()
        try:
            stamp, frame, pose = self.camera.results.get_nowait()
            self.stamp, self.pose = stamp, pose
            if SHOW_VIDEO:
                cv2.imshow('ArUco approach', frame)
        except queue.Empty:
            pass
        if SHOW_VIDEO and cv2.waitKey(1) & 0xff == ord('q'):
            self.done = True
        if self.camera.error:
            self.done = True
            self.get_logger().error(self.camera.error, throttle_duration_sec=2.0)
        if self.done:
            self.stop_robot()
            return
        if self.phase != 'APPROACH':
            self.side_control(now)
            return
        if now - self.stamp > self.FRAME_TIMEOUT:
            self.stable_since = None
            self.stop_robot()
            # Never search blindly if the video stream itself is stale.
            if self.search_start is not None:
                self.search_failed = True
            return
        if self.pose is None:
            self.stable_since = None
            if not self.seen_marker or self.search_failed or self.last_forward is None or self.last_forward <= self.TARGET_FORWARD + self.FORWARD_TOL:
                self.stop_robot()
                return
            if self.search_start is None:
                self.search_start = now
                self.stop_robot()
                return
            elapsed = now - self.search_start
            cmd = Twist()
            if elapsed < self.SEARCH_FIRST_TIME:
                cmd.angular.z = self.last_direction * self.SEARCH_SPEED
            elif elapsed < self.SEARCH_FIRST_TIME + self.SEARCH_REVERSE_TIME:
                cmd.angular.z = -self.last_direction * self.SEARCH_SPEED
            else:
                self.search_failed = True
                self.get_logger().warn('SEARCH TIMEOUT | STOP')
            self.cmd_pub.publish(cmd)
            return
        if self.search_start is not None:
            self.get_logger().info('ID 1 REACQUIRED | RESUME')
        self.search_start = None
        self.search_failed = False
        self.seen_marker = True
        marker_id, x, y, z, *_ = self.pose
        if self.camera.selected_id is None:
            self.camera.selected_id = marker_id
        if marker_id != self.camera.selected_id:
            self.stop_robot()
            return
        self.last_forward = z
        lateral = -x
        error = z - self.TARGET_FORWARD
        bearing = math.atan2(lateral, z)
        if abs(bearing) > math.radians(1):
            self.last_direction = -1.0 if bearing > 0 else 1.0
        cmd = Twist()
        if abs(error) <= self.FORWARD_TOL and abs(lateral) <= self.LATERAL_TOL:
            if self.stable_since is None:
                self.stable_since = now
            if now - self.stable_since >= 0.5:
                self.begin_side(now)
                return
        else:
            self.stable_since = None
            if abs(lateral) > self.LATERAL_TOL:
                cmd.angular.z = max(-self.MAX_ANGULAR, min(self.MAX_ANGULAR, -1.0 * bearing))
            if abs(bearing) < math.radians(12) and error > self.FORWARD_TOL:
                cmd.linear.x = min(self.MAX_LINEAR, 0.7 * error)
            elif error < -self.FORWARD_TOL:
                # No automatic reverse: rear clearance is not verified.
                cmd = Twist()
                self.get_logger().warn('TOO CLOSE | reposition manually', throttle_duration_sec=1.0)
        self.cmd_pub.publish(cmd)
        self.get_logger().info(f'ID {marker_id} | Forward: {z:.3f} | Lateral: {lateral:+.3f} | V: {cmd.linear.x:.3f} | W: {cmd.angular.z:+.3f}', throttle_duration_sec=0.5)

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

        node.camera.stop_event.set()
        node.camera.join(timeout=1.0)
        if SHOW_VIDEO:
            cv2.destroyAllWindows()
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
