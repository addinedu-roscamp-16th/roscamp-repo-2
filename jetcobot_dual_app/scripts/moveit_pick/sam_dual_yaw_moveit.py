#!/usr/bin/env python3
"""Manual ROI -> SAM2 -> LightGlue -> dual-arm automatic real MoveIt approach.

ROI selection automatically plans; 'E' requests physical movement when execution
is enabled. manual_roi_drag.py enables it by default; --plan-only disables it.
E approaches with the configured offset (default 40 mm);
First G previews the measured and target TCP Z; second G descends and closes.
L lifts and returns to see_center; O opens the gripper.
sam_center_moveit.py uses camera-centered approach.
Do not treat SDK final-goal execution as collision-checked path following. Needs ROS2 Jazzy and running move_group on robot control PC.
"""
import argparse
import subprocess
import signal
import json
from contextlib import nullcontext
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import cv2
from fine_roi_real import (roi_translation, target_pixel_for_world_xy,
                           update_direction_sign)
from move_right_sam_yaw import grasp_tcp_target_z_m
from auto_fine import AutoFineMixin
import numpy as np
import torch
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstVideo, GLib

try:
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import rbd
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'LightGlue'))
    from lightglue import LightGlue, SuperPoint
    from lightglue.utils import rbd

PANEL_W, PANEL_H = 640, 480
MAIN_WINDOW = 'SAM2 + LightGlue | ROI selection'
RESULT_WINDOW = 'SAM2 + LightGlue | 3D and grasp yaw preview'
TOP_WINDOW = 'Top-down grasp preview | right_base_link'
APPROACH_CAMERA_WINDOW = 'Approach camera | moving arm live'

# Both 640x480 cameras currently share one measured calibration file.
DEFAULT_CAMERA_CALIBRATION = (Path(__file__).resolve().parent /
    'calibration/camera_calibration.npz')
K = None
DIST = None


def load_camera_calibration(path):
    with np.load(path) as calibration:
        matrix = np.asarray(calibration['camera_matrix'], dtype=np.float64)
        distortion = np.asarray(calibration['dist_coeffs'], dtype=np.float64).reshape(-1)
        image_size = tuple(int(v) for v in calibration['image_size'])
    if matrix.shape != (3, 3) or distortion.size not in (4, 5, 8, 12, 14):
        raise ValueError('Invalid camera matrix or distortion coefficient shape')
    if not np.isfinite(matrix).all() or not np.isfinite(distortion).all():
        raise ValueError('Camera calibration has nonfinite values')
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or image_size != (640, 480):
        raise ValueError('Camera calibration must match 640x480 streams')
    return matrix, distortion

# Optical camera -> each arm's 6_Link, loaded separately at startup.
DEFAULT_HAND_EYE = (Path(__file__).resolve().parent /
    'calibration/T_gripper_camera.json')
DEFAULT_RIGHT_HAND_EYE = (Path(__file__).resolve().parent /
    'calibration/T_right_gripper_camera_20261117.json')
DEFAULT_STEREO_CORRECTION = (Path(__file__).resolve().parent /
    'calibration/stereo_see_center_correction_20261117.json')
T_GRIPPER_CAMERA_BY_SIDE = {}


def load_handeye(path, side):
    result = json.loads(path.read_text())
    gripper_frame = str(result.get('gripper_frame', ''))
    if result.get('translation_unit') != 'm' or gripper_frame not in (
            'real_robot/6_Link', f'{side}_6_Link'):
        raise ValueError(f'{side} hand-eye must describe meters and its 6_Link frame')
    transform = np.asarray(result['T_gripper_camera'], dtype=np.float64)
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError('Hand-eye transform must be a finite 4x4 matrix')
    if not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError('Hand-eye transform has an invalid final row')
    rotation = transform[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3) or abs(np.linalg.det(rotation)-1) > 1e-3:
        raise ValueError('Hand-eye rotation is not orthogonal')
    return transform, result

DEFAULT_URDF = (Path.home() /
    'jetcobot_moveit2_real_upload/test_urdf/urdf/jetcobot_dual_real.urdf')
DEFAULT_SAM_CKPT = (Path.home() /
    'sam2_src/checkpoints/sam2.1_hiera_small.pt')
DEFAULT_SAM_CFG = 'configs/sam2.1/sam2.1_hiera_s.yaml'

def rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def rot_y(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def fixed_6_to_tcp(urdf_path, side):
    """Fixed transform from an arm's 6_Link to its modeled TCP."""
    joints = {j.find('child').get('link'): j
              for j in ET.parse(urdf_path).getroot().findall('joint')}
    def fixed_chain(link, visited):
        if link == f'{side}_6_Link':
            return np.eye(4)
        if link in visited or link not in joints:
            raise ValueError(f'No fixed 6_Link to TCP chain for {side}: {link}')
        joint = joints[link]
        if joint.get('type') != 'fixed':
            raise ValueError(f'Unexpected movable TCP mounting joint: {joint.get("name")}')
        origin = joint.find('origin')
        xyz = [0., 0., 0.] if origin is None else list(map(float, origin.get('xyz', '0 0 0').split()))
        rpy = [0., 0., 0.] if origin is None else list(map(float, origin.get('rpy', '0 0 0').split()))
        T = np.eye(4)
        T[:3, :3] = rot_z(rpy[2]) @ rot_y(rpy[1]) @ rot_x(rpy[0])
        T[:3, 3] = xyz
        return fixed_chain(joint.find('parent').get('link'), visited | {link}) @ T
    return fixed_chain(f'{side}_TCP', set())


def camera_center_offset_tcp_xy(urdf_path, side, T_6_camera, above_m):
    """Target XY in TCP coordinates that puts it on this wrist camera's optical axis."""
    T_6_tcp = fixed_6_to_tcp(urdf_path, side)
    T_tcp_camera = np.linalg.inv(T_6_tcp) @ T_6_camera
    origin = T_tcp_camera[:3, 3]
    optical_axis = T_tcp_camera[:3, 2]
    if optical_axis[2] < 0.5:
        raise ValueError('Wrist camera is not aimed toward the TCP approach plane')
    distance = (above_m - origin[2]) / optical_axis[2]
    if distance <= 0:
        raise ValueError('Target plane is behind the wrist camera')
    offset = origin[:2] + distance * optical_axis[:2]
    if not np.isfinite(offset).all() or np.linalg.norm(offset) > 0.08:
        raise ValueError(f'Observation offset outside 8 cm bound: {offset}')
    return offset


def urdf_base_transforms(urdf_path):
    """Read fixed world->base chains; never assume unmeasured movable joints are zero."""
    root = ET.parse(urdf_path).getroot()
    parents = {j.find('child').get('link'): j for j in root.findall('joint')}

    def world_transform(link, visited=None):
        visited = set() if visited is None else visited
        if link in visited:
            raise ValueError(f'Cycle in URDF at {link}')
        if link == 'world':
            return np.eye(4)
        visited.add(link)
        if link not in parents:
            raise ValueError(f'No world chain for {link}')
        joint = parents[link]
        if joint.get('type') != 'fixed':
            raise ValueError(f'Base placement includes movable joint {joint.get("name")}')
        origin = joint.find('origin')
        xyz = [0., 0., 0.] if origin is None else list(map(float, origin.get('xyz', '0 0 0').split()))
        rpy = [0., 0., 0.] if origin is None else list(map(float, origin.get('rpy', '0 0 0').split()))
        T = np.eye(4)
        T[:3, :3] = rot_z(rpy[2]) @ rot_y(rpy[1]) @ rot_x(rpy[0])
        T[:3, 3] = xyz
        return world_transform(joint.find('parent').get('link'), visited) @ T

    left = world_transform('left_base_link')
    right = world_transform('right_base_link')
    return np.linalg.inv(right) @ left, np.eye(4)


def camera_projection_matrix(T_world_camera):
    """P=K[R_cw | t_cw], from a camera->world transform."""
    R_wc, C_w = T_world_camera[:3, :3], T_world_camera[:3, 3]
    R_cw = R_wc.T
    t_cw = -R_cw @ C_w
    return K @ np.column_stack((R_cw, t_cw))


def charuco_board_camera_pose(image_bgr, min_markers=3, max_rmse_px=2.0):
    """Measure optical-camera -> fixed board from visible ArUco marker corners."""
    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('OpenCV ArUco module is required for board-relative stereo')
    board = cv2.aruco.CharucoBoard(
        (7, 5), 0.025, 0.017,
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_50))
    detector = cv2.aruco.ArucoDetector(board.getDictionary())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY))
    board_ids = board.getIds().reshape(-1)
    board_points = board.getObjPoints()
    object_points, image_points, used_ids = [], [], []
    if ids is not None:
        for corner, marker_id in zip(corners, ids.reshape(-1)):
            found = np.flatnonzero(board_ids == marker_id)
            if len(found):
                object_points.extend(np.asarray(board_points[int(found[0])]).reshape(-1, 3))
                image_points.extend(np.asarray(corner).reshape(-1, 2))
                used_ids.append(int(marker_id))
    if len(used_ids) < min_markers:
        raise RuntimeError(f'Only {len(used_ids)} known ChArUco markers visible; '
                           f'need {min_markers} in each camera')
    object_points = np.asarray(object_points, dtype=np.float64)
    image_points = np.asarray(image_points, dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(object_points, image_points, K, DIST)
    if not ok:
        raise RuntimeError('ChArUco marker pose solvePnP failed')
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, K, DIST)
    rmse = float(np.sqrt(np.mean(np.sum(
        (projected.reshape(-1, 2) - image_points) ** 2, axis=1))))
    if not np.isfinite(rmse) or rmse > max_rmse_px:
        raise RuntimeError(f'ChArUco marker pose RMSE {rmse:.2f} px '
                           f'exceeds {max_rmse_px:.1f} px')
    T_camera_board = np.eye(4, dtype=np.float64)
    T_camera_board[:3, :3] = cv2.Rodrigues(rvec)[0]
    T_camera_board[:3, 3] = tvec.reshape(3)
    return T_camera_board, {'marker_ids': used_ids, 'pnp_rmse_px': rmse}


def board_relative_camera_poses(images, T_world_cameras):
    """Anchor LEFT world pose; align RIGHT to the same board seen in both frames."""
    left_board, left_info = charuco_board_camera_pose(images[0])
    right_board, right_info = charuco_board_camera_pose(images[1])
    left_world = T_world_cameras[0]
    right_from_board = left_world @ left_board @ np.linalg.inv(right_board)
    old_board_world = T_world_cameras[1] @ right_board
    new_board_world = left_world @ left_board
    delta = np.linalg.inv(new_board_world) @ old_board_world
    angle_deg = float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(delta[:3, :3])[0])))
    info = {
        'left': left_info, 'right': right_info,
        'old_board_world_difference_mm': float(np.linalg.norm(
            old_board_world[:3, 3] - new_board_world[:3, 3]) * 1000),
        'old_board_world_rotation_difference_deg': angle_deg,
        'method': 'same captured board, left world camera pose as anchor',
    }
    return [left_world, right_from_board], info


def fixed_see_center_camera_poses(T_world_cameras, measured_joints, correction_path):
    """Reuse a board-measured correction only near its recorded joint pose."""
    record = json.loads(correction_path.read_text())
    if record.get('frame') != 'right_base_link':
        raise RuntimeError('Fixed stereo correction has the wrong world frame')
    correction = np.asarray(record['right_world_pose_left_multiply'], dtype=np.float64)
    reference = np.asarray(record['reference_joint_positions_rad'], dtype=np.float64)
    joints = np.asarray(measured_joints, dtype=np.float64)
    if (correction.shape != (4, 4) or reference.shape != (2, 6) or
            joints.shape != (2, 6) or not np.isfinite(correction).all() or
            not np.isfinite(reference).all() or not np.isfinite(joints).all() or
            not np.allclose(correction[3], [0, 0, 0, 1], atol=1e-8) or
            not np.allclose(correction[:3, :3].T @ correction[:3, :3], np.eye(3), atol=1e-3)):
        raise RuntimeError('Invalid fixed stereo correction data')
    max_difference = float(np.rad2deg(np.max(np.abs(joints - reference))))
    limit = float(record['max_joint_difference_deg'])
    if not np.isfinite(limit) or limit <= 0 or max_difference > limit:
        raise RuntimeError(f'Fixed stereo correction requires reference pose: '
                           f'joint difference {max_difference:.2f} deg (limit {limit:.2f})')
    return [T_world_cameras[0], correction @ T_world_cameras[1]], {
        'method': record['method'],
        'source_capture': record['source_capture'],
        'max_joint_difference_deg': max_difference,
        'limit_deg': limit,
    }


def project_points(points_w, T_world_camera):
    R_wc, C_w = T_world_camera[:3, :3], T_world_camera[:3, 3]
    points_c = (points_w - C_w) @ R_wc
    z = points_c[:, 2]
    pix = np.full((len(points_c), 2), np.nan, dtype=np.float64)
    ok = np.abs(z) > 1e-9
    if np.any(ok):
        distorted, _ = cv2.projectPoints(
            points_c[ok], np.zeros(3), np.zeros(3), K, DIST)
        pix[ok] = distorted.reshape(-1, 2)
    return pix, z


