#!/usr/bin/env python3
"""Camera poses from fixed table ArUco markers (2026-11-24).

The wrist-camera pose from joints + hand-eye drifted 10-35 mm between returns
(servo zero/backlash, wrong right hand-eye). Fixed markers on the table give
each camera's pose directly at every capture, so stereo no longer depends on
the arm joints. Markers: cut-out 17 mm DICT_6X6_50 squares (IDs from the
ChArUco board), any fixed layout; the layout is measured once with `build`.

  build: python table_markers.py build --capture <board_capture_dir> [...]
  check: python table_markers.py check --capture <board_capture_dir>

Frame F: on the marker plane (z = 0), origin at the marker centroid.
T_world_F puts F on the work surface (z = floor) with world-vertical normal;
XY/yaw are anchored to LEFT camera FK using the fitted board corners
(constant offset removed by fine alignment).
"""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
LAYOUT_PATH = HERE / 'calibration' / 'table_markers.json'
DICTIONARY = cv2.aruco.DICT_6X6_50
MARKER_SIZE_M = 0.017


def _detector():
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTIONARY))


def detect(image, detector=None):
    corners, ids, _ = (detector or _detector()).detectMarkers(
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(np.float64) for c, i in zip(corners, ids.ravel())}


def _square(size):
    h = size / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=np.float64)


def _pose(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE):
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=flags)
    if not ok:
        raise RuntimeError('solvePnP failed')
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    projected, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    rmse = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - img) ** 2, axis=1))))
    return T, rmse


def _board_pose(layout, found, K, dist):
    ids = sorted(layout.keys() & found.keys())
    return _pose(np.concatenate([layout[m] for m in ids]),
                 np.concatenate([found[m] for m in ids]), K, dist)


def _refine_planar(layout, views, iterations=5):
    """2026-11-25: chained single-marker poses left the layout bent (9 mm) and
    one camera above the 2.5 px check. Alternate: camera pose from ALL markers,
    then each corner = its pixel ray intersected with the F plane (z=0),
    averaged over views."""
    for it in range(iterations):
        samples, errors = {}, []
        for found, K, dist in views:
            ids = [m for m in sorted(found) if m in layout]
            if len(ids) < 3:
                continue
            T_c_F, rmse = _board_pose(layout, found, K, dist)
            errors.append(rmse)
            T_F_c = np.linalg.inv(T_c_F)
            for m in ids:
                rays = cv2.undistortPoints(found[m].reshape(-1, 1, 2).astype(np.float64),
                                           K, dist).reshape(-1, 2)
                d = (T_F_c[:3, :3] @ np.c_[rays, np.ones(len(rays))].T).T
                o = T_F_c[:3, 3]
                t = -o[2] / d[:, 2]
                samples.setdefault(m, []).append(o + d * t[:, None])
        print(f'layout refine {it}: PnP RMSE per view {[round(e, 2) for e in errors]} px')
        if not samples:
            break
        layout = {m: np.mean(samples[m], axis=0) if m in samples else c
                  for m, c in layout.items()}
        for c in layout.values():
            c[:, 2] = 0.0
    return layout


