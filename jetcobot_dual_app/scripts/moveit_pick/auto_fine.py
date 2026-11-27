"""Bounded automatic fine alignment for chocolate or Mentos."""
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from left_detail import (WrapperDetector, TemporalFilter, image_to_view,
                         tolerance_status, PRESET_BEST_X,
                         PRESET_BEST_TOP_Y, DEFAULT_X_TOL, DEFAULT_Y_UP,
                         DEFAULT_Y_DOWN)
from fine_roi_real import roi_translation
from left_mentos_detail import (
    MentosDetector, StableCoords, PRESET_BEST_X as MENTOS_BEST_X,
    PRESET_BEST_Y as MENTOS_BEST_Y, PRESET_X_TOL as MENTOS_X_TOL,
    PRESET_Y_TOL_UP as MENTOS_Y_UP,
    PRESET_Y_TOL_DOWN as MENTOS_Y_DOWN)

def dashed_line(image, start, end, color, thickness=1, dash=8, gap=6):
    a = np.asarray(start, dtype=float)
    b = np.asarray(end, dtype=float)
    length = float(np.linalg.norm(b - a))
    if length == 0:
        return
    direction = (b - a) / length
    for offset in np.arange(0, length, dash + gap):
        first = a + direction * offset
        last = a + direction * min(offset + dash, length)
        cv2.line(image, tuple(np.rint(first).astype(int)),
                 tuple(np.rint(last).astype(int)), color, thickness, cv2.LINE_AA)