def triangulate_matches(pts_left, pts_right, T_wc_left, T_wc_right,
                        reproj_threshold_px=30.0, min_angle_deg=1.5):
    """Triangulate in RIGHT-robot-base frame; reject invalid/unreliable pairs."""
    if len(pts_left) == 0:
        return dict(xyz_m=np.empty((0, 3)), valid=np.empty((0,), bool),
                    reproj_left=np.empty((0,)), reproj_right=np.empty((0,)),
                    depth_left_m=np.empty((0,)), depth_right_m=np.empty((0,)),
                    angle_deg=np.empty((0,)))
    P_l = camera_projection_matrix(T_wc_left)
    P_r = camera_projection_matrix(T_wc_right)
    undist_left = cv2.undistortPoints(
        np.asarray(pts_left, dtype=np.float64).reshape(-1, 1, 2), K, DIST, P=K).reshape(-1, 2)
    undist_right = cv2.undistortPoints(
        np.asarray(pts_right, dtype=np.float64).reshape(-1, 1, 2), K, DIST, P=K).reshape(-1, 2)
    homog = cv2.triangulatePoints(P_l, P_r,
                                  np.ascontiguousarray(undist_left.T),
                                  np.ascontiguousarray(undist_right.T))
    w = homog[3, :]
    xyz = np.full((len(pts_left), 3), np.nan, dtype=np.float64)
    stable = np.isfinite(w) & (np.abs(w) > 1e-12)
    xyz[stable] = (homog[:3, stable] / w[stable]).T
    with np.errstate(divide='ignore', invalid='ignore'):
        predicted_l, z_l = project_points(xyz, T_wc_left)
        predicted_r, z_r = project_points(xyz, T_wc_right)
        err_l = np.linalg.norm(predicted_l - pts_left, axis=1)
        err_r = np.linalg.norm(predicted_r - pts_right, axis=1)
        c_l = T_wc_left[:3, 3]
        c_r = T_wc_right[:3, 3]
        a = xyz - c_l
        b = xyz - c_r
        denom = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
        cosangle = np.clip(np.sum(a*b, axis=1) / denom, -1., 1.)
        angle = np.rad2deg(np.arccos(cosangle))
    valid = (stable & np.isfinite(xyz).all(axis=1) &
             np.isfinite(err_l) & np.isfinite(err_r) &
             (z_l > 0.01) & (z_r > 0.01) &
             (err_l <= reproj_threshold_px) &
             (err_r <= reproj_threshold_px) &
             (angle >= min_angle_deg))
    return dict(xyz_m=xyz, valid=valid, reproj_left=err_l,
                reproj_right=err_r, depth_left_m=z_l, depth_right_m=z_r,
                angle_deg=angle)


def save_xyz_csv(path, pts_l, pts_r, triangulation):
    v = triangulation
    data = np.column_stack((pts_l, pts_r, v['xyz_m'] * 1000,
                            v['depth_left_m'] * 1000, v['depth_right_m'] * 1000,
                            v['reproj_left'], v['reproj_right'],
                            v['angle_deg'], v['valid'].astype(int)))
    np.savetxt(str(path), data, delimiter=',', fmt='%.6f', comments='',
               header='left_u_px,left_v_px,right_u_px,right_v_px,x_mm,y_mm,z_mm,'
                      'left_depth_mm,right_depth_mm,'
                      'reproj_left_px,reproj_right_px,ray_angle_deg,valid')


class LivePoses:
    """Measured joints -> URDF 6_Link -> calibrated optical camera, read only."""
    def __init__(self, urdf_path):
        import rclpy
        from sensor_msgs.msg import JointState
        self.ros = rclpy
        self.joints = {j.find('child').get('link'): j
                       for j in ET.parse(urdf_path).getroot().findall('joint')}
        self.T_right_left, _ = urdf_base_transforms(urdf_path)
        self.samples = {}
        rclpy.init()
        self.node = rclpy.create_node('dual_roi_measured_joints')
        self.subs = [self.node.create_subscription(
            JointState, f'/{side}/real_joint_states',
            lambda msg, side=side: self.receive(side, msg), 10)
            for side in ('left', 'right')]

    def receive(self, side, msg):
        if len(msg.name) != len(msg.position) or len(set(msg.name)) != len(msg.name):
            return
        raw = dict(zip(msg.name, msg.position))
        q = {}
        for i in range(1, 7):
            name = f'{side}_{i}_Joint'
            value = raw.get(name, raw.get(f'{i}_Joint'))
            if value is None or not np.isfinite(value):
                return
            q[name] = float(value)
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.samples[side] = (time.monotonic(), stamp, q)

    def poll(self):
        # Drain a bounded number of callbacks, including both subscriptions.
        for _ in range(4):
            self.ros.spin_once(self.node, timeout_sec=0.0)

    def wrist_transform(self, side, q):
        def chain(link, visited):
            if link == f'{side}_base_link':
                return np.eye(4)
            if link in visited or link not in self.joints:
                raise RuntimeError(f'Invalid URDF wrist chain: {link}')
            joint = self.joints[link]
            origin = joint.find('origin')
            T = np.eye(4)
            if origin is not None:
                T[:3, 3] = list(map(float, origin.get('xyz', '0 0 0').split()))
                roll, pitch, yaw = map(float, origin.get('rpy', '0 0 0').split())
                T[:3, :3] = rot_z(yaw) @ rot_y(pitch) @ rot_x(roll)
            kind = joint.get('type')
            if kind in ('revolute', 'continuous'):
                axis_tag = joint.find('axis')
                axis = np.array(list(map(float, '1 0 0'.split() if axis_tag is None
                                         else axis_tag.get('xyz', '1 0 0').split())))
                norm = np.linalg.norm(axis)
                if norm < 1e-12 or joint.get('name') not in q:
                    raise RuntimeError(f'Missing joint measurement or invalid axis: {joint.get("name")}')
                motion = np.eye(4)
                motion[:3, :3] = cv2.Rodrigues(axis / norm * q[joint.get('name')])[0]
                T = T @ motion
            elif kind != 'fixed':
                raise RuntimeError(f'Unsupported wrist joint type: {kind}')
            return chain(joint.find('parent').get('link'), visited | {link}) @ T
        return chain(f'{side}_6_Link', set())

    def snapshot(self):
        self.poll()
        if all(side in self.samples and time.monotonic() - self.samples[side][0] <= 1.5
               for side in ('left', 'right')):
            deadline = time.monotonic() + 1.0
            while (abs(self.samples['left'][0] - self.samples['right'][0]) > 0.75
                   and time.monotonic() < deadline):
                self.ros.spin_once(self.node, timeout_sec=0.1)
        cameras, joints, stamps, received_times = [], [], [], []
        for side in ('left', 'right'):
            if side not in self.samples:
                raise RuntimeError(f'Waiting for /{side}/real_joint_states')
            received, stamp, q = self.samples[side]
            if time.monotonic() - received > 1.5:
                raise RuntimeError(f'{side} joints are stale (no valid message received within 1.5 s).')
            base = self.T_right_left if side == 'left' else np.eye(4)
            cameras.append(base @ self.wrist_transform(side, q) @ T_GRIPPER_CAMERA_BY_SIDE[side])
            joints.append([q[f'{side}_{i}_Joint'] for i in range(1, 7)])
            stamps.append(stamp)
            received_times.append(received)
        if abs(received_times[0] - received_times[1]) > 0.75:
            raise RuntimeError('Left/right joint samples are too far apart in time '
                               f'({abs(received_times[0] - received_times[1]):.2f} s).')
        return cameras, np.array(joints), np.array(stamps)

    def close(self):
        self.node.destroy_node()
        self.ros.shutdown()


class Camera:
    def __init__(self, label, port):
        self.label, self.port = label, port
        self.frame = None
        self.received = None
        self.count = 0
        self.pipeline = Gst.parse_launch(
            f'udpsrc port={port} reuse=false '
            'caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
            'rtpjitterbuffer latency=30 drop-on-latency=true ! '
            'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
            'video/x-raw,format=BGR ! '
            'appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true'
        )
        self.sink = self.pipeline.get_by_name('sink')
        self.bus = self.pipeline.get_bus()

    def start(self):
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f'{self.label}: receiver startup failed')

    def poll(self):
        message = self.bus.timed_pop_filtered(
            0, Gst.MessageType.ERROR | Gst.MessageType.EOS
        )
        if message:
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                raise RuntimeError(f'{self.label} UDP {self.port}: {error}; {debug}')
            raise RuntimeError(f'{self.label}: stream ended')
        sample = self.sink.emit('try-pull-sample', 0)
        if sample is None:
            return
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            self.frame = np.ndarray(
                (info.height, info.width, 3), dtype=np.uint8,
                buffer=mapped.data, offset=info.offset[0],
                strides=(info.stride[0], 3, 1)
            ).copy()
        finally:
            buffer.unmap(mapped)
        self.received = time.monotonic()
        self.count += 1
        if self.count == 1:
            print(f'{self.label}: UDP {self.port}, {info.width}x{info.height}', flush=True)

    def is_live(self):
        return self.frame is not None and self.received is not None and time.monotonic() - self.received < 1.0

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)




def normalize_yaw_deg(angle):
    """An undirected long axis is equivalent after a 180-degree flip."""
    return (float(angle) + 90.0) % 180.0 - 90.0


def yaw_difference_180(a, b):
    return abs(normalize_yaw_deg(float(a) - float(b)))


def largest_component(mask):
    binary = (mask > 0).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if num < 2:
        return binary.astype(bool)
    region = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == region


def segment_with_sam(predictor, image_bgr, roi):
    """SAM on full 640x480 image with user box; return only the selected region."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pred_device = getattr(predictor, 'device', torch.device('cpu'))
    is_cuda = torch.device(pred_device).type == 'cuda'
    autocast_ctx = (torch.autocast('cuda', dtype=torch.bfloat16)
                    if is_cuda else nullcontext())
    with torch.inference_mode(), autocast_ctx:
        predictor.set_image(rgb)
        masks, scores, _ = predictor.predict(
            box=np.array(roi, dtype=np.float32), multimask_output=False)
    if len(masks) != 1:
        raise RuntimeError(f'SAM expected one mask, got {len(masks)}')
    mask = np.asarray(masks[0] > 0, dtype=np.uint8)
    x1, y1, x2, y2 = roi
    only_roi = np.zeros_like(mask)
    only_roi[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
    binary = largest_component(only_roi)
    if int(binary.sum()) < 70:
        raise RuntimeError('SAM produced too few object pixels. Adjust ROI.')
    return binary, float(np.asarray(scores).reshape(-1)[0])


def to_tensor(bgr, device):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return (torch.from_numpy(np.ascontiguousarray(rgb))
            .permute(2, 0, 1).float().div(255).unsqueeze(0).to(device))


def contrast_enhanced_image(image):
    """Boost low-contrast object texture for a second feature-matching pass."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def mask_filtered_features(extractor, image, mask, roi, device, erode_px, max_keypoints):
    """Keep only mask-interior keypoints (including descriptors) BEFORE matching."""
    x1, y1, x2, y2 = roi
    crop = image[y1:y2, x1:x2].copy()
    inside = mask[y1:y2, x1:x2].astype(np.uint8)
    if erode_px > 0:
        ksize = 2 * erode_px + 1
        inside = cv2.erode(inside, np.ones((ksize, ksize), np.uint8))
    if not np.any(inside):
        raise RuntimeError('ROI mask completely disappeared after erosion.')

    # Avoid artificial high-contrast boundaries created by all-black backgrounds.
    # Extract on ORIGINAL crop; then remove background features BEFORE LightGlue.
    f = extractor.extract(to_tensor(crop, device))
    keypoints = f['keypoints'][0].detach().cpu().numpy()
    px = np.rint(keypoints).astype(np.int32)
    if len(px) == 0:
        raise RuntimeError('SuperPoint found no keypoints.')
    h, w = inside.shape
    valid = ((px[:, 0] >= 0) & (px[:, 0] < w) &
             (px[:, 1] >= 0) & (px[:, 1] < h))
    keep = np.zeros(len(px), dtype=bool)
    keep[valid] = inside[px[valid, 1], px[valid, 0]] > 0
    idx = torch.as_tensor(np.flatnonzero(keep), device=f['keypoints'].device)
    count_all = len(px)
    if len(idx) > max_keypoints:
        scores = f['keypoint_scores'][0].index_select(0, idx)
        best = torch.topk(scores, max_keypoints, sorted=True).indices
        idx = idx.index_select(0, best)
    count_kept = len(idx)
    if count_kept < 2:
        raise RuntimeError(f'Only {count_kept} mask-interior features; widen ROI or reduce erosion.')
    # A LightGlue feature dictionary includes fields with per-keypoint second dimension;
    # keep image_size unchanged because it is a global image attribute.
    for key, value in list(f.items()):
        if (key != 'image_size' and torch.is_tensor(value) and
            value.ndim >= 2 and value.shape[1] == count_all):
            f[key] = value.index_select(1, idx)
    return f, count_all, count_kept


def rotate_match_crop(image, degrees):
    if degrees == 0:
        return image
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE}[degrees]
    return cv2.rotate(image, code)


def unrotate_match_points(points, degrees, width, height):
    x, y = points[:, 0], points[:, 1]
    if degrees == 90:
        return np.column_stack((y, height - 1 - x))
    if degrees == 180:
        return np.column_stack((width - 1 - x, height - 1 - y))
    if degrees == 270:
        return np.column_stack((width - 1 - y, x))
    return points


def match_homography_inliers(left, right):
    if len(left) < 4:
        return np.zeros(len(left), dtype=bool)
    matrix, flags = cv2.findHomography(left, right, cv2.USAC_MAGSAC, 3.0)
    if matrix is None or flags is None:
        return np.zeros(len(left), dtype=bool)
    return flags.ravel().astype(bool)


def triangulated_good(tri):
    pts = np.asarray(tri['xyz_m'])[np.asarray(tri['valid'], dtype=bool)]
    if len(pts) < 3:
        return pts
    # Remove especially extreme depth outliers, but allow normal object height variation.
    zm = float(np.median(pts[:, 2]))
    mad = float(np.median(np.abs(pts[:, 2] - zm)))
    ok = np.abs(pts[:, 2] - zm) < max(0.035, 4.0 * 1.4826 * mad)
    return pts[ok] if sum(ok) >= 3 else pts


def pca_axis_xy(xy):
    xy = np.asarray(xy, dtype=np.float64)
    if len(xy) < 5 or not np.isfinite(xy).all():
        return None
    xy = xy - xy.mean(axis=0)
    cov = xy.T @ xy / max(1, len(xy) - 1)
    vals, vecs = np.linalg.eigh(cov)
    if vals[1] < 1e-9:
        return None
    idx = np.argsort(vals)
    vals, vecs = vals[idx], vecs[:, idx]
    vec = vecs[:, -1]
    yaw = normalize_yaw_deg(np.rad2deg(np.arctan2(vec[1], vec[0])))
    aspect = float(np.sqrt(vals[-1] / max(vals[0], 1e-12)))
    return dict(yaw_deg=yaw, aspect=aspect)


