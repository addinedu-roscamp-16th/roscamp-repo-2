#!/usr/bin/env python3
"""Rebuild the fixed see_center stereo correction from a ChArUco/ArUco capture.

Method (same as the 2026-11-18 file): the LEFT camera's measured world pose is
the anchor. The board seen by both cameras in the same capture gives where the
RIGHT camera must be:  right_world = left_world @ T_left_board @ inv(T_right_board).
The stored correction is the left-multiply that maps the RIGHT camera's
measured world pose (hand-eye + joints) onto that:  C = right_world @ inv(right_measured).

Input: a normal pick-UI capture folder (left.png, right.png, triangulation.npz)
taken with BOTH arms at see_center and the board visible to both cameras.
Default is a dry run that prints the comparison; --write replaces the file the
UI loads (the old one is backed up next to it). No robot command is sent.
"""
import argparse
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
TARGET = HERE / 'calibration/stereo_see_center_correction_20261117.json'  # path the UI loads
CAPTURES = HERE.parent / 'sam_yaw_output'


def board_pose(image, K, dist, min_markers, max_rmse):
    """Camera -> board from visible ArUco marker corners (board as in the UI)."""
    board = cv2.aruco.CharucoBoard((7, 5), 0.025, 0.017,
                                   cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_50))
    detector = cv2.aruco.ArucoDetector(board.getDictionary())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    board_ids = board.getIds().reshape(-1)
    board_points = board.getObjPoints()
    obj, img, used = [], [], []
    if ids is not None:
        for corner, marker_id in zip(corners, ids.reshape(-1)):
            found = np.flatnonzero(board_ids == marker_id)
            if len(found):
                obj.extend(np.asarray(board_points[int(found[0])]).reshape(-1, 3))
                img.extend(np.asarray(corner).reshape(-1, 2))
                used.append(int(marker_id))
    if len(used) < min_markers:
        raise RuntimeError(f'only {len(used)} board markers visible (need {min_markers})')
    obj = np.asarray(obj, dtype=np.float64)
    img = np.asarray(img, dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist)
    if not ok:
        raise RuntimeError('solvePnP failed')
    projected, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    rmse = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - img)**2, axis=1))))
    if rmse > max_rmse:
        raise RuntimeError(f'PnP RMSE {rmse:.2f} px > {max_rmse:.1f} px')
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.reshape(3)
    return T, {'marker_ids': used, 'pnp_rmse_px': rmse}


def angle_deg(R):
    return float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(R)[0])))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture', type=Path,
                    help='capture_* folder (default: newest in sam_yaw_output)')
    ap.add_argument('--write', action='store_true', help='replace the correction the UI loads')
    ap.add_argument('--min-markers', type=int, default=6)
    ap.add_argument('--max-rmse-px', type=float, default=2.0)
    ap.add_argument('--max-joint-difference-deg', type=float, default=2.0,
                    help='how close to this see_center pose the UI will accept the correction')
    args = ap.parse_args()
    capture = args.capture or max(
        [*CAPTURES.glob('capture_*'), *CAPTURES.glob('board_capture_*')],
        key=lambda p: p.stat().st_mtime)
    data = np.load(capture / 'triangulation.npz', allow_pickle=True)
    K, dist = data['K'], data['DIST']
    left_measured = data['T_world_left_camera_measured']
    right_measured = data['T_world_right_camera_measured']
    joints = data['measured_joint_positions_rad']
    images = [cv2.imread(str(capture / name)) for name in ('left.png', 'right.png')]
    if any(image is None for image in images):
        raise SystemExit(f'{capture}: left.png/right.png missing')
    print(f'capture: {capture}')
    print('joints deg left/right:',
          np.round(np.rad2deg(joints), 1).tolist())
    (T_lb, left_info), (T_rb, right_info) = [
        board_pose(image, K, dist, args.min_markers, args.max_rmse_px) for image in images]
    print(f'markers left {len(left_info["marker_ids"])} (RMSE {left_info["pnp_rmse_px"]:.2f} px), '
          f'right {len(right_info["marker_ids"])} (RMSE {right_info["pnp_rmse_px"]:.2f} px)')
    right_world = left_measured @ T_lb @ np.linalg.inv(T_rb)
    correction = right_world @ np.linalg.inv(right_measured)
    board_by_left = left_measured @ T_lb
    board_by_right = right_measured @ T_rb
    print(f'uncorrected stereo disagreement on the board: '
          f'{np.linalg.norm(board_by_right[:3, 3]-board_by_left[:3, 3])*1000:.1f} mm, '
          f'{angle_deg((np.linalg.inv(board_by_left) @ board_by_right)[:3, :3]):.2f} deg')
    print(f'new correction: translation {np.round(correction[:3, 3]*1000, 1).tolist()} mm, '
          f'rotation {angle_deg(correction[:3, :3]):.2f} deg')
    if TARGET.is_file():
        old = json.loads(TARGET.read_text())
        old_c = np.asarray(old['right_world_pose_left_multiply'])
        change = np.linalg.inv(old_c) @ correction
        print(f'current file ({old.get("source_capture")}): translation '
              f'{np.round(old_c[:3, 3]*1000, 1).tolist()} mm, rotation {angle_deg(old_c[:3, :3]):.2f} deg')
        print(f'change vs current: {np.linalg.norm(change[:3, 3])*1000:.1f} mm, '
              f'{angle_deg(change[:3, :3]):.2f} deg')
        ref_diff = float(np.rad2deg(np.max(np.abs(joints - np.asarray(old['reference_joint_positions_rad'])))))
        print(f'joint difference from current reference pose: {ref_diff:.2f} deg')
    record = {
        'frame': 'right_base_link',
        'method': 'one-time same-capture ChArUco relative camera pose; LEFT world pose anchor',
        'source_capture': str(capture),
        'right_world_pose_left_multiply': correction.tolist(),
        'reference_joint_positions_rad': np.asarray(joints).tolist(),
        'max_joint_difference_deg': args.max_joint_difference_deg,
        'board_detection': {'left': left_info, 'right': right_info,
                            'method': 'same captured board, left world camera pose as anchor'},
        'scope': 'fixed stereo correction near recorded joint pose',
        'created_by': 'moveit_pick/make_stereo_correction.py',
        'created_unix': time.time(),
    }
    if not args.write:
        print('DRY RUN: nothing written. Add --write to replace the correction the UI loads.')
        return
    if TARGET.is_file():
        backup = TARGET.with_name(
            f'stereo_see_center_correction_before_{capture.name.split("capture_")[-1]}.json')
        shutil.copy2(TARGET, backup)
        print(f'backup: {backup}')
    TARGET.write_text(json.dumps(record, indent=2) + '\n')
    print(f'written: {TARGET} (restart the pick UI to load it)')


if __name__ == '__main__':
    main()