def build_layout(captures, floor_z_m, size_m=MARKER_SIZE_M):
    """Measure marker corners in a common plane frame from one or more captures."""
    square = _square(size_m)
    samples = {}  # id -> list of 4x3 corner arrays in the reference marker frame
    detections = []
    for capture in captures:
        with np.load(capture / 'triangulation.npz', allow_pickle=True) as data:
            K, dist = data['K'], data['DIST']
            left_fk = data['T_world_left_camera_measured']
        for side in ('left', 'right'):
            found = detect(cv2.imread(str(capture / f'{side}.png')))
            if len(found) >= 2:
                detections.append((capture, side, found, K, dist, left_fk))

    left_ids = {marker for _, side, found, _, _, _ in detections
                if side == 'left' for marker in found}
    if not left_ids:
        raise RuntimeError('Need at least 2 markers in a left-camera view to anchor the layout')
    counts = {marker: 0 for marker in left_ids}
    for _, _, found, _, _, _ in detections:
        for marker in left_ids.intersection(found):
            counts[marker] += 1
    ref = max(counts, key=lambda marker: (counts[marker], -marker))
    print(f'layout reference marker: {ref}')

    for capture, side, found, K, dist, _ in detections:
        if ref not in found:
            print(f'{capture.name} {side}: reference marker {ref} not visible for initialization')
            continue
        T_c_ref, _ = _pose(square, found[ref], K, dist, cv2.SOLVEPNP_IPPE_SQUARE)
        T_ref_c = np.linalg.inv(T_c_ref)
        for marker, img in found.items():
            T_c_m, _ = _pose(square, img, K, dist, cv2.SOLVEPNP_IPPE_SQUARE)
            T_ref_m = T_ref_c @ T_c_m
            samples.setdefault(marker, []).append(
                (T_ref_m[:3, :3] @ square.T).T + T_ref_m[:3, 3])
    if len(samples) < 3:
        raise RuntimeError('Need at least 3 markers seen together with the reference marker')
    corners = {m: np.mean(v, axis=0) for m, v in samples.items()}
    points = np.concatenate(list(corners.values()))
    centroid = points.mean(axis=0)
    normal = np.linalg.svd(points - centroid)[2][2]
    if normal[2] < 0:
        normal = -normal  # same side as the reference marker's +Z
    x_axis = np.array([1.0, 0, 0]) - normal * normal[0]
    x_axis /= np.linalg.norm(x_axis)
    R_ref_F = np.column_stack([x_axis, np.cross(normal, x_axis), normal])
    T_ref_F = np.eye(4)
    T_ref_F[:3, :3] = R_ref_F
    T_ref_F[:3, 3] = centroid
    T_F_ref = np.linalg.inv(T_ref_F)
    layout = {m: (T_F_ref[:3, :3] @ c.T).T + T_F_ref[:3, 3] for m, c in corners.items()}
    flatness = max(float(np.max(np.abs(c[:, 2]))) for c in layout.values()) * 1000
    for c in layout.values():
        c[:, 2] = 0.0
    views = [(found, K, dist) for _, _, found, K, dist, _ in detections]
    layout = _refine_planar(layout, views)
    # The seed defines coordinates for the initial fit. Anchor the refined board
    # to left-camera FK using every visible layout corner in each left image.
    anchors = []
    for _, side, found, K, dist, left_fk in detections:
        if side == 'left' and len(layout.keys() & found.keys()) >= 2:
            T_c_F, _ = _board_pose(layout, found, K, dist)
            anchors.append(left_fk @ T_c_F)
    T_world_F_left = np.mean(anchors, axis=0)
    fx = T_world_F_left[:3, 0].copy()
    fx[2] = 0.0
    fx /= np.linalg.norm(fx)
    up = np.array([0.0, 0, 1.0])
    flip = T_world_F_left[2, 2] < 0  # F normal points away from the table
    z_axis = -up if flip else up
    T_world_F = np.eye(4)
    T_world_F[:3, :3] = np.column_stack([fx, np.cross(z_axis, fx), z_axis])
    T_world_F[:3, 3] = [T_world_F_left[0, 3], T_world_F_left[1, 3], floor_z_m]
    tilt = float(np.degrees(np.arccos(min(1.0, abs(T_world_F_left[2, 2])))))
    print(f'layout: {len(layout)} markers {sorted(layout)}, plane flatness {flatness:.2f} mm, '
          f'left-FK plane tilt {tilt:.1f} deg, left-FK plane z {T_world_F_left[2, 3]*1000:.1f} mm '
          f'(forced to {floor_z_m*1000:.1f} mm)')
    return {'dictionary': 'DICT_6X6_50', 'marker_size_m': size_m,
            'corners_F_m': {str(m): c.tolist() for m, c in sorted(layout.items())},
            'T_world_F': T_world_F.tolist(), 'floor_z_m': floor_z_m,
            'source_captures': [str(c) for c in captures], 'created_unix': time.time()}