def pixels_to_level_plane(uv, T_wc, z_level):
    """Map pixel rays to horizontal Z=z_level plane in right_base_link."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    normalized = cv2.undistortPoints(uv.reshape(-1, 1, 2), K, DIST).reshape(-1, 2)
    rays_cam = np.column_stack((normalized, np.ones(len(uv))))
    R = T_wc[:3, :3]
    C = T_wc[:3, 3]
    rays_world = rays_cam @ R.T
    dz = rays_world[:, 2]
    ok = np.abs(dz) > 1e-4
    lam = np.zeros(len(dz), dtype=np.float64)
    lam[ok] = (z_level - C[2]) / dz[ok]
    ok &= (lam > 0) & (lam < 3.0) & np.isfinite(lam)
    return (C + lam[ok, None] * rays_world[ok])[:, :2]


def mask_rectangle_world(mask, T_wc, z_plane):
    """Approximate ground-parallel footprint by back-projecting silhouette.

    Works best for upright objects viewed near vertically. It is NOT a true
    3D mesh, side-face contour pixels do not all have identical Z.
    """
    optical_z_world = T_wc[:3, :3][:, 2]
    cosine = float(np.dot(optical_z_world, [0, 0, -1]))
    if cosine < 0.60:
        return None  # camera not top-down enough for this approximation
    contours, _ = cv2.findContours(mask.astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    uv = contour[:, 0, :][::max(1, len(contour) // 500)].astype(np.float64)
    plane = pixels_to_level_plane(uv, T_wc, z_plane)
    if len(plane) < 8:
        return None
    rect = cv2.minAreaRect(plane.astype(np.float32))
    corners = cv2.boxPoints(rect).astype(np.float64)
    edge0 = corners[1] - corners[0]
    edge1 = corners[2] - corners[1]
    if np.linalg.norm(edge0) > np.linalg.norm(edge1):
        long, short = edge0, edge1
    else:
        long, short = edge1, edge0
    length = float(np.linalg.norm(long))
    width = float(np.linalg.norm(short))
    if width < 0.003 or length < 0.008:
        return None
    return dict(
        yaw_deg=normalize_yaw_deg(np.rad2deg(np.arctan2(long[1], long[0]))),
        center_xy=np.array(rect[0], dtype=np.float64),
        length_m=length, width_m=width, aspect=length / width,
        corners_xy=corners, vertical_cos=cosine)


def estimate_grasp_preview(masks, T_wc_list, tri, min_points=6, arm='right', yaw_camera='auto'):
    points = triangulated_good(tri)
    if len(points) < min_points:
        return None, f'TOO FEW 3D POINTS ({len(points)})'
    center = np.median(points, axis=0)
    z_ref = float(center[2])
    pca = pca_axis_xy(points[:, :2])

    options = []
    for index, (mask, T_wc) in enumerate(zip(masks, T_wc_list)):
        candidate = mask_rectangle_world(mask, T_wc, z_ref)
        if candidate is not None:
            candidate['source'] = ('LEFT', 'RIGHT')[index]
            options.append(candidate)
    # A more top-down camera is not necessarily the cleaner silhouette. Compare
    # each planar mask center to the stereo 3D median and reward elongation.
    for candidate in options:
        candidate['center_error_m'] = float(np.linalg.norm(candidate['center_xy'] - center[:2]))
        candidate['view_score'] = (candidate['vertical_cos']
                                   + 0.10 * min(candidate['aspect'], 3.0)
                                   - 4.0 * min(candidate['center_error_m'], 0.05))
    options.sort(key=lambda e: e['view_score'], reverse=True)

    if options:
        selected_side = options[0]['source'].lower() if yaw_camera == 'auto' else yaw_camera
        selected = [item for item in options if item['source'].lower() == selected_side]
        if not selected:
            return None, f'{selected_side.upper()} camera has no planar yaw estimate; adjust ROI/view'
        chosen = selected[0]
        # The selected camera must supply both its yaw and its projected XY center.
        center[:2] = chosen['center_xy']
        yaw = chosen['yaw_deg']
        axis_ratio = chosen['aspect']
        origin = f'SAM mask projected onto Z={z_ref*1000:.1f} mm plane ({chosen["source"]})'
        corners = chosen['corners_xy']
        length = chosen['length_m']
        width = chosen['width_m']
    elif pca is not None:
        return None, 'No camera has a planar yaw estimate; adjust ROI/view'
    else:
        return None, 'NO STABLE ORIENTATION'

    warnings = []
    if axis_ratio < 1.3:
        warnings.append('NEAR-SYMMETRIC SHAPE: YAW UNSTABLE')
    if pca is not None and yaw_difference_180(yaw, pca['yaw_deg']) > 30:
        warnings.append('MASK YAW DIFFERS FROM SPARSE 3D PCA')
    if len(options) >= 2 and yaw_difference_180(options[0]['yaw_deg'], options[1]['yaw_deg']) > 25:
        warnings.append('LEFT/RIGHT MASK YAW DISAGREE')
    if not options:
        warnings.append('NO PLANAR MASK FOOTPRINT; PCA IS PRELIMINARY')
    if chosen['center_error_m'] > 0.025:
        warnings.append('SELECTED MASK CENTER DIFFERS FROM STEREO POINTS BY >25 MM')
    warnings.append('TOP SURFACE Z AND CONTACTS NOT VERIFIED')

    # Closing axis is perpendicular to the object's long axis, subject to
    # the robot gripper's actual TCP convention and aperture geometry.
    grasp_closing_yaw = normalize_yaw_deg(yaw + 90.0)
    result = dict(
        center_xyz_m=center, object_long_yaw_deg=yaw,
        selected_arm=arm, selected_yaw_camera=chosen['source'].lower(),
        yaw_view_scores={item['source'].lower(): float(item['view_score']) for item in options},
        yaw_view_details={item['source'].lower(): {
            'vertical_cos': float(item['vertical_cos']),
            'aspect_ratio': float(item['aspect']),
            'center_error_mm': float(item['center_error_m'] * 1000.)}
            for item in options},
        yaw_by_side={item['source'].lower(): {
            'object_long_yaw_deg': float(item['yaw_deg']),
            'closing_axis_yaw_deg': normalize_yaw_deg(item['yaw_deg'] + 90.),
            'center_xy_mm': (item['center_xy'] * 1000.).tolist()}
            for item in options},
        closing_axis_yaw_deg=grasp_closing_yaw,
        aspect_ratio=axis_ratio, source=origin,
        points_3d=len(points), footprint_corners_xy=corners,
        length_m=length, width_m=width,
        warnings=warnings,
        point_pca_yaw_deg=None if pca is None else pca['yaw_deg'])
    return result, 'OK (PREVIEW ONLY)'


def overlay_mask(img, mask):
    out = img.copy()
    shade = np.zeros_like(out)
    shade[:, :, 1] = 230
    out[mask] = cv2.addWeighted(out, 0.58, shade, 0.42, 0)[mask]
    contours, _ = cv2.findContours(mask.astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, contours, -1, (0, 255, 0), 2)
    return out


def draw_grasp_axes(img, T_wc, center_xyz, long_yaw_deg, length_m=None, width_m=None):
    """Overlay approximate desired closing axis, not the calibrated TCP pose."""
    a = np.deg2rad(long_yaw_deg)
    axis_long = np.array([np.cos(a), np.sin(a), 0])
    axis_close = np.array([-np.sin(a), np.cos(a), 0])
    # Default 30-mm half-length so overlay stays visible for small objects.
    long_half = 0.03 if length_m is None else float(np.clip(length_m / 2, .015, .075))
    short_half = 0.025 if width_m is None else float(np.clip(width_m / 2, .012, .06))
    pts3 = np.vstack((center_xyz, center_xyz + axis_long * long_half,
                      center_xyz - axis_long * long_half,
                      center_xyz + axis_close * short_half,
                      center_xyz - axis_close * short_half))
    uv, depth = project_points(pts3, T_wc)
    if not np.isfinite(uv).all() or not np.all(depth > 0):
        return img
    xy = np.rint(uv).astype(int)
    h, w = img.shape[:2]
    if not (0 <= xy[0,0] < w and 0 <= xy[0,1] < h):
        return img
    cv2.line(img, tuple(xy[2]), tuple(xy[1]), (0, 165, 255), 3, cv2.LINE_AA)
    cv2.line(img, tuple(xy[4]), tuple(xy[3]), (255, 60, 220), 3, cv2.LINE_AA)
    cv2.circle(img, tuple(xy[0]), 6, (0, 0, 255), -1)
    return img


def topdown_plot(tri, preview):
    """World XY visualization. Orange = long axis, purple = closing axis."""
    size = 540
    canvas = np.full((size, size, 3), 246, dtype=np.uint8)
    if preview is None:
        cv2.putText(canvas, 'NO VALID 3D GRASP PREVIEW', (30, size//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 180), 2)
        return canvas
    pts = triangulated_good(tri)[:, :2]
    center = preview['center_xyz_m'][:2]
    radius = max(0.09, float(np.max(np.abs(pts - center))) * 1.5)
    radius = min(radius, 0.7)
    scale = 230.0 / radius
    mid = np.array([size/2, size/2])
    def pixel(p):
        q = mid + (np.asarray(p) - center) * [scale, -scale]
        return tuple(np.rint(q).astype(int))

    cv2.line(canvas, (20, size//2), (size-20, size//2), (190, 190, 190), 1)
    cv2.line(canvas, (size//2, 20), (size//2, size-20), (190, 190, 190), 1)
    cv2.putText(canvas, '+X', (size-60, size//2-8), cv2.FONT_HERSHEY_SIMPLEX,
                .6, (80,80,80), 2)
    cv2.putText(canvas, '+Y', (size//2+8, 35), cv2.FONT_HERSHEY_SIMPLEX,
                .6, (80,80,80), 2)
    for p in pts:
        cv2.circle(canvas, pixel(p), 2, (80, 110, 170), -1)
    corners = preview['footprint_corners_xy']
    if corners is not None:
        cv2.polylines(canvas, [np.array([pixel(p) for p in corners])], True,
                      (0, 150, 60), 2)
    a = np.deg2rad(preview['object_long_yaw_deg'])
    dir_l = np.array([np.cos(a), np.sin(a)])
    dir_c = np.array([-np.sin(a), np.cos(a)])
    ll = (preview['length_m']/2 if preview['length_m'] else .04)
    wl = (preview['width_m']/2 if preview['width_m'] else .03)
    ll, wl = max(ll, .025), max(wl, .018)
    cv2.line(canvas, pixel(center-ll*dir_l), pixel(center+ll*dir_l),
             (0, 165, 255), 3, cv2.LINE_AA)
    cv2.line(canvas, pixel(center-wl*dir_c), pixel(center+wl*dir_c),
             (255, 60, 220), 3, cv2.LINE_AA)
    cv2.circle(canvas, pixel(center), 5, (0, 0, 255), -1)
    yaw = preview['object_long_yaw_deg']
    grasp_yaw = preview['closing_axis_yaw_deg']
    cv2.rectangle(canvas, (0, 0), (size, 78), (255,255,255), -1)
    cv2.putText(canvas, f'OBJECT LONG AXIS: {yaw:+.1f} deg', (12, 26),
                cv2.FONT_HERSHEY_SIMPLEX, .67, (0, 110, 205), 2)
    cv2.putText(canvas, f'CLOSING AXIS: {grasp_yaw:+.1f} deg', (12, 52),
                cv2.FONT_HERSHEY_SIMPLEX, .65, (190, 25, 170), 2)
    cv2.putText(canvas, 'PREVIEW ONLY - NOT A GRASP EXECUTION POSE', (8, size-15),
                cv2.FONT_HERSHEY_SIMPLEX, .44, (60, 60, 60), 1)
    return canvas


class SAMYawApp(AutoFineMixin):
    def __init__(self, cameras, live_poses, sam_predictor, extractor, matcher, device, args):
        self.cameras = cameras
        self.live_poses = live_poses
        self.sam_predictor = sam_predictor
        self.extractor = extractor
        self.matcher = matcher
        self.device = device
        self.args = args
        self.selected_object_type = None
        self.frozen = None
        self.frozen_joints = None
        self.frozen_stamps = None
        self.T_wc = None
        self.T_wc_measured = None
        self.rois = [None, None]
        self.drag = None
        self.pending = False
        self.result = None
        self.topview = None
        self.status = 'DRAG ROI LEFT + RIGHT'
        self.capture_id = 0
        self.capture_time_unix = None
        self.capture_consumed = False
        self.preview_file = None
        self.plan_ready = False
        self.execute_phase = 'approach'
        self.motion_child = None
        self.motion_log_writer = None
        self.motion_log_reader = None
        self.motion_stage = None
        self.approach_camera_enabled = False
        self.align_roi = None
        self.align_drag = None
        self.align_roi_at = None
        self.align_template = None
        self.auto_align_pending = False
        self.align_wait_frame_count = None
        self.align_prev_error = None
        self.fine_axis = None
        self.fine_request = None
        self.fine_stop_requested = False
        self.fine_direction_signs = {'x': args.fine_x_sign, 'y': args.fine_y_sign}
        self.last_fine_observation = None
        self.grasp_preview_file = None
        self.auto_grasp_attempted = False
        self.auto_grasp_cancelled = False
        self.auto_grasp_ready_at = None
        self.camera_K, self.camera_DIST = K, DIST
        self.init_auto_fine()

    def reset(self):
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Motion planning/execution is still active; R is blocked.', flush=True)
            return
        self.plan_ready = False
        self.selected_object_type = None
        self.args.object_type = None
        self.execute_phase = 'approach'
        self.approach_camera_enabled = False
        self.align_roi = None
        self.align_drag = None
        self.align_roi_at = None
        self.align_template = None
        self.auto_align_pending = False
        self.align_wait_frame_count = None
        self.align_prev_error = None
        self.fine_axis = None
        self.fine_request = None
        self.fine_stop_requested = False
        self.fine_direction_signs = {'x': self.args.fine_x_sign, 'y': self.args.fine_y_sign}
        self.last_fine_observation = None
        self.grasp_preview_file = None
        self.auto_grasp_attempted = False
        self.auto_grasp_cancelled = False
        self.auto_grasp_ready_at = None
        self.reset_auto_fine(clear_moves=True)
        self.preview_file = None
        self.capture_consumed = False
        self.capture_time_unix = None
        self.frozen = None
        self.frozen_joints = None
        self.frozen_stamps = None
        self.T_wc = None
        self.T_wc_measured = None
        self.rois = [None, None]
        self.drag = None
        self.pending = False
        self.result = None
        self.topview = None
        self.status = 'DRAG ROI LEFT + RIGHT'
        print('Reset: select NEW two ROIs.', flush=True)

    def panel_coord(self, x, y):
        idx = 0 if x < PANEL_W else 1
        pt = (int(np.clip(x-idx*PANEL_W, 0, PANEL_W-1)),
              int(np.clip(y, 0, PANEL_H-1)))
        return idx, pt

    @staticmethod
    def native_pt(pt, image):
        h, w = image.shape[:2]
        return (int(np.clip(round(pt[0]*w/PANEL_W),0,w)),
                int(np.clip(round(pt[1]*h/PANEL_H),0,h)))

    def on_mouse(self, event, x, y, flags, userdata):
        if self.capture_consumed or (self.motion_child is not None and self.motion_child.poll() is None):
            if event == cv2.EVENT_LBUTTONDOWN:
                print('Capture used; press R after motion finishes to select a new pair.', flush=True)
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.frozen is None:
                if not all(c.is_live() for c in self.cameras):
                    print('Both UDP cameras must be LIVE.', flush=True)
                    return
                if any(c.frame.shape[:2] != (480,640) for c in self.cameras):
                    print('Expected 640x480; calibration K is for 640x480.', flush=True)
                    return
                try:
                    self.T_wc, self.frozen_joints, self.frozen_stamps = self.live_poses.snapshot()
                    self.T_wc_measured = [transform.copy() for transform in self.T_wc]
                except RuntimeError as e:
                    print('Cannot capture measured poses:', e, flush=True)
                    return
                self.frozen = [cam.frame.copy() for cam in self.cameras]
                self.capture_time_unix = time.time()
                self.status = 'FROZEN - SELECT ROIS'
                print('Captured frozen stereo pair + fresh measured joint FK.', flush=True)
            idx, pt = self.panel_coord(x, y)
            self.drag = (idx, pt, pt)
            self.rois[idx] = None
            self.pending = False
            self.result = None
            self.topview = None
        elif event == cv2.EVENT_MOUSEMOVE and self.drag is not None:
            idx, a, _ = self.drag
            self.drag = (idx, a,
                (int(np.clip(x-idx*PANEL_W, 0, PANEL_W-1)),
                 int(np.clip(y, 0, PANEL_H-1))))
        elif event == cv2.EVENT_LBUTTONUP and self.drag is not None:
            idx, a, _ = self.drag
            b = (int(np.clip(x-idx*PANEL_W,0,PANEL_W-1)),
                 int(np.clip(y,0,PANEL_H-1)))
            self.drag = None
            p1 = self.native_pt(a, self.frozen[idx])
            p2 = self.native_pt(b, self.frozen[idx])
            x1,x2 = sorted((p1[0],p2[0]))
            y1,y2 = sorted((p1[1],p2[1]))
            if x2-x1 < 32 or y2-y1 < 32:
                print('ROI must be >= 32x32 pixels.', flush=True)
                return
            self.rois[idx]=(x1,y1,x2,y2)
            print(('LEFT','RIGHT')[idx], 'ROI', self.rois[idx], flush=True)
            self.pending = all(roi is not None for roi in self.rois)
            if self.pending:
                self.status = 'SAM2 + LIGHTGLUE WORKING...'

    def view(self):
        images = self.frozen if self.frozen is not None else [c.frame for c in self.cameras]
        panels=[]
        for idx, image in enumerate(images):
            panel = (np.zeros((PANEL_H,PANEL_W,3), dtype=np.uint8) if image is None
                     else cv2.resize(image,(PANEL_W,PANEL_H)))
            if self.frozen is not None and self.rois[idx] is not None:
                x1,y1,x2,y2 = self.rois[idx]
                cv2.rectangle(panel,(x1,y1),(x2,y2),(0,255,0),2)
            if self.drag is not None and self.drag[0] == idx:
                _,a,b=self.drag
                cv2.rectangle(panel,a,b,(0,255,255),2)
            cv2.rectangle(panel,(0,0),(PANEL_W,32),(20,20,20),-1)
            status='FROZEN' if self.frozen is not None else ('LIVE' if self.cameras[idx].is_live() else 'WAITING')
            cv2.putText(panel, f'{("LEFT","RIGHT")[idx]} | UDP {self.cameras[idx].port} | {status}',
                        (9,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
            panels.append(panel)
        out=np.hstack(panels)
        cv2.rectangle(out,(0,PANEL_H-35),(2*PANEL_W,PANEL_H),(20,20,20),-1)
        controls = ('1: chocolate | 2: Mentos | R: recapture | Q: quit'
                    if self.preview_file is not None and self.selected_object_type is None
                    else 'AUTO GRASP + LIFT | S: stop | R: recapture | Q: quit'
                    if self.args.auto_fine and self.execute_phase in ('fine_x', 'fine_y', 'fine_done')
                    else 'S: stop | C: return | R: recapture | Q: quit'
                    if self.args.auto_fine and self.execute_phase in ('descend', 'close', 'lift', 'grasp', 'done')
                    else 'E: approach | R: recapture | Q: quit')
        cv2.putText(out,f'{self.status} | {controls}',
                    (10,PANEL_H-12),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
        return out

    def start_fine(self):
        if not self.args.enable_execute or self.fine_request is None:
            print('Automatic fine request or execution is unavailable.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            return
        if np.linalg.norm(self.fine_request['delta_xyz_m'][:2]) < .003:
            axis = self.fine_request['axis']
            self.execute_phase = 'fine_done'
            self.fine_request = None
            self.align_roi = None
            self.status = f'FINE {axis.upper()} ALREADY CENTERED - AUTO CHECKING RANGE'
            print(self.status, flush=True)
            return
        axis = self.fine_request['axis']
        roi = self.fine_request['roi_xyxy']
        pixel = (roi[0] + roi[2]) / 2 if axis == 'x' else roi[1]
        target_pixel = self.fine_request['target_pixel_xy'][0 if axis == 'x' else 1]
        self.last_fine_observation = {
            'axis': axis, 'pixel_error_px': pixel - target_pixel,
            'target_pixel_xy': self.fine_request['target_pixel_xy'],
            'start_joints_rad': self.fine_request['start_joints_rad'],
            'capture_id': self.capture_id}
        self.grasp_preview_file = None
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        request_path = self.preview_file.parent / f'fine_{axis}_{stamp}_request.json'
        request_path.write_text(json.dumps(self.fine_request, indent=2))
        logpath = self.preview_file.parent / f'fine_{axis}_{stamp}.log'
        self.motion_log_writer = logpath.open('w')
        self.motion_log_reader = logpath.open('r')
        self.motion_child = subprocess.Popen([str(self.args.motion_python),
            str(Path(__file__).with_name('move_right_sam_yaw.py')),
            '--fine-request', str(request_path)], stdout=self.motion_log_writer,
            stderr=subprocess.STDOUT, start_new_session=True)
        self.motion_stage = 'FINE'
        self.fine_stop_requested = False
        self.status = f'FINE {axis.upper()} MOVING - S STOP'
        print(f'Fine worker PID={self.motion_child.pid}, log={logpath}', flush=True)

    def start_return_see_center(self, open_grippers=True):
        if not self.args.enable_execute:
            print('C return requires --enable-execute.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the active worker or press S before C return.', flush=True)
            return
        self.live_poses.poll()
        missing = [side for side in ('left', 'right')
                   if side not in self.live_poses.samples or
                   time.monotonic() - self.live_poses.samples[side][0] > 1.5]
        if missing:
            print(f'C return unavailable: no recent measured joints from {missing}. '
                  'Check both Pi state/SDK bridges and ROS_DOMAIN_ID=42.', flush=True)
            return
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        out_dir = self.args.out_dir / 'see_center_returns' / stamp
        out_dir.mkdir(parents=True, exist_ok=True)
        logpath = out_dir / 'return.log'
        try:
            self.motion_log_writer = logpath.open('w')
            self.motion_log_reader = logpath.open('r')
            self.motion_child = subprocess.Popen([str(self.args.motion_python),
                str(Path(__file__).with_name('return_see_center.py')),
                '--out-dir', str(out_dir)] + (['--open-grippers'] if open_grippers else []),
                stdout=self.motion_log_writer,
                stderr=subprocess.STDOUT, start_new_session=True)
            self.motion_stage = 'RETURN'
            self.status = 'BOTH ARMS RETURNING TO SEE_CENTER - S STOP'
            print(f'See-center return worker PID={self.motion_child.pid}, log={logpath}', flush=True)
        except Exception as exc:
            print(f'Could not start see-center return: {exc}', flush=True)
            if self.motion_log_reader: self.motion_log_reader.close()
            if self.motion_log_writer: self.motion_log_writer.close()
            self.motion_log_reader = self.motion_log_writer = self.motion_child = None
            self.motion_stage = None

    def save_board_capture(self):
        """A key: save the live stereo pair + measured camera poses for
        make_stereo_correction.py (no ROI, SAM or robot motion)."""
        if not all(c.is_live() for c in self.cameras):
            print('A capture: both UDP cameras must be LIVE.', flush=True)
            return
        if any(c.frame.shape[:2] != (480, 640) for c in self.cameras):
            print('A capture: expected 640x480 frames.', flush=True)
            return
        try:
            T_wc, joints, stamps = self.live_poses.snapshot()
        except RuntimeError as exc:
            print(f'A capture: no fresh measured joints: {exc}', flush=True)
            return
        frames = [cam.frame.copy() for cam in self.cameras]
        folder = self.args.out_dir / f'board_capture_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}'
        folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / 'left.png'), frames[0])
        cv2.imwrite(str(folder / 'right.png'), frames[1])
        np.savez(folder / 'triangulation.npz', K=K, DIST=DIST,
                 T_world_left_camera_measured=T_wc[0],
                 T_world_right_camera_measured=T_wc[1],
                 measured_joint_positions_rad=np.asarray(joints),
                 measured_joint_stamps_unix=np.asarray(stamps))
        print(f'BOARD CAPTURE saved: {folder}\n'
              f'  next: ~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/'
              f'make_stereo_correction.py --capture {folder}', flush=True)
        self.status = 'BOARD CAPTURE SAVED - SEE TERMINAL'

    def start_place_after_lift(self):
        """After lift: carry the held object to the case, release, return.

        Runs right_place_trial/coordinated_right_case.py --from-pick for the
        arm that picked (the other arm yields). Replaces the old direct
        lift -> both see_center return (2026-11-19).
        """
        if not self.args.enable_execute:
            print('Place requires --enable-execute.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the active worker before place.', flush=True)
            return
        script = Path(__file__).resolve().parents[1] / 'right_place_trial' / 'coordinated_right_case.py'
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        out_dir = (self.preview_file.parent if self.preview_file is not None
                   else self.args.out_dir / 'place_runs' / stamp)
        out_dir.mkdir(parents=True, exist_ok=True)
        logpath = out_dir / f'place_{stamp}.log'
        try:
            self.motion_log_writer = logpath.open('w')
            self.motion_log_reader = logpath.open('r')
            self.motion_child = subprocess.Popen(
                [str(self.args.motion_python), str(script), '--execute', '--place',
                 '--from-pick', '--arm', self.args.arm],
                stdout=self.motion_log_writer, stderr=subprocess.STDOUT,
                start_new_session=True)
            self.motion_stage = 'PLACE'
            self.status = f'{self.args.arm.upper()} CARRYING TO CASE - S STOP'
            print(f'Place worker PID={self.motion_child.pid}, log={logpath}', flush=True)
        except Exception as exc:
            print(f'Could not start place worker: {exc}', flush=True)
            if self.motion_log_reader: self.motion_log_reader.close()
            if self.motion_log_writer: self.motion_log_writer.close()
            self.motion_log_reader = self.motion_log_writer = self.motion_child = None
            self.motion_stage = None

    def approach_camera_frame(self):
        if not self.approach_camera_enabled:
            return None
        camera = self.cameras[0 if self.args.arm == 'left' else 1]
        if camera.is_live():
            frame = camera.frame.copy()
            if self.args.auto_fine and self.execute_phase in ('fine_y', 'fine_x', 'fine_done'):
                return self.auto_fine_view(frame)
            return frame
        frame = np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)
        cv2.putText(frame, f'{self.args.arm.upper()} CAMERA: NO VIDEO',
                    (10, 32), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
        return frame

    def run(self):
        if self.capture_consumed:
            print('ROI already processed; press R to capture a NEW pair.', flush=True)
            return
        if self.frozen is None or not all(self.rois):
            print('Select TWO ROI boxes first.', flush=True)
            return
        self.status = 'SAM2 + LIGHTGLUE WORKING...'
        try:
            self._run()
        except Exception as exc:
            self.status = 'FAILED - SEE TERMINAL / R RESET'
            print(f'Failed: {type(exc).__name__}: {exc}', flush=True)

    def _run(self):
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        folder=self.args.out_dir / f'capture_{stamp}'
        folder.mkdir(parents=True,exist_ok=True)
        left,right=self.frozen
        cv2.imwrite(str(folder/'left.png'),left)
        cv2.imwrite(str(folder/'right.png'),right)
        board_pose_info = None
        fixed_pose_info = None
        if self.args.stereo_board_relative:
            self.T_wc, board_pose_info = board_relative_camera_poses(
                self.frozen, self.T_wc_measured)
            (folder/'board_relative_stereo.json').write_text(
                json.dumps(board_pose_info, indent=2) + '\n')
            print('Board-relative stereo: '
                  f'left/right board PnP RMSE '
                  f'{board_pose_info["left"]["pnp_rmse_px"]:.2f}/'
                  f'{board_pose_info["right"]["pnp_rmse_px"]:.2f} px; '
                  f'measured-pose board difference '
                  f'{board_pose_info["old_board_world_difference_mm"]:.1f} mm, '
                  f'{board_pose_info["old_board_world_rotation_difference_deg"]:.2f} deg',
                  flush=True)
        elif self.args.stereo_fixed_see_center:
            self.T_wc, fixed_pose_info = fixed_see_center_camera_poses(
                self.T_wc_measured, self.frozen_joints, DEFAULT_STEREO_CORRECTION)
            print('Fixed marker-free stereo: '
                  f'max joint difference {fixed_pose_info["max_joint_difference_deg"]:.2f} deg; '
                  f'source {fixed_pose_info["source_capture"]}', flush=True)

        masks=[]
        scores=[]
        for idx, (im, roi) in enumerate(zip(self.frozen,self.rois)):
            print(f'SAM2 processing {("LEFT","RIGHT")[idx]}...',flush=True)
            mask,score=segment_with_sam(self.sam_predictor,im,roi)
            masks.append(mask)
            scores.append(score)
            cv2.imwrite(str(folder/f'{("left","right")[idx]}_mask.png'),
                        (mask*255).astype(np.uint8))
            print(f'  mask pixels={mask.sum()}, SAM score={score:.3f}',flush=True)

        def match_pass(images, erode_px, max_keypoints, label):
            with torch.inference_mode():
                left_features, left_total, left_kept = mask_filtered_features(
                    self.extractor, images[0], masks[0], self.rois[0],
                    self.device, erode_px, max_keypoints)
                print(f'{label} LEFT SuperPoint keypoints: {left_total} total, '
                      f'{left_kept} inside mask', flush=True)
                angles = (0, 90, 180, 270) if self.args.match_rotation else (0,)
                candidates = []
                x1, y1, x2, y2 = self.rois[1]
                right_crop = images[1][y1:y2, x1:x2]
                right_mask = masks[1][y1:y2, x1:x2].astype(np.uint8)
                for angle in angles:
                    try:
                        if angle:
                            rotated = rotate_match_crop(right_crop, angle)
                            rotated_mask = rotate_match_crop(right_mask, angle)
                            h, w = rotated.shape[:2]
                            right_features, right_total, right_kept = mask_filtered_features(
                                self.extractor, rotated, rotated_mask, (0, 0, w, h),
                                self.device, erode_px, max_keypoints)
                        else:
                            right_features, right_total, right_kept = mask_filtered_features(
                                self.extractor, images[1], masks[1], self.rois[1],
                                self.device, erode_px, max_keypoints)
                    except RuntimeError as exc:
                        print(f'{label} RIGHT {angle} deg skipped: {exc}', flush=True)
                        continue
                    print(f'{label} RIGHT {angle} deg SuperPoint keypoints: '
                          f'{right_total} total, {right_kept} inside mask', flush=True)
                    m01 = self.matcher({'image0': left_features, 'image1': right_features})
                    f0, f1, mm = [rbd(x) for x in (left_features, right_features, m01)]
                    matches = mm['matches']
                    left_pts = f0['keypoints'][matches[:, 0]].cpu().numpy().astype(np.float64)
                    right_pts = f1['keypoints'][matches[:, 1]].cpu().numpy().astype(np.float64)
                    if angle:
                        right_pts = unrotate_match_points(
                            right_pts, angle, right_crop.shape[1], right_crop.shape[0])
                    left_pts += np.array(self.rois[0][:2])
                    right_pts += np.array(self.rois[1][:2])
                    inliers = match_homography_inliers(left_pts, right_pts)
                    candidates.append((angle, left_pts, right_pts, inliers))
                    if self.args.match_rotation:
                        print(f'{label} RIGHT {angle} deg: raw {len(left_pts)}, '
                              f'2D-consistent {int(inliers.sum())}', flush=True)
                if not candidates:
                    raise RuntimeError('No right ROI rotation produced enough features')
            if self.args.match_rotation:
                angle, left_pts, right_pts, inliers = max(
                    candidates, key=lambda item: (int(item[3].sum()),
                    float(item[3].mean()) if len(item[3]) else 0.0))
                if int(inliers.sum()) < 8:
                    raise RuntimeError(
                        f'No reliable rotation: {int(inliers.sum())}/{len(inliers)} '
                        '2D-consistent matches (need at least 8)')
                left_pts, right_pts = left_pts[inliers], right_pts[inliers]
                print(f'{label} selected RIGHT rotation {angle} deg: '
                      f'{len(left_pts)} 2D candidates', flush=True)
            else:
                angle, left_pts, right_pts, _ = candidates[0]
            geometry = triangulate_matches(left_pts, right_pts, self.T_wc[0], self.T_wc[1],
                                           self.args.reproj_px, self.args.min_angle_deg)
            print(f'{label} LightGlue matches: {len(left_pts)}, '
                  f'valid 3D: {int(geometry["valid"].sum())}', flush=True)
            return left_pts, right_pts, geometry, angle

        try:
            pts0, pts1, tri, match_rotation_deg = match_pass(self.frozen, self.args.mask_erode,
                                         self.args.max_keypoints, 'ORIGINAL')
        except RuntimeError as exc:
            print(f'Original matching failed: {exc}; trying CLAHE.', flush=True)
            pts0 = np.empty((0, 2), dtype=np.float64)
            pts1 = np.empty((0, 2), dtype=np.float64)
            tri = triangulate_matches(pts0, pts1, self.T_wc[0], self.T_wc[1])
            match_rotation_deg = None
        if self.args.retry_clahe and int(tri['valid'].sum()) < max(self.args.min_valid_points + 2, 8):
            try:
                enhanced = [contrast_enhanced_image(image) for image in self.frozen]
                alt0, alt1, alt_tri, alt_angle = match_pass(
                    enhanced, max(0, self.args.mask_erode - 2),
                    max(self.args.max_keypoints, 4096), 'CLAHE')
                original_count = int(tri['valid'].sum())
                alternate_count = int(alt_tri['valid'].sum())
                original_error = (float(np.median(np.maximum(
                    tri['reproj_left'][tri['valid']], tri['reproj_right'][tri['valid']])))
                    if original_count else float('inf'))
                alternate_error = (float(np.median(np.maximum(
                    alt_tri['reproj_left'][alt_tri['valid']],
                    alt_tri['reproj_right'][alt_tri['valid']])))
                    if alternate_count else float('inf'))
                if (alternate_count > original_count or
                        (alternate_count == original_count and alternate_error < original_error)):
                    pts0, pts1, tri, match_rotation_deg = alt0, alt1, alt_tri, alt_angle
                    print(f'Using CLAHE matches: {alternate_count} valid vs '
                          f'{original_count} original.', flush=True)
                else:
                    print(f'Keeping original matches: {original_count} valid vs '
                          f'{alternate_count} CLAHE.', flush=True)
            except RuntimeError as exc:
                print(f'CLAHE matching failed: {exc}; keeping original result.', flush=True)
        print(f'LightGlue object-only matches used: {len(pts0)}', flush=True)
        print(f'Valid 3D matches: {int(tri["valid"].sum())}/{len(pts0)}',flush=True)
        if len(pts0) and not np.any(tri['valid']):
            both_reproj = np.maximum(tri['reproj_left'], tri['reproj_right'])
            print('3D rejection diagnostics: '
                  f'positive depths {int(((tri["depth_left_m"] > 0.01) & (tri["depth_right_m"] > 0.01)).sum())}/{len(pts0)}, '
                  f'ray angle >= {self.args.min_angle_deg:g} deg '
                  f'{int((tri["angle_deg"] >= self.args.min_angle_deg).sum())}/{len(pts0)}, '
                  f'both reprojection <= {self.args.reproj_px:g} px '
                  f'{int(((tri["reproj_left"] <= self.args.reproj_px) & (tri["reproj_right"] <= self.args.reproj_px)).sum())}/{len(pts0)}',
                  flush=True)
            if np.isfinite(both_reproj).any():
                print(f'Worst-side reprojection error [px]: '
                      f'min {np.nanmin(both_reproj):.1f}, '
                      f'median {np.nanmedian(both_reproj):.1f}, '
                      f'max {np.nanmax(both_reproj):.1f}', flush=True)
        valid_ids = np.flatnonzero(tri['valid'])
        if len(valid_ids):
            left_depth_mm = tri['depth_left_m'][valid_ids] * 1000
            right_depth_mm = tri['depth_right_m'][valid_ids] * 1000
            print('Valid match optical depths [mm] (camera-forward Z):', flush=True)
            print('match_id, left_depth_mm, right_depth_mm, right_base_z_mm', flush=True)
            for idx, left_depth, right_depth in zip(valid_ids, left_depth_mm, right_depth_mm):
                print(f'{idx}, {left_depth:.2f}, {right_depth:.2f}, '
                      f'{tri["xyz_m"][idx, 2] * 1000:.2f}', flush=True)
            print('Depth median [mm] left/right:',
                  round(float(np.median(left_depth_mm)), 2),
                  round(float(np.median(right_depth_mm)), 2), flush=True)
        preview,reason=estimate_grasp_preview(
            masks,self.T_wc,tri,self.args.min_valid_points,self.args.arm,self.args.yaw_camera)
        if preview is None:
            print('Cannot estimate yaw:',reason,flush=True)
            self.status='NO RELIABLE 3D/YAW - R RETRY'
        else:
            center=preview['center_xyz_m']*1000
            print('========================================',flush=True)
            print('Right-base preview center (mm) XYZ:',np.round(center,2),flush=True)
            print('Selected moving arm / yaw camera:', self.args.arm.upper(), '/', preview['selected_yaw_camera'].upper(), flush=True)
            print('View scores:', preview['yaw_view_scores'], flush=True)
            print('View details:', preview['yaw_view_details'], flush=True)
            print('Per-camera yaw in right_base_link:', preview['yaw_by_side'], flush=True)
            print(f'Object LONG axis yaw (deg): {preview["object_long_yaw_deg"]:+.2f}',flush=True)
            print(f'Proposed GRIPPER CLOSING axis yaw (deg): {preview["closing_axis_yaw_deg"]:+.2f}',flush=True)
            print(f'Yaw estimation: {preview["source"]}',flush=True)
            if preview['length_m']:
                print('Approx silhouette length / width [mm]:',
                      round(preview['length_m']*1000,1),
                      round(preview['width_m']*1000,1),flush=True)
            for warning in preview['warnings']:
                print('CAUTION:',warning,flush=True)
            print('PLAN preview will be requested; no physical movement.',flush=True)
            self.status='PREVIEW SAVED - R NEW CAPTURE'

        annotated=[overlay_mask(im,mask) for im,mask in zip(self.frozen,masks)]
        rng=np.random.default_rng(42)
        for k,(p0,p1) in enumerate(zip(pts0,pts1)):
            color=(90,110,110) if not tri['valid'][k] else tuple(int(x) for x in rng.integers(80,256,3))
            for idx,pt in enumerate((p0,p1)):
                cv2.circle(annotated[idx],tuple(np.round(pt).astype(int)),2,color,-1)
        if preview:
            for idx in range(2):
                draw_grasp_axes(annotated[idx],self.T_wc[idx],preview['center_xyz_m'],
                                preview['object_long_yaw_deg'],preview['length_m'],preview['width_m'])
        pairs=np.hstack(annotated)
        shown_ids = np.linspace(0, len(pts0) - 1, min(50, len(pts0)), dtype=int) if len(pts0) else []
        for k in shown_ids:
            p0, p1 = pts0[k], pts1[k]
            a=tuple(np.round(p0).astype(int))
            b=tuple(np.round(p1+np.array([PANEL_W,0])).astype(int))
            color=(100,190,130) if tri['valid'][k] else (110,110,110)
            cv2.line(pairs,a,b,color,1,cv2.LINE_AA)
        cv2.rectangle(pairs,(0,0),(PANEL_W*2,33),(15,15,15),-1)
        cv2.putText(pairs,f'SAM2 mask + LightGlue | matches {len(pts0)} | 3D valid {int(tri["valid"].sum())}',
                    (9,24),cv2.FONT_HERSHEY_SIMPLEX,.66,(255,255,255),2)
        if preview is not None:
            xyz = preview['center_xyz_m'] * 1000
            cv2.rectangle(pairs, (0, PANEL_H-52), (PANEL_W*2, PANEL_H), (15,15,15), -1)
            msg = (f'XYZ approx [mm]: {xyz[0]:+.1f}, {xyz[1]:+.1f}, {xyz[2]:+.1f}'
                   f' | long yaw: {preview["object_long_yaw_deg"]:+.1f}'
                   f' | closing axis: {preview["closing_axis_yaw_deg"]:+.1f} deg')
            cv2.putText(pairs, msg, (9,PANEL_H-25), cv2.FONT_HERSHEY_SIMPLEX,
                        .52, (255,255,255), 1)
            if len(preview['warnings']) > 1:
                cv2.putText(pairs, 'CHECK YAW RELIABILITY (see terminal)',
                            (9, PANEL_H-6), cv2.FONT_HERSHEY_SIMPLEX,
                            .41, (0,140,255), 1)
        self.result=pairs
        self.topview=topdown_plot(tri,preview)
        cv2.imwrite(str(folder/'sam_lightglue_preview.png'),pairs)
        cv2.imwrite(str(folder/'topdown_grasp_preview.png'),self.topview)
        save_xyz_csv(folder/'triangulation.csv',pts0,pts1,tri)
        np.savez_compressed(folder/'triangulation.npz',
                pts_left=pts0,pts_right=pts1,xyz_right_base_m=tri['xyz_m'],
                valid_mask=tri['valid'], depth_left_m=tri['depth_left_m'],
                depth_right_m=tri['depth_right_m'],
                T_world_left_camera=self.T_wc[0],
                T_world_right_camera=self.T_wc[1],K=K,DIST=DIST,
                T_world_left_camera_measured=self.T_wc_measured[0],
                T_world_right_camera_measured=self.T_wc_measured[1],
                stereo_board_relative=self.args.stereo_board_relative,
                stereo_fixed_see_center=self.args.stereo_fixed_see_center,
                camera_calibration=str(self.args.camera_calibration),
                handeye=str(self.args.right_handeye if self.args.arm == 'right' else self.args.handeye),
                T_gripper_camera=T_GRIPPER_CAMERA_BY_SIDE[self.args.arm],
                handeye_left=str(self.args.handeye),
                handeye_right=str(self.args.right_handeye),
                T_gripper_camera_left=T_GRIPPER_CAMERA_BY_SIDE['left'],
                T_gripper_camera_right=T_GRIPPER_CAMERA_BY_SIDE['right'],
                measured_joint_positions_rad=self.frozen_joints,
                measured_joint_stamps_unix=self.frozen_stamps,
                left_mask=masks[0],right_mask=masks[1],
                match_rotation_deg=-1 if match_rotation_deg is None else match_rotation_deg,
                roi_left=self.rois[0],roi_right=self.rois[1])
        document={
            'frame':'right_base_link',
            'units':'XYZ in mm, angles in degrees',
            'camera_calibration_npz':str(self.args.camera_calibration),
            'stereo_pose_source': ('same-capture ChArUco board, left world anchor'
                                   if self.args.stereo_board_relative else
                                   'fixed see_center correction, left world anchor'
                                   if self.args.stereo_fixed_see_center else
                                   'measured joints + hand-eye'),
            'board_relative_stereo':board_pose_info,
            'fixed_stereo_correction':fixed_pose_info,
            'handeye_json':str(self.args.right_handeye if self.args.arm == 'right' else self.args.handeye),
            'handeye_json_by_side':{'left':str(self.args.handeye),
                                    'right':str(self.args.right_handeye)},
            'handeye_internal_consistency_passed':self.args.handeye_passed[self.args.arm],
            'handeye_internal_consistency_by_side':self.args.handeye_passed,
            'sam_score_left_right':scores,
            'rois':{'left':self.rois[0],'right':self.rois[1]},
            'object_only_lightglue_matches':len(pts0),
            'match_rotation_enabled':self.args.match_rotation,
            'match_rotation_deg':match_rotation_deg,
            'valid_triangulated_matches':int(tri['valid'].sum()),
            'grasp_preview':None if preview is None else {
                'center_xyz_mm':(preview['center_xyz_m']*1000).tolist(),
                'object_long_axis_yaw_deg_mod_180':preview['object_long_yaw_deg'],
                'proposed_closing_axis_yaw_deg_mod_180':preview['closing_axis_yaw_deg'],
                'source':preview['source'],
                'selected_arm':self.args.arm,
                'yaw_by_side':preview['yaw_by_side'],
                'selected_yaw_camera':preview['selected_yaw_camera'],
                'yaw_view_scores':preview['yaw_view_scores'],
                'yaw_view_details':preview['yaw_view_details'],
                'aspect_ratio':preview['aspect_ratio'],
                'approx_length_mm':None if preview['length_m'] is None else preview['length_m']*1000,
                'approx_width_mm':None if preview['width_m'] is None else preview['width_m']*1000,
                'warnings':preview['warnings'],
            },
            'WARNING':'Visualization only. No calibrated real gripper TCP pose or grasp clearance.'
        }
        (folder/'preview.json').write_text(json.dumps(document,indent=2,ensure_ascii=False))
        print('Saved:',folder,flush=True)
        self.capture_consumed = True
        if preview is not None:
            approach_mode = 'camera_center' if self.args.camera_center else 'tcp_above_target'
            observation_xy = (camera_center_offset_tcp_xy(
                self.args.urdf, self.args.arm,
                T_GRIPPER_CAMERA_BY_SIDE[self.args.arm], self.args.above_mm / 1000.)
                if self.args.camera_center else np.zeros(2))
            print(f'Approach mode: {approach_mode}; TCP XY offset [mm]: '
                  f'{np.round(observation_xy * 1000, 1).tolist()}', flush=True)
            state = {
                'frame': 'right_base_link',
                'object_type': None,
                'center_xyz_mm': (preview['center_xyz_m'] * 1000).tolist(),
                'closing_axis_yaw_deg': float(preview['closing_axis_yaw_deg']),
                'selected_arm': self.args.arm,
                'yaw_by_side': preview['yaw_by_side'],
                'selected_yaw_camera': preview['selected_yaw_camera'],
                'yaw_view_scores': preview['yaw_view_scores'],
                'yaw_view_details': preview['yaw_view_details'],
                'object_long_yaw_deg': float(preview['object_long_yaw_deg']),
                'valid_points': int(tri['valid'].sum()),
                'warnings': list(preview['warnings']),
                'capture_time_unix': float(self.capture_time_unix),
                'joint_positions_rad': {
                    f'{side}_{i}_Joint': float(self.frozen_joints[index, i-1])
                    for index, side in enumerate(('left', 'right')) for i in range(1, 7)
                },
                'joint_stamps_unix': np.asarray(self.frozen_stamps).tolist(),
                'urdf': str(self.args.urdf),
                'approach_mode': approach_mode,
                'observation_tcp_xy_m': observation_xy.tolist(),
                'observation_above_mm': float(self.args.above_mm),
                'source': str(folder),
            }
            self.preview_file = folder / 'moveit_target.json'
            self.preview_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
            self.status = 'MATCHED - 1 CHOCOLATE / 2 MENTOS'
            print('Matching complete. Press 1 for chocolate or 2 for Mentos to plan.', flush=True)
        else:
            self.status='NO GRASP PREVIEW - R RECAPTURE'

    def select_object_type(self, object_type):
        if self.preview_file is None or self.execute_phase != 'approach':
            print('Select the object after a successful stereo match, before approach.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the current plan to finish before changing the object.', flush=True)
            return
        self.selected_object_type = object_type
        self.args.object_type = object_type
        state = json.loads(self.preview_file.read_text())
        state['object_type'] = object_type
        self.preview_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        self.reset_auto_fine(clear_moves=True)
        self.plan_ready = False
        self.status = f'{object_type.upper()} SELECTED - PLANNING'
        print(self.status, flush=True)
        self.start_motion(execute=False)

    def select_yaw_camera(self, mode):
        """Reuse this capture and replan with the inspected camera yaw."""
        if self.preview_file is None:
            print('No captured yaw to select yet.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait until the current MoveIt plan finishes.', flush=True)
            return
        state = json.loads(self.preview_file.read_text())
        scores = state.get('yaw_view_scores', {})
        side = max(scores, key=scores.get) if mode == 'auto' and scores else mode
        yaw_data = state.get('yaw_by_side', {}).get(side)
        if yaw_data is None:
            print(f'No {side.upper()} yaw estimate in this capture; recapture.', flush=True)
            return
        state['selected_yaw_camera'] = side
        state['closing_axis_yaw_deg'] = yaw_data['closing_axis_yaw_deg']
        state['object_long_yaw_deg'] = yaw_data['object_long_yaw_deg']
        if 'center_xy_mm' in yaw_data:
            state['center_xyz_mm'][:2] = yaw_data['center_xy_mm']
        self.preview_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        self.args.yaw_camera = mode
        self.plan_ready = False
        print(f'Yaw camera changed to {side.upper()} (mode={mode}); replanning. '
              f'Closing yaw={yaw_data["closing_axis_yaw_deg"]:+.2f} deg', flush=True)
        self.start_motion(execute=False)

    def start_grasp(self, automatic=False):
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the current motion to finish before G.', flush=True)
            return
        if self.args.auto_fine and not self.auto_fine_ok:
            print('G requires AUTO FINE OK. Inspect camera and robot, or restart with --no-auto-fine.', flush=True)
            return
        if self.preview_file is None or not (self.preview_file.parent / 'approach_complete.json').exists():
            print('G requires a completed approach. Execute E first.', flush=True)
            return
        target = json.loads(self.preview_file.read_text())
        if target.get('approach_mode') != 'tcp_above_target':
            print('G requires TCP-above-target approach mode.', flush=True)
            return
        if self.execute_phase in ('descend', 'close', 'grasp') or (self.preview_file.parent / 'descend_attempt.json').exists():
            print('Grasp already started or attempted; press C to restart.', flush=True)
            return
        if self.grasp_preview_file != self.preview_file:
            try:
                self.live_poses.poll()
                sample = self.live_poses.samples.get(self.args.arm)
                if sample is None or time.monotonic() - sample[0] > 1.5:
                    raise RuntimeError(f'No fresh {self.args.arm} measured joints')
                base = (self.live_poses.T_right_left if self.args.arm == 'left'
                        else np.eye(4))
                tcp = (base @ self.live_poses.wrist_transform(self.args.arm, sample[2]) @
                       fixed_6_to_tcp(self.args.urdf, self.args.arm))
                current_z_mm = float(tcp[2, 3] * 1000.)
                target_z_mm = grasp_tcp_target_z_m(self.args.arm) * 1000.
                move_mm = current_z_mm - target_z_mm
                print(f'G PREVIEW {self.args.arm.upper()}: current TCP Z={current_z_mm:.1f} mm, '
                      f'target TCP Z={target_z_mm:.1f} mm, '
                      f'vertical move={move_mm:+.1f} mm (+down).', flush=True)
                if not -10. <= move_mm <= 100.:
                    print('G vertical move is outside the configured -10..100 mm range.', flush=True)
                    return
            except (RuntimeError, ValueError) as exc:
                print(f'G preview unavailable: {exc}', flush=True)
                return
            self.grasp_preview_file = self.preview_file
            self.status = ('GRASP HEIGHT CHECKED - AUTO DESCENT STARTING'
                           if automatic else 'G PREVIEW READY - PRESS G AGAIN TO DESCEND AND GRIP')
            print(self.status, flush=True)
            return
        self.grasp_preview_file = None
        # Auto mode: one worker does descend + close + lift (saves two worker
        # start-ups, ~10 s; 2026-11-19). Manual G keeps the separate steps.
        self.execute_phase = ('grasp' if automatic and self.args.auto_fine
                              and not self.auto_grasp_cancelled else 'descend')
        self.auto_align_pending = False
        self.fine_request = None
        self.status = ('AUTO GRASP - DESCEND, CLOSE, LIFT' if self.execute_phase == 'grasp'
                       else 'G GRASP - CALIBRATED TCP Z, THEN CLOSE')
        print(self.status, flush=True)
        self.start_motion(execute=True)

    def maybe_auto_grasp(self):
        if (not self.args.auto_fine or not self.auto_fine_ok or
                self.auto_grasp_attempted or self.auto_grasp_cancelled or
                self.execute_phase != 'fine_done' or
                self.motion_child is not None or
                (self.auto_grasp_ready_at is not None and
                 time.monotonic() < self.auto_grasp_ready_at)):
            return
        self.auto_grasp_attempted = True
        self.start_grasp(automatic=True)  # Check measured pose and calibrated Z.
        if self.grasp_preview_file != self.preview_file:
            self.status = 'AUTO GRASP PREVIEW FAILED - CHECK TERMINAL AND ROBOT'
            print(self.status, flush=True)
            return
        self.start_grasp(automatic=True)  # Execute descent and gripper close.
        if self.motion_child is None:
            self.status = 'AUTO GRASP DID NOT START - CHECK TERMINAL AND ROBOT'
            print(self.status, flush=True)

    def start_lift(self):
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the current motion to finish before L.', flush=True)
            return
        if self.preview_file is None or not (self.preview_file.parent / 'gripper_close_complete.json').exists():
            print('L requires a completed G grasp.', flush=True)
            return
        closed = json.loads((self.preview_file.parent / 'gripper_close_complete.json').read_text())
        if closed.get('arm') != self.args.arm:
            print('L close record does not match the selected arm.', flush=True)
            return
        self.execute_phase = 'lift'
        self.status = ('AUTO LIFT 5 CM, THEN PLACE IN CASE'
                       if self.args.auto_fine else 'L LIFT 5 CM, THEN PLACE IN CASE')
        print(self.status, flush=True)
        self.start_motion(execute=True)

    def start_open_gripper(self):
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('Wait for the current motion to finish before O.', flush=True)
            return
        if not self.args.enable_execute:
            print('EXECUTE DISABLED: relaunch with --enable-execute first.', flush=True)
            return
        worker = Path(__file__).with_name('open_gripper.py')
        log_dir = self.preview_file.parent if self.preview_file is not None else self.args.out_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        logpath = log_dir / f'gripper_open_{int(time.time() * 1000)}.log'
        try:
            self.motion_log_writer = logpath.open('w')
            self.motion_log_reader = logpath.open('r')
            self.motion_child = subprocess.Popen(
                [str(self.args.motion_python), str(worker), '--arm', self.args.arm],
                stdout=self.motion_log_writer, stderr=subprocess.STDOUT, start_new_session=True)
            self.motion_stage = 'OPEN'
            self.status = f'OPENING {self.args.arm.upper()} GRIPPER'
            print(f'Gripper open worker PID={self.motion_child.pid}, log={logpath}', flush=True)
        except Exception as exc:
            print(f'Gripper open worker failed to start: {exc}', flush=True)
            if self.motion_log_reader: self.motion_log_reader.close()
            if self.motion_log_writer: self.motion_log_writer.close()
            self.motion_log_reader = self.motion_log_writer = self.motion_child = None
            self.status = 'GRIPPER OPEN WORKER FAILED'

    def start_motion(self, execute=False):
        if self.preview_file is None:
            print('No valid preview to send to MoveIt.', flush=True)
            return
        if self.selected_object_type is None and self.execute_phase == 'approach':
            print('Press 1 for chocolate or 2 for Mentos after matching.', flush=True)
            return
        if not execute and self.execute_phase != 'approach':
            print('After approach, automatic fine alignment uses the live camera.', flush=True)
            return
        if self.motion_child is not None and self.motion_child.poll() is None:
            print('MoveIt worker still running.', flush=True)
            return
        if execute and not self.args.enable_execute:
            print('EXECUTE DISABLED: relaunch with --enable-execute first.', flush=True)
            return
        if execute and self.preview_file is not None:
            summary_path = self.preview_file.parent / 'moveit_plan_summary.json'
            try:
                summary = json.loads(summary_path.read_text())
            except (OSError, ValueError):
                summary = {}
            if summary.get('execution_allowed') is False:
                print('EXECUTION BLOCKED: ' + '; '.join(summary.get('execution_block_reasons', []))
                      + '. R recapture; current plan remains preview-only.', flush=True)
                return
        if execute and self.execute_phase == 'done':
            print('Approach complete; press R for a new capture.', flush=True)
            return
        if execute and self.execute_phase == 'approach' and not self.plan_ready:
            print('No successful PLAN ONLY result yet. Press P first.', flush=True)
            return
        # The live pose snapshots are refreshed to prevent commands after robot motion.
        for _ in range(8):
            self.live_poses.poll()
        try:
            _, joints, stamps = self.live_poses.snapshot()
        except RuntimeError as exc:
            print(f'PLAN/EXECUTE BLOCKED: {exc}', flush=True)
            return
        delta = float(np.max(np.abs(joints - self.frozen_joints)))
        if (not execute or self.execute_phase == 'approach') and delta > np.deg2rad(self.args.max_state_change_deg):
            print(f'PLAN/EXECUTE BLOCKED: arm moved {np.rad2deg(delta):.2f} deg after capture; R recapture.', flush=True)
            self.plan_ready = False
            return
        if (not execute or self.execute_phase == 'approach') and time.time() - self.capture_time_unix > self.args.max_capture_age:
            print('PLAN/EXECUTE BLOCKED: capture expired; R recapture.', flush=True)
            self.plan_ready = False
            return
        worker = Path(__file__).with_name('move_right_sam_yaw.py')
        cmd = [str(self.args.motion_python), str(worker),
               '--target-file', str(self.preview_file),
               '--phase', self.execute_phase if execute else 'approach',
               '--arm', self.args.arm,
               '--yaw-camera', self.args.yaw_camera,
               '--peer-home',
               '--group', self.args.group, '--tip-link', self.args.tip_link,
               '--move-action', self.args.move_action,
               '--execute-action', self.args.execute_action,
               '--display-topic', self.args.display_topic,
               '--tcp-closing-axis', self.args.tcp_closing_axis,
               '--yaw-offset-deg', str(self.args.yaw_offset_deg),
               '--above-mm', str(self.args.above_mm),
               '--max-joint-change-deg', str(self.args.max_joint_change_deg),
               '--max-state-change-deg', str(self.args.max_state_change_deg),
               '--max-capture-age', str(self.args.max_capture_age),
               '--min-valid-points', str(self.args.min_valid_points),
               '--out-dir', str(self.preview_file.parent)]
        if self.execute_phase == 'approach':
            worker = Path(__file__).with_name('move_dual_sam_yaw_real.py')
            cmd = [str(self.args.motion_python), str(worker),
                   '--target-file', str(self.preview_file),
                   '--out-dir', str(self.preview_file.parent),
                   '--above-mm', str(self.args.above_mm),
                   '--yaw-camera', self.args.yaw_camera,
                   '--tcp-closing-axis', self.args.tcp_closing_axis,
                   '--yaw-offset-deg', str(self.args.yaw_offset_deg),
                   '--max-capture-age', str(self.args.max_capture_age),
                   '--max-state-change-deg', str(self.args.max_state_change_deg)]
            if execute:
                cmd.append('--execute-saved-plan')
        if self.args.allow_gripper_tilt:
            if self.execute_phase == 'approach':
                cmd.append('--allow-gripper-tilt')
            else:
                cmd.extend(['--orientation-tolerance-deg', '8'])
        if self.args.allow_uncertain_yaw and self.execute_phase != 'approach':
            cmd.append('--allow-uncertain-yaw')
        if self.args.minimal_drag and self.execute_phase != 'approach':
            cmd.append('--minimal-drag')
        if execute:
            self.grasp_preview_file = None
            if self.execute_phase != 'approach':
                cmd.append('--execute')
            self.plan_ready = False
            self.approach_camera_enabled = True
        self.motion_stage = 'EXECUTE' if execute else 'PLAN'
        self.status = f'MOVEIT {self.args.arm.upper()}: ' + self.motion_stage
        logpath = self.preview_file.parent / (f'moveit_{self.execute_phase}.log' if execute else 'moveit_plan.log')
        try:
            self.motion_log_writer = logpath.open('w')
            self.motion_log_reader = logpath.open('r')
            self.motion_child = subprocess.Popen(
                cmd, stdout=self.motion_log_writer, stderr=subprocess.STDOUT,
                start_new_session=True)
            print(f'MoveIt {self.motion_stage} worker PID={self.motion_child.pid}, log={logpath}', flush=True)
        except Exception as exc:
            print('Worker failed to start:', exc, flush=True)
            if self.motion_log_reader: self.motion_log_reader.close()
            if self.motion_log_writer: self.motion_log_writer.close()
            self.motion_log_reader = self.motion_log_writer = self.motion_child = None
            self.status = 'MOVEIT WORKER FAILED'

    def _timing(self, code):
        """Print and log how long the finished worker took (2026-11-19)."""
        started = getattr(self, '_timing_started', None)
        if started is None:
            return
        elapsed = time.monotonic() - started
        since = (time.time() - self.capture_time_unix
                 if getattr(self, 'capture_time_unix', None) else float('nan'))
        stage = f'{self.motion_stage}/{self.execute_phase}'
        print(f'TIMING {stage}: {elapsed:.1f} s (exit={code}); {since:.1f} s since capture',
              flush=True)
        try:
            log = self.args.out_dir / 'timing_log.csv'
            new = not log.exists()
            with log.open('a') as handle:
                if new:
                    handle.write('unix_time,stage,elapsed_s,since_capture_s,exit,arm\n')
                handle.write(f'{time.time():.1f},{stage},{elapsed:.2f},{since:.2f},{code},'
                             f'{self.args.arm}\n')
        except OSError:
            pass
        self._timing_started = None

    def poll_motion(self):
        if self.motion_child is None:
            return
        if getattr(self, '_timing_pid', None) != self.motion_child.pid:
            self._timing_pid = self.motion_child.pid
            self._timing_started = time.monotonic()
        chunk = self.motion_log_reader.read()
        if chunk:
            print(chunk, end='', flush=True)
        code = self.motion_child.poll()
        if code is None:
            return
        rest = self.motion_log_reader.read()
        if rest:
            print(rest, end='', flush=True)
        worker_log = ''
        if self.motion_stage == 'FINE' and code != 0:
            try:
                worker_log = Path(self.motion_log_reader.name).read_text()
            except OSError:
                pass
        self._timing(code)
        self.motion_log_reader.close()
        self.motion_log_writer.close()
        self.motion_log_reader = self.motion_log_writer = self.motion_child = None
        next_phase = None
        if self.motion_stage == 'PLACE':
            print(f'Place worker exit={code}', flush=True)
            self.motion_stage = None
            if code == 0:
                self.reset()
                for name in (RESULT_WINDOW, TOP_WINDOW, APPROACH_CAMERA_WINDOW):
                    try: cv2.destroyWindow(name)
                    except cv2.error: pass
                self.status = 'PLACED - BOTH SEE_CENTER - DRAW NEW LEFT + RIGHT ROIS'
            else:
                self.status = 'PLACE FAILED - CHECK LOG AND ROBOT (C = return to see_center)'
            return
        if self.motion_stage == 'RETURN':
            print(f'See-center return worker exit={code}', flush=True)
            self.motion_stage = None
            if code == 0:
                self.reset()
                for name in (RESULT_WINDOW, TOP_WINDOW, APPROACH_CAMERA_WINDOW):
                    try: cv2.destroyWindow(name)
                    except cv2.error: pass
                self.status = 'BOTH SEE_CENTER - DRAW NEW LEFT + RIGHT ROIS'
            else:
                self.status = 'SEE_CENTER RETURN FAILED - CHECK LOG AND ROBOT'
            return
        if self.motion_stage == 'OPEN':
            self.status = ('GRIPPER OPEN - CAMERA LIVE' if code == 0
                           else 'GRIPPER OPEN FAILED - CHECK LOG AND ROBOT')
            print(f'Gripper open worker exit={code}', flush=True)
            self.motion_stage = None
            return
        if self.motion_stage == 'FINE':
            was_mentos_search = self.mentos_search_pending
            self.mentos_search_pending = False
            if self.args.auto_fine and code == 0:
                self.reset_auto_fine()
            elif self.args.auto_fine:
                reason = ('operator stopped fine' if self.fine_stop_requested else
                          'Pi SDK XY failed; check robot pose and Pi log'
                          if 'Native SDK XY prediction failed' in worker_log else
                          'fine worker failed or stopped; check log')
                self.pause_auto_fine(reason)
            if code == 0:
                axis = self.execute_phase[-1]
                self.execute_phase = 'fine_done'
                self.fine_request = None
                self.align_roi = None
                self.status = ('MENTOS SEARCH MOVED - RE-DETECTING'
                               if was_mentos_search else
                               f'FINE {axis.upper()} COMPLETE - AUTO CHECKING RANGE')
            else:
                self.fine_request = None
                self.status = ('FINE STOP REQUESTED - CHECK ROBOT' if self.fine_stop_requested
                               else 'FINE FAILED - CHECK LOG AND ROBOT')
            print(f'Fine worker exit={code}', flush=True)
            self.motion_stage = None
            return
        if self.motion_stage == 'PLAN' and code == 0:
            self.plan_ready = True
            summary_path = self.preview_file.parent / 'moveit_plan_summary.json'
            try:
                summary = json.loads(summary_path.read_text())
            except (OSError, ValueError):
                summary = {}
            if summary.get('dual_auto_real') and summary.get('selected_arm') in ('left', 'right'):
                selected = summary['selected_arm']
                self.args.arm = selected
                self.args.group = f'arm_{selected}'
                self.args.tip_link = f'{selected}_TCP'
                target = json.loads(self.preview_file.read_text())
                target['selected_arm'] = selected
                self.preview_file.write_text(json.dumps(target, indent=2, ensure_ascii=False))
                print(f'AUTO SELECTED {selected.upper()} arm; peer={summary["yield_strategy"]}; '
                      f'routes={[row["route"] for row in summary["phases"]]}', flush=True)
            if summary.get('execution_allowed') is False:
                self.status = 'RVIZ PLAN OK - EXECUTION BLOCKED - SEE LOG'
                print('RViz preview ready. EXECUTION BLOCKED: ' + '; '.join(
                    summary.get('execution_block_reasons', [])), flush=True)
            else:
                self.status = 'PLAN OK - E EXECUTE (if enabled) / R RECAPTURE'
                print('Plan successful. Inspect MoveIt RViz path. Press E only if safe & enabled.', flush=True)
        else:
            self.plan_ready = False
            if code == 0 and self.motion_stage == 'EXECUTE':
                completed = self.execute_phase
                if completed == 'approach':
                    if self.args.auto_fine:
                        self.execute_phase = 'fine_y'
                        self.align_roi = None
                        self.fine_request = None
                        self.reset_auto_fine(clear_moves=True)
                        self.status = 'APPROACH DONE - AUTO FINE WATCHING'
                        print(self.status, flush=True)
                    elif self.args.camera_center:
                        self.execute_phase = 'done'
                        self.status = f'CAMERA-CENTER APPROACH DONE - {self.args.arm.upper()} CAMERA LIVE'
                        print('Camera-centered observation complete. Descent is only available in TCP-above-target mode.', flush=True)
                    else:
                        self.execute_phase = 'done'
                        self.status = 'APPROACH DONE - G GRASP'
                        print('Approach complete. Press G to preview the grasp height.', flush=True)
                elif completed == 'descend':
                    self.execute_phase = 'close'
                    next_phase = 'close'
                    self.status = f'DESCENT DONE - {self.args.arm.upper()} CAMERA LIVE - CLOSING'
                    print('Descent confirmed. Camera live; closing gripper.', flush=True)
                elif completed == 'close':
                    self.execute_phase = 'done'
                    if self.args.auto_fine and not self.auto_grasp_cancelled:
                        next_phase = 'auto_lift'
                        self.status = 'GRIPPER CLOSED - AUTO LIFT STARTING'
                        print(self.status, flush=True)
                    else:
                        self.status = f'GRIPPER CLOSED - {self.args.arm.upper()} CAMERA LIVE - L LIFT / C RETURN'
                        print('Gripper close complete. Press L to lift 5 cm and return both arms to see_center.', flush=True)
                elif completed in ('lift', 'grasp'):
                    self.execute_phase = 'done'
                    next_phase = 'return_after_lift'
                    self.status = 'LIFT DONE - CARRYING OBJECT TO CASE'
                    print(self.status, flush=True)
            else:
                if self.motion_stage == 'EXECUTE':
                    self.execute_phase = 'done'
                self.status = 'MOVEIT FAILED - CHECK ROBOT / GATE / R RECAPTURE'
            print(f'MoveIt {self.motion_stage} worker exit={code}', flush=True)
        self.motion_stage = None
        if code == 0 and next_phase == 'auto_lift':
            self.start_lift()
            if self.motion_child is None:
                self.status = 'AUTO LIFT DID NOT START - CHECK TERMINAL AND ROBOT'
                print(self.status, flush=True)
        elif code == 0 and next_phase == 'return_after_lift':
            # Old behaviour (lift -> both see_center, object kept in gripper):
            # self.start_return_see_center(open_grippers=False)
            self.start_place_after_lift()
        elif code == 0 and next_phase is not None:
            self.start_motion(execute=True)

    def stop_motion(self):
        if self.motion_child is None or self.motion_child.poll() is not None:
            return
        if self.motion_stage == 'FINE' or (self.motion_stage == 'EXECUTE' and self.execute_phase in ('descend', 'grasp')):
            from std_srvs.srv import Trigger
            name = f'/{self.args.arm}/sdk_linear_xy_stop'
            client = self.live_poses.node.create_client(Trigger, name)
            try:
                if client.wait_for_service(timeout_sec=0.5):
                    future = client.call_async(Trigger.Request())
                    deadline = time.monotonic() + 2.0
                    while not future.done() and time.monotonic() < deadline:
                        self.live_poses.ros.spin_once(self.live_poses.node, timeout_sec=0.05)
                    if future.done():
                        reply = future.result()
                        print(f'Pi SDK stop: {reply.message}', flush=True)
                    else:
                        print('Pi SDK stop response timed out.', flush=True)
                else:
                    print(f'Pi SDK stop service unavailable: {name}', flush=True)
            except Exception as exc:
                print(f'Pi SDK stop request failed: {exc}', flush=True)
            finally:
                self.live_poses.node.destroy_client(client)
            if self.motion_stage == 'FINE':
                self.fine_stop_requested = True
        print('Requesting worker cancellation.', flush=True)
        self.motion_child.send_signal(signal.SIGINT)
        try:
            self.motion_child.wait(timeout=5.)
        except subprocess.TimeoutExpired:
            print(f'Worker still active, PID={self.motion_child.pid}.', flush=True)


def main():
    global K, DIST, T_GRIPPER_CAMERA_BY_SIDE
    parser=argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument('--enable-execute', dest='enable_execute', action='store_true',
                                help='Permit E-key physical movement (manual_roi_drag default)')
    execution_mode.add_argument('--plan-only', dest='enable_execute', action='store_false',
                                help='Disable physical movement; plan and preview only')
    parser.set_defaults(enable_execute=False)
    parser.add_argument('--motion-python', type=Path, default=Path('/usr/bin/python3'),
                        help='Python with ROS 2 Jazzy rclpy and moveit_msgs')
    parser.add_argument('--yaw-camera', choices=('auto', 'left', 'right'), default='auto',
                        help='auto picks higher view score; choose side to override')
    parser.add_argument('--arm', choices=('left', 'right'), default='right',
                        help='Moving arm and corresponding camera used for yaw')
    parser.add_argument('--object-type', choices=('chocobar',), default=None,  # 'mentos_pair' disabled 2026-11-19
                        help='Compatibility option; select the object with 1/2 after matching')
    parser.add_argument('--group', default=None,
                        help='Exact MoveIt SRDF group, adjust for your configuration')
    parser.add_argument('--tip-link', default=None,
                        help='Exact selected-arm TCP link in MoveIt robot model')
    parser.add_argument('--move-action', default='/jetcobot_dual/move_action')
    parser.add_argument('--execute-action', default='/jetcobot_dual/execute_trajectory')
    parser.add_argument('--display-topic', default='/jetcobot_dual/display_planned_path')
    parser.add_argument('--tcp-closing-axis', choices=('x','y'), default='x')
    parser.add_argument('--yaw-offset-deg',type=float,default=0.)
    parser.add_argument('--allow-gripper-tilt',action='store_true',
                        help='Relax vertical TCP orientation tolerance from 2 to 8 degrees')
    parser.add_argument('--above-mm',type=float,default=40.)
    parser.add_argument('--minimal-drag', action='store_true',
                        help='Use segmented endpoint/drag approach execution')
    parser.add_argument('--fine-roi', action='store_true',
                        help='After approach, choose X/Y and correct from new live ROIs')
    parser.add_argument('--auto-fine', '--auto-left-fine', dest='auto_fine',
                        action='store_true',
                        help='Automatically fine the selected arm using the selected object detector')
    parser.add_argument('--no-auto-fine', '--no-auto-left-fine',
                        dest='auto_fine', action='store_false')
    parser.set_defaults(auto_fine=False)
    parser.add_argument('--fine-x-sign', type=int, choices=(-1, 1), default=None,
                        help='X ROI correction direction; default +1')
    parser.add_argument('--fine-y-sign', type=int, choices=(-1, 1), default=None,
                        help='Y ROI correction direction; default +1 (live camera geometry)')
    parser.add_argument('--fine-xy-sign', type=int, choices=(-1, 1), default=None,
                        help='Legacy override for both X and Y directions')
    parser.add_argument('--camera-center', action='store_true',
                        help='Shift TCP sideways to center target in the moving arm camera')
    parser.add_argument('--max-joint-change-deg',type=float,default=45.)
    parser.add_argument('--max-state-change-deg',type=float,default=1.)
    parser.add_argument('--max-capture-age',type=float,default=120.)
    parser.add_argument('--allow-uncertain-yaw',action='store_true',
                        help='Compatibility flag; yaw disagreement is now warning-only')
    parser.add_argument('--left-port',type=int,default=5000)
    parser.add_argument('--right-port',type=int,default=5001)
    parser.add_argument('--urdf',type=Path,default=DEFAULT_URDF)
    parser.add_argument('--camera-calibration',type=Path,default=DEFAULT_CAMERA_CALIBRATION)
    parser.add_argument('--handeye',type=Path,default=DEFAULT_HAND_EYE,
                        help='Left-arm hand-eye JSON; use --handeye to try a recalibration')
    parser.add_argument('--right-handeye',type=Path,default=DEFAULT_RIGHT_HAND_EYE,
                        help='Right-arm hand-eye JSON; default is the 2026-11-17 recalibration')
    parser.add_argument('--sam-checkpoint',type=Path,default=DEFAULT_SAM_CKPT)
    parser.add_argument('--sam-config',default=DEFAULT_SAM_CFG)
    parser.add_argument('--device',choices=('cuda','cpu'),default=None)
    parser.add_argument('--retry-clahe', action='store_true',
                        help='Experimental contrast-enhanced second matching pass')
    parser.add_argument('--stereo-board-relative', action='store_true',
                        help='Derive right camera pose from the same visible ChArUco board')
    parser.add_argument('--stereo-fixed-see-center', action='store_true',
                        help='Use marker-free fixed correction near recorded see_center joints')
    parser.add_argument('--match-rotation', dest='match_rotation', action='store_true',
                        help='Try four right-ROI rotations and keep 2D-consistent matches')
    parser.add_argument('--no-match-rotation', dest='match_rotation', action='store_false',
                        help='Use the original unrotated LightGlue matching')
    parser.set_defaults(match_rotation=False)
    parser.add_argument('--mask-erode',type=int,default=3,
                        help='Pixels removed from mask edge for SuperPoint filtering')
    parser.add_argument('--max-keypoints',type=int,default=2048)
    parser.add_argument('--reproj-px',type=float,default=30.0)
    parser.add_argument('--min-angle-deg',type=float,default=1.5)
    parser.add_argument('--min-valid-points',type=int,default=6)
    parser.add_argument('--out-dir',type=Path,
                        default=Path(__file__).resolve().parent.parent/'sam_yaw_output')
    args=parser.parse_args()
    if args.stereo_board_relative and args.stereo_fixed_see_center:
        parser.error('Choose only one stereo correction mode')
    if args.stereo_fixed_see_center and not DEFAULT_STEREO_CORRECTION.is_file():
        parser.error(f'Fixed stereo correction missing: {DEFAULT_STEREO_CORRECTION}')
    if args.fine_xy_sign is not None:
        args.fine_x_sign = args.fine_xy_sign if args.fine_x_sign is None else args.fine_x_sign
        args.fine_y_sign = args.fine_xy_sign if args.fine_y_sign is None else args.fine_y_sign
    if args.fine_x_sign is None:
        args.fine_x_sign = 1
    if args.fine_y_sign is None:
        args.fine_y_sign = 1
    if not args.camera_calibration.is_file():
        parser.error(f'Camera calibration NPZ not found: {args.camera_calibration}')
    try:
        K, DIST = load_camera_calibration(args.camera_calibration)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(f'Invalid camera calibration NPZ: {exc}')
    print(f'Camera calibration: {args.camera_calibration}; distortion={DIST.tolist()}', flush=True)
    args.handeye_passed = {}
    T_GRIPPER_CAMERA_BY_SIDE = {}
    for side, path in (('left', args.handeye), ('right', args.right_handeye)):
        if not path.is_file():
            parser.error(f'{side} hand-eye JSON not found: {path}')
        try:
            transform, handeye_info = load_handeye(path, side)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            parser.error(f'Invalid {side} hand-eye JSON: {exc}')
        T_GRIPPER_CAMERA_BY_SIDE[side] = transform
        print(f'{side.upper()} hand-eye transform: {path}', flush=True)
        passed = bool(handeye_info.get('passed_internal_consistency', False))
        args.handeye_passed[side] = passed
        if not passed:
            consistency = handeye_info.get('consistency', {})
            print(f'WARNING: {side} hand-eye internal consistency FAILED; '
                  f'translation RMS={consistency.get("translation_rms_m", float("nan"))*1000:.1f} mm. '
                  'Verify real 3D coordinates before motion.', flush=True)
    args.group = args.group or f'arm_{args.arm}'
    args.tip_link = args.tip_link or f'{args.arm}_TCP'
    if args.group != f'arm_{args.arm}' or args.tip_link != f'{args.arm}_TCP':
        parser.error('--group and --tip-link must match --arm')
    if args.left_port==args.right_port:
        parser.error('Choose two distinct camera UDP ports')
    if args.mask_erode<0 or args.mask_erode>20:
        parser.error('--mask-erode must be in range 0..20')
    if args.max_keypoints<64 or args.min_valid_points<3:
        parser.error('Invalid number of keypoints / minimum valid matches')
    if args.above_mm < 10 or args.max_capture_age <= 0:
        parser.error('--above-mm must be >= 10 and --max-capture-age > 0')
    if not args.motion_python.is_file():
        parser.error(f'ROS motion Python missing: {args.motion_python}')
    if not Path(__file__).with_name('move_right_sam_yaw.py').is_file():
        parser.error('move_right_sam_yaw.py must be beside this script')
    if args.camera_center:
        parser.error('Dual auto mode requires TCP-above-target approach; omit --camera-center')
    if args.auto_fine and (not args.fine_roi or not args.enable_execute):
        parser.error('--auto-fine requires --fine-roi and --enable-execute')
    if args.fine_roi and args.camera_center:
        parser.error('--fine-roi and --camera-center cannot be combined')
    if not args.urdf.is_file():
        parser.error(f'URDF not found: {args.urdf}')
    if not args.sam_checkpoint.is_file():
        parser.error(f'SAM2 checkpoint not found: {args.sam_checkpoint}. See setup instructions.')
    try:
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
    except ImportError as exc:
        parser.error(f'SAM 2 library not installed in this Python environment: {exc}')

    device=args.device or ('cuda' if torch.cuda.is_available() else 'cpu')
    print('Inference device:',device,flush=True)
    print('Loading SAM2.1:',args.sam_checkpoint,flush=True)
    sam_model=build_sam2(args.sam_config,str(args.sam_checkpoint),device=device)
    predictor=SAM2ImagePredictor(sam_model)
    extractor=SuperPoint(max_num_keypoints=None).eval().to(device)
    matcher=LightGlue(features='superpoint').eval().to(device)
    Gst.init(None)
    live=None
    cameras=[]
    try:
        live=LivePoses(args.urdf)
        cameras=[Camera('LEFT',args.left_port),Camera('RIGHT',args.right_port)]
        app=SAMYawApp(cameras,live,predictor,extractor,matcher,device,args)
        for cam in cameras:
            cam.start()
        cv2.namedWindow(MAIN_WINDOW,cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(MAIN_WINDOW,app.on_mouse)
        print(f'Moving arm=AUTO (initial preview={args.arm}); yaw camera={args.yaw_camera}; group={args.group}; approach={"CAMERA CENTER" if args.camera_center else "TCP ABOVE TARGET"} +{args.above_mm:.0f} mm. Initial stereo ROIs -> match; 1 chocolate / 2 Mentos -> PLAN; E approaches. ' + ('AUTO FINE OK -> calibrated descent -> gripper close -> 5 cm lift -> both see_center; S stops.' if args.auto_fine else 'After approach: G previews grasp; L lifts.') + ' O open gripper; C both see_center + open both grippers; R reset; S stop; Q quit.', flush=True)
        while True:
            live.poll()
            for cam in cameras:
                cam.poll()
            app.poll_motion()
            if app.pending:
                app.pending=False
                app.run()
            app.maybe_auto_fine()
            cv2.imshow(MAIN_WINDOW,app.view())
            approach_frame = app.approach_camera_frame()
            if approach_frame is not None:
                cv2.namedWindow(APPROACH_CAMERA_WINDOW, cv2.WINDOW_AUTOSIZE)
                cv2.imshow(APPROACH_CAMERA_WINDOW, approach_frame)
            if app.result is not None:
                cv2.imshow(RESULT_WINDOW,app.result)
                cv2.imshow(TOP_WINDOW,app.topview)
            key=cv2.waitKey(10)&255
            if key in (ord('q'),27):
                break
            if key in (ord('c'), ord('C')):
                app.start_return_see_center()
            elif key==ord('r'):
                app.reset()
                for name in (RESULT_WINDOW,TOP_WINDOW,APPROACH_CAMERA_WINDOW):
                    try: cv2.destroyWindow(name)
                    except cv2.error: pass
            elif key==ord('m'):
                app.run()
            elif key == ord('a'):
                app.save_board_capture()
            elif key == ord('1'):
                app.select_object_type('chocobar')
            # Mentos disabled until its pick/place handling is finished (2026-11-19).
            # elif key == ord('2'):
            #     app.select_object_type('mentos_pair')
            elif key == ord('2'):
                print('Mentos is disabled for now; only 1 = chocobar.', flush=True)
            elif key == ord('0'):
                app.select_yaw_camera('auto')
            elif key==ord('p'):
                app.start_motion(execute=False)
            elif key==ord('s'):
                app.auto_grasp_cancelled = True
                if app.args.auto_fine:
                    app.pause_auto_fine('operator pressed S')
                app.stop_motion()
            elif key in (ord('g'), ord('G')):
                app.start_grasp()
            elif key in (ord('l'), ord('L')):
                app.start_lift()
            elif key in (ord('o'), ord('O')):
                app.start_open_gripper()
            elif key==ord('e'):
                if app.execute_phase in ('fine_y', 'fine_x'):
                    print('Fine alignment is automatic; E does not start a manual ROI move.', flush=True)
                elif app.execute_phase == 'fine_done':
                    print('Fine alignment complete. Press G for grasp preview or R to recapture.', flush=True)
                else:
                    app.start_motion(execute=True)
            app.maybe_auto_grasp()
    finally:
        if 'app' in locals():
            app.stop_motion()
        for cam in cameras:
            cam.close()
        cv2.destroyAllWindows()
        if live is not None:
            live.close()


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError,GLib.Error) as exc:
        raise SystemExit(f'ERROR: {exc}')