class AutoFineMixin:
    def init_auto_fine(self):
        self.auto_fine_detector = WrapperDetector()
        self.auto_fine_filter = TemporalFilter()
        self.auto_fine_profile_type = 'chocobar'
        self.auto_fine_detection = None
        self.auto_fine_detection_kind = None
        self.auto_fine_stable = None
        self.auto_fine_mask = None
        self.auto_fine_last_count = -1
        self.auto_fine_deadline = None
        self.auto_fine_moves = 0
        self.auto_fine_paused = False
        self.auto_fine_ok = False
        self.auto_fine_reason = None
        self.mentos_search_done = False
        self.mentos_search_pending = False
        self.mentos_seen_detection = False

    def auto_fine_profile(self):
        object_type = self.selected_object_type
        if object_type not in ('chocobar', 'mentos_pair'):
            raise ValueError('Choose chocolate or Mentos before automatic fine alignment')
        if object_type != self.auto_fine_profile_type:
            self.auto_fine_detector = (MentosDetector() if object_type == 'mentos_pair'
                                       else WrapperDetector())
            self.auto_fine_filter = (StableCoords() if object_type == 'mentos_pair'
                                     else TemporalFilter())
            self.auto_fine_profile_type = object_type
            self.auto_fine_filter.reset()
        if object_type == 'mentos_pair':
            return (MENTOS_BEST_X, MENTOS_BEST_Y, MENTOS_X_TOL,
                    MENTOS_Y_UP, MENTOS_Y_DOWN)
        return (PRESET_BEST_X, PRESET_BEST_TOP_Y, DEFAULT_X_TOL,
                DEFAULT_Y_UP, DEFAULT_Y_DOWN)

    def reset_auto_fine(self, clear_moves=False):
        self.auto_fine_filter.reset()
        self.auto_fine_detection = None
        self.auto_fine_detection_kind = None
        self.auto_fine_stable = None
        self.auto_fine_mask = None
        camera_index = 0 if self.args.arm == 'left' else 1
        self.auto_fine_last_count = self.cameras[camera_index].count
        self.auto_fine_deadline = time.monotonic() + 20.
        self.mentos_seen_detection = False
        # Diagnostics only (2026-11-25): printed when the 20 s stability wait fails.
        self.auto_fine_diag = {'frames': 0, 'missed': 0, 'peer_start': None, 'peer_max_deg': 0.0}
        if clear_moves:
            self.auto_fine_moves = 0
            self.auto_fine_paused = False
            self.auto_fine_ok = False
            self.auto_fine_reason = None
            self.mentos_search_done = False
            self.mentos_search_pending = False

    def _auto_fine_diag_update(self, detection):
        diag = getattr(self, 'auto_fine_diag', None)
        if diag is None:
            return
        diag['frames'] += 1
        if detection is None:
            diag['missed'] += 1
        peer = 'right' if self.args.arm == 'left' else 'left'
        sample = self.live_poses.samples.get(peer)
        if sample is not None:
            q = np.array([sample[2][f'{peer}_{i}_Joint'] for i in range(1, 7)])
            if diag['peer_start'] is None:
                diag['peer_start'] = q
            else:
                diag['peer_max_deg'] = max(diag['peer_max_deg'],
                                           float(np.rad2deg(np.max(np.abs(q - diag['peer_start'])))))

    def _auto_fine_diag_print(self):
        diag = getattr(self, 'auto_fine_diag', None)
        if diag is None:
            return
        f = self.auto_fine_filter
        xs, ys = list(getattr(f, 'xs', [])), list(getattr(f, 'ys', []))
        spread = (f'x std {np.std(xs):.1f} px, y std {np.std(ys):.1f} px over last {len(xs)} detections'
                  if xs else 'no recent detections')
        peer = 'right' if self.args.arm == 'left' else 'left'
        print(f'AUTO FINE DIAG {self.args.arm.upper()}: frames {diag["frames"]}, '
              f'no detection {diag["missed"]}; {spread} '
              f'(stable needs {getattr(f, "size", "?")} in a row, std <= {getattr(f, "max_std", "?")} px); '
              f'{peer} arm moved up to {diag["peer_max_deg"]:.1f} deg during the wait', flush=True)

    def pause_auto_fine(self, reason):
        self.auto_fine_paused = True
        self.auto_fine_reason = reason
        self.fine_request = None
        self.status = f'AUTO FINE PAUSED: {reason} - CHECK ROBOT'
        print(self.status, flush=True)

    def auto_fine_view(self, frame):
        """Only the detected box, current lines, allowed range, and BEST lines."""
        image = frame.copy()
        if image.shape[:2] != (480, 640):
            image = cv2.resize(image, (640, 480))

        # The allowed position of (wrapper center X, top edge Y).
        best_x, best_y, x_tol, y_up, y_down = self.auto_fine_profile()
        left = round(best_x - x_tol)
        right = round(best_x + x_tol)
        top = round(best_y - y_up)
        bottom = round(best_y + y_down)
        allowed = (225, 225, 225)  # dashed white
        for a, b in (((left, top), (right, top)),
                     ((right, top), (right, bottom)),
                     ((right, bottom), (left, bottom)),
                     ((left, bottom), (left, top))):
            dashed_line(image, a, b, allowed, thickness=2)

        # BEST X and Y are solid blue lines.
        best = (220, 110, 45)
        cv2.line(image, (round(best_x), 0),
                 (round(best_x), 479), best, 1, cv2.LINE_AA)
        cv2.line(image, (0, round(best_y)),
                 (639, round(best_y)), best, 1, cv2.LINE_AA)

        detection = self.auto_fine_detection
        if detection is not None:
            x, y, w, h = detection.get('box', detection.get('bbox'))
            cv2.rectangle(image, (x, y), (x + w, y + h),
                          (255, 185, 35), 2, cv2.LINE_AA)
            current = (self.auto_fine_stable if self.auto_fine_stable is not None
                       else (detection['x'], detection['y']))
            current_x, current_y = map(round, current)
            # Current judged center X and top edge Y are solid white lines.
            current_color = (255, 255, 255)
            cv2.line(image, (current_x, 0), (current_x, 479),
                     current_color, 1, cv2.LINE_AA)
            cv2.line(image, (0, current_y), (639, current_y),
                     current_color, 1, cv2.LINE_AA)
            # Detection anchor: wrapper center X and detected top edge Y.
            anchor = (round(detection['x']), round(detection['y']))
            cv2.circle(image, anchor, 5, (0, 0, 220), -1, cv2.LINE_AA)
            cv2.circle(image, anchor, 6, (255, 255, 255), 1, cv2.LINE_AA)
        return image

    def start_auto_fine_move(self, axis, roi, target_px, side_index, search=False):
        transforms, joints, _ = self.live_poses.snapshot()
        z_object = json.loads(self.preview_file.read_text())['center_xyz_mm'][2] / 1000.
        model_delta = roi_translation(roi, axis, transforms[side_index], self.camera_K,
            self.camera_DIST, z_object, max_distance=.01, target_pixel_xy=target_px)
        # Search uses the same Y correction and sign as a detected box below range.
        direction_sign = self.fine_direction_signs[axis]
        delta = model_delta * direction_sign
        if np.linalg.norm(delta[:2]) < .003:
            raise ValueError('calculated fine movement below 3 mm')
        self.execute_phase = f'fine_{axis}'
        self.fine_request = dict(axis=axis, delta_xyz_m=delta.tolist(),
            model_delta_xyz_m=model_delta.tolist(),
            fine_xy_sign=direction_sign,
            target_pixel_xy=target_px.tolist(), roi_xyxy=roi,
            start_joints_rad=joints[side_index].tolist(), target_file=str(self.preview_file),
            arm=self.args.arm, group=self.args.group, tip_link=self.args.tip_link,
            above_mm=self.args.above_mm, yaw_camera=self.args.yaw_camera,
            move_action=self.args.move_action, execute_action=self.args.execute_action,
            display_topic=self.args.display_topic,
            tcp_closing_axis=self.args.tcp_closing_axis,
            yaw_offset_deg=self.args.yaw_offset_deg,
            max_joint_change_deg=self.args.max_joint_change_deg,
            max_state_change_deg=self.args.max_state_change_deg,
            min_valid_points=self.args.min_valid_points,
            allow_uncertain_yaw=self.args.allow_uncertain_yaw)
        if search:
            self.mentos_search_done = True
            self.mentos_search_pending = True
            print(f'MENTOS SEARCH: same camera-Y direction as a box below range; '
                  f'bounded SDK XY {(delta[:2]*1000).round(2).tolist()} mm, then re-detect.',
                  flush=True)
        else:
            self.auto_fine_moves += 1
            print(f'AUTO FINE {axis.upper()} move {self.auto_fine_moves}: '
                  f'SDK XY {(delta[:2]*1000).round(2).tolist()} mm', flush=True)
        self.start_fine()
        self.auto_fine_detection = None
        self.auto_fine_stable = None
        self.auto_fine_mask = None

    def maybe_auto_fine(self):
        if (not self.args.auto_fine or self.auto_fine_paused or
                self.execute_phase not in ('fine_y', 'fine_x', 'fine_done') or
                self.motion_child is not None or self.preview_file is None):
            return
        side_index = 0 if self.args.arm == 'left' else 1
        camera = self.cameras[side_index]
        if not camera.is_live() or camera.frame is None or camera.frame.shape[:2] != (480, 640):
            if self.auto_fine_deadline and time.monotonic() > self.auto_fine_deadline:
                self.pause_auto_fine(f'no fresh {self.args.arm.upper()} video')
            return
        if camera.count == self.auto_fine_last_count:
            return
        self.auto_fine_last_count = camera.count
        try:
            best_x, best_y, x_tol, y_up, y_down = self.auto_fine_profile()
            frame = image_to_view(camera.frame)
            if isinstance(self.auto_fine_detector, WrapperDetector):
                detection, mask = self.auto_fine_detector.detect(frame, prefer_xy=(best_x, best_y))
            else:
                detection, mask = self.auto_fine_detector.detect(frame)
            kind = (detection.get('kind', 'chocobar') if detection is not None else None)
            if kind is not None and kind != self.auto_fine_detection_kind:
                self.auto_fine_filter.reset()
            self.auto_fine_detection_kind = kind
            # Both orientations use the selected Mentos BEST range. Never mix
            # magenta and yellow samples in one stability window.
            stable = self.auto_fine_filter.update(detection)
            self._auto_fine_diag_update(detection)
            self.auto_fine_detection, self.auto_fine_stable, self.auto_fine_mask = detection, stable, mask
            if detection is not None:
                self.mentos_seen_detection = True
            if stable is None:
                if self.auto_fine_deadline and time.monotonic() > self.auto_fine_deadline:
                    if (self.selected_object_type == 'mentos_pair' and
                            not self.mentos_seen_detection and not self.mentos_search_done):
                        # Search only once, after 20 s without any Mentos detection.
                        virtual_y = min(479, round(best_y + y_down + 40.))
                        virtual_roi = (round(best_x) - 1, virtual_y,
                                       round(best_x) + 1, virtual_y + 1)
                        self.start_auto_fine_move('y', virtual_roi,
                            np.array([best_x, best_y], dtype=float), side_index, search=True)
                    else:
                        self._auto_fine_diag_print()
                        self.pause_auto_fine('selected object not stable within 20 s')
                return
            target = {'x': best_x, 'y': best_y}
            status, ok_x, ok_y = tolerance_status(stable, target,
                x_tol, y_up, y_down)
            if status == 'OK':
                self.execute_phase = 'fine_done'
                self.fine_request = None
                self.auto_fine_paused = True
                self.auto_fine_ok = True
                self.auto_grasp_ready_at = time.monotonic() + .75
                self.status = (f'AUTO FINE OK x={stable[0]:.1f} top={stable[1]:.1f}'
                               ' - AUTO GRASP STARTING')
                print(self.status, flush=True)
                return
            axis = 'y' if not ok_y else 'x'
            x, y, w, h = detection.get('box', detection.get('bbox'))
            roi = (x, y, x + w, y + h)
            self.start_auto_fine_move(axis, roi,
                np.array([best_x, best_y], dtype=float), side_index)
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            self.pause_auto_fine(str(exc))