class TableMarkers:
    def __init__(self, path=LAYOUT_PATH, min_markers=2, max_rmse_px=2.5):
        record = json.loads(Path(path).read_text())
        self.corners = {int(k): np.asarray(v, dtype=np.float64)
                        for k, v in record['corners_F_m'].items()}
        self.T_world_F = np.asarray(record['T_world_F'], dtype=np.float64)
        self.min_markers = min_markers
        self.max_rmse_px = max_rmse_px
        self.detector = _detector()

    def camera_pose(self, image, K, dist):
        found = {m: c for m, c in detect(image, self.detector).items() if m in self.corners}
        if len(found) < self.min_markers:
            raise RuntimeError(f'only {len(found)} table markers visible {sorted(found)}')
        T_c_F, rmse = _board_pose(self.corners, found, K, dist)
        if rmse > self.max_rmse_px:
            raise RuntimeError(f'table marker PnP RMSE {rmse:.2f} px > {self.max_rmse_px} px')
        return self.T_world_F @ np.linalg.inv(T_c_F), {'markers': sorted(found), 'rmse_px': rmse}

    def exclusion(self, T_world_cam, shape, K, dist, grow=1.6):
        """uint8 mask of every layout marker (incl. white margin) projected into the
        image, also where a marker is partly covered and not detected."""
        T_cam_F = np.linalg.inv(T_world_cam) @ self.T_world_F
        rvec = cv2.Rodrigues(T_cam_F[:3, :3])[0]
        out = np.zeros(shape[:2], np.uint8)
        for quad in self.corners.values():
            center = quad.mean(axis=0)
            pts, _ = cv2.projectPoints(center + (quad - center) * grow, rvec, T_cam_F[:3, 3], K, dist)
            cv2.fillConvexPoly(out, np.round(pts.reshape(-1, 2)).astype(np.int32), 1)
        return out

    def camera_poses(self, images, K, dist):
        poses, info = [], {}
        for side, image in zip(('left', 'right'), images):
            T, detail = self.camera_pose(image, K, dist)
            poses.append(T)
            info[side] = detail
        return poses, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('build', 'check'))
    ap.add_argument('--capture', type=Path, action='append', required=True)
    ap.add_argument('--floor-z-m', type=float, default=0.008)
    ap.add_argument('--marker-size-m', type=float, default=MARKER_SIZE_M)
    ap.add_argument('--out', type=Path, default=LAYOUT_PATH)
    args = ap.parse_args()
    if args.command == 'build':
        record = build_layout(args.capture, args.floor_z_m, args.marker_size_m)
        if args.out.exists():
            backup = args.out.parent / 'history' / f'{args.out.stem}_before_{int(time.time())}.json'
            backup.parent.mkdir(exist_ok=True)
            args.out.rename(backup)
            print(f'backup: {backup}')
        args.out.write_text(json.dumps(record, indent=2))
        print(f'written: {args.out}')
    markers = TableMarkers(args.out)
    for capture in args.capture:
        data = np.load(capture / 'triangulation.npz', allow_pickle=True)
        images = [cv2.imread(str(capture / f'{s}.png')) for s in ('left', 'right')]
        poses, info = markers.camera_poses(images, data['K'], data['DIST'])
        fk = [data['T_world_left_camera_measured'], data['T_world_right_camera_measured']]
        for side, T, F, detail in zip(('left', 'right'), poses, fk, info.values()):
            dpos = (T[:3, 3] - F[:3, 3]) * 1000
            dang = np.degrees(np.arccos(np.clip((np.trace(T[:3, :3].T @ F[:3, :3]) - 1) / 2, -1, 1)))
            print(f'{capture.name} {side}: markers {detail["markers"]}, RMSE {detail["rmse_px"]:.2f} px, '
                  f'camera z {T[2, 3]*1000:.1f} mm; vs joint FK {np.round(dpos, 1).tolist()} mm, {dang:.2f} deg')


if __name__ == '__main__':
    main()
