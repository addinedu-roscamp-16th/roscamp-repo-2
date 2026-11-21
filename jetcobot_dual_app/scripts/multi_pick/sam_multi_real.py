#!/usr/bin/env python3
"""Isolated cooperative pick scheduler for alternating JetCobot arms.

The single-object system (moveit_pick/, right_place_trial/) is NOT modified.
This file subclasses the real pick UI (moveit_pick/sam_dual_yaw_moveit.py) and
replaces only the ROI selection and perception:

  1. Freeze both cameras (first left-drag), drag ONE ROI PER OBJECT in each
     image (any order, up to 12 per image). Right-click deletes an ROI.
  2. M: SAM2 -> SuperPoint -> LightGlue per ROI, then a global one-to-one
     left/right association (copied from the Gazebo multi_roi_perception).
  3. The confirmed objects become a queue. They are recognised ONCE and used
     until the end (user decision 2026-11-19); the arms return to see_center
     between objects but no new capture is taken.
  4. Alternate arm assignments (each arm's turn takes the remaining object most
     on its own side). After E (or --auto-execute): approach, fine, grasp/lift,
     then carry and place at once. During the return to see_center, the next
     approach is planned from the return path's projected endpoint. Execution
     checks the measured start or re-plans.

One gate goal runs at a time, so arm motions are sequential; only planning
overlaps with motion.

Extra options (parsed here, the rest go to the original UI):
  --first-arm           first assigned arm (default left), then alternate
  --object-height-mm    top-surface prior (default 15)
  --floor-z-m           floor prior (default 0.007 -> top ~22 mm, as measured)
  --height-tolerance-mm (default 15; was 10 until 2026-11-20)
  --reproj-px-multi / --epipolar-px  stereo checks used only to pair ROIs (12 / 10; were 8 / 6 until 2026-11-20 --
                        about 0.5 deg camera drift after a return already costs ~5 px);
                        final positions use the single UI's --reproj-px (30) like one object
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import types
from datetime import datetime
from pathlib import Path


def _pkg_share(name, fallback):
    """Installed ROS package share dir; old source path when not built."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory(name))
    except Exception:
        return Path(fallback).expanduser()


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / 'moveit_pick'))
sys.path.append(str(HERE.parent.parent))  # shared perception helpers in camera_code
shared_path = str(HERE.parent.parent)
pythonpath = os.environ.get('PYTHONPATH', '').split(os.pathsep)
if shared_path not in pythonpath:
    os.environ['PYTHONPATH'] = os.pathsep.join([shared_path, *filter(None, pythonpath)])

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import sam_dual_yaw_moveit as sam  # noqa: E402
from multi_roi_perception import MultiROIPerception  # noqa: E402
from state_machine import WorkState, alternating_assignments  # noqa: E402
from stereo_self_correct import correct_right_rotation  # noqa: E402

MAX_ROIS = 12


def parse_multi_args():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--auto-execute', action='store_true')
    ap.add_argument('--order-ids', nargs='+', metavar='OBJECT_ID',
                    help='Pick only these matched ROI IDs (for example object_1 object_3)')
    ap.add_argument('--first-arm', choices=('left', 'right'), default='left',
                    help='Alternate assignments from this arm; an unreachable assigned arm fails closed')
    ap.add_argument('--object-height-mm', type=float, default=15.0)
    ap.add_argument('--floor-z-m', type=float, default=0.007)
    ap.add_argument('--height-tolerance-mm', type=float, default=15.0)
    ap.add_argument('--reproj-px-multi', dest='reproj_px', type=float, default=12.0)
    ap.add_argument('--epipolar-px', type=float, default=10.0)
    ap.add_argument('--no-self-correct', action='store_true',
                    help='Disable the per-capture right-camera rotation fix from scene features')
    ap.add_argument('--min-valid-points-multi', dest='min_valid_points', type=int, default=8)
    ap.add_argument('--min-valid-ratio', type=float, default=0.30)
    ap.add_argument('--assignment-margin', type=float, default=0.2)
    known, rest = ap.parse_known_args(sys.argv[1:])
    sys.argv = [sys.argv[0]] + rest
    return known


MULTI = parse_multi_args()
PERCEPTION_SETTINGS = types.SimpleNamespace(
    mask_erode=2, max_keypoints=1024, min_angle_deg=1.5,
    reproj_px=MULTI.reproj_px, epipolar_px=MULTI.epipolar_px,
    min_valid_points=MULTI.min_valid_points, min_valid_ratio=MULTI.min_valid_ratio,
    assignment_margin=MULTI.assignment_margin,
    object_height_mm=MULTI.object_height_mm, floor_z_m=MULTI.floor_z_m,
    height_tolerance_mm=MULTI.height_tolerance_mm,
    # Chocobar 90 x 35 x 15 mm (pick_objects.yaml); generous for sparse features.
    max_spread_m=0.14, length_range_m=(0.05, 0.12), width_range_m=(0.015, 0.05))


class MultiApp(sam.SAMYawApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.multi_rois = [[], []]
        self.multi_drag = None
        self.queue = []
        self.queue_total = 0
        self.queue_started = False
        self.multi_folder = None
        self.auto_execute_pending = False
        self.work = WorkState()
        self.active_arm = None
        self.held_jobs = []
        self.queue_auto_started = False
        self.bg = None
        self.bg_waiting = False
        self.bg_pending_arm = None
        self.operator_paused = False
        self.return_settling = None
        self.active_object = None
        self.recovery = None
        self.manual_restart = False
        self.place_planning = False
        self.plan_wait_sample = None
        self.plan_wait_seconds = 0.0
        self.next_start_retry_at = 0.0
        self.see_center_z = {}
        self.see_center_joints = {}
        self.tcp_mount = {side: sam.fixed_6_to_tcp(self.args.urdf, side)
                          for side in ('left', 'right')}
        # Final position = the single-object UI's triangulation settings.
        PERCEPTION_SETTINGS.single_reproj_px = self.args.reproj_px
        PERCEPTION_SETTINGS.single_min_angle_deg = self.args.min_angle_deg
        PERCEPTION_SETTINGS.single_min_valid_points = self.args.min_valid_points
        self.perception = MultiROIPerception(self.sam_predictor, self.extractor, self.matcher,
                                             self.device, PERCEPTION_SETTINGS)

    # ----------------------------------------------------------- ROI input
    def _freeze(self):
        if not all(c.is_live() for c in self.cameras):
            print('Both UDP cameras must be LIVE.', flush=True)
            return False
        if any(c.frame.shape[:2] != (480, 640) for c in self.cameras):
            print('Expected 640x480; calibration K is for 640x480.', flush=True)
            return False
        try:
            self.T_wc, self.frozen_joints, self.frozen_stamps = self.live_poses.snapshot()
            self.T_wc_measured = [transform.copy() for transform in self.T_wc]
        except RuntimeError as exc:
            print('Cannot capture measured poses:', exc, flush=True)
            return False
        self.frozen = [cam.frame.copy() for cam in self.cameras]
        self.capture_time_unix = time.time()
        self.status = 'FROZEN - ONE ROI PER OBJECT IN EACH IMAGE, THEN M'
        print('Captured frozen stereo pair. Drag one ROI per object in BOTH images; M = match.',
              flush=True)
        return True

    def on_mouse(self, event, x, y, flags, userdata):
        if self.queue_started:
            return  # The frozen multi-object result owns the queue until completion.
        if self.capture_consumed:
            return super().on_mouse(event, x, y, flags, userdata)
        if self.motion_child is not None and self.motion_child.poll() is None:
            return
        idx, pt = self.panel_coord(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.frozen is None and not self._freeze():
                return
            self.multi_drag = (idx, pt, pt)
        elif event == cv2.EVENT_MOUSEMOVE and self.multi_drag is not None:
            self.multi_drag = (self.multi_drag[0], self.multi_drag[1], pt if idx == self.multi_drag[0]
                               else self.multi_drag[2])
        elif event == cv2.EVENT_LBUTTONUP and self.multi_drag is not None:
            side, a, b = self.multi_drag
            self.multi_drag = None
            x1, x2 = sorted((a[0], b[0]))
            y1, y2 = sorted((a[1], b[1]))
            if x2 - x1 < 10 or y2 - y1 < 10:
                return
            if len(self.multi_rois[side]) >= MAX_ROIS:
                print(f'At most {MAX_ROIS} ROIs per image.', flush=True)
                return
            self.multi_rois[side].append([int(x1), int(y1), int(x2), int(y2)])
            print(f'{("LEFT", "RIGHT")[side]} ROI {len(self.multi_rois[side])}: {[x1, y1, x2, y2]}',
                  flush=True)
        elif event == cv2.EVENT_RBUTTONDOWN:
            for k, (x1, y1, x2, y2) in enumerate(self.multi_rois[idx]):
                if x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2:
                    del self.multi_rois[idx][k]
                    print(f'{("LEFT", "RIGHT")[idx]} ROI {k+1} deleted.', flush=True)
                    break

    def view(self):
        out = super().view()
        if self.queue_started or self.frozen is None:
            if self.queue_started:
                cv2.putText(out, f'MULTI: object {self.queue_total-len(self.queue)}/{self.queue_total}',
                            (10, 56), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
                status = (f'L {self.work.arms["left"]} | R {self.work.arms["right"]} '
                          f'| PICK {self.work.pick_owner or "free"} '
                          f'| CASE {self.work.basket_owner or "free"}')
                if self.bg is not None:
                    status += (f' | NEXT {self.bg["arm"][0].upper()} '
                               f'{"planning" if self.bg["child"] is not None else "ready"}')
                cv2.putText(out, status, (10, 82), cv2.FONT_HERSHEY_SIMPLEX,
                            .52, (0, 255, 255), 2)
            return out
        for side in range(2):
            for k, (x1, y1, x2, y2) in enumerate(self.multi_rois[side]):
                ox = side * sam.PANEL_W
                cv2.rectangle(out, (x1+ox, y1), (x2+ox, y2), (0, 255, 0), 2)
                cv2.putText(out, f'{("L", "R")[side]}{k+1}', (x1+ox+3, max(48, y1-4)),
                            cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 255, 0), 2)
        if self.multi_drag is not None:
            side, a, b = self.multi_drag
            ox = side * sam.PANEL_W
            cv2.rectangle(out, (a[0]+ox, a[1]), (b[0]+ox, b[1]), (0, 255, 255), 2)
        return out

    def reset(self):
        if getattr(self, '_keep_queue', False):
            return super().reset()
        if self.operator_paused and self.motion_child is not None:
            print('STATE: wait for worker cancellation before resetting.', flush=True)
            return
        if (self.args.enable_execute and self.queue_started and
                (self.held_jobs or self.work.pick_owner or
                 self.work.basket_owner)):
            print('STATE: resource occupied; R cannot discard the running queue. '
                  'Stop and inspect the robot before restarting the UI.', flush=True)
            return
        super().reset()
        self.multi_rois = [[], []]
        self.multi_drag = None
        self.queue = []
        self.queue_total = 0
        self.queue_started = False
        self.auto_execute_pending = False
        self.work = WorkState()
        self.active_arm = None
        self.held_jobs = []
        self.queue_auto_started = False
        self.bg = None
        self.bg_waiting = False
        self.bg_pending_arm = None
        self.operator_paused = False
        self.return_settling = None
        self.active_object = None
        self.recovery = None
        self.manual_restart = False
        self.place_planning = False
        self.plan_wait_sample = None
        self.plan_wait_seconds = 0.0
        self.next_start_retry_at = 0.0
        self.see_center_z = {}

    # ---------------------------------------------------------- perception
    def run(self):
        if self.queue_started or self.capture_consumed:
            print('Objects already recognised; R resets (queue is discarded).', flush=True)
            return
        if self.frozen is None or not self.multi_rois[0] or not self.multi_rois[1]:
            print('Freeze and draw at least one ROI in EACH image first.', flush=True)
            return
        self.status = 'MULTI PERCEPTION WORKING...'
        try:
            self._run_multi()
        except Exception as exc:
            self.status = 'MULTI PERCEPTION FAILED - SEE TERMINAL / R RESET'
            print(f'Failed: {type(exc).__name__}: {exc}', flush=True)

    def _run_multi(self):
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        folder = self.args.out_dir / f'multi_capture_{stamp}'
        folder.mkdir(parents=True, exist_ok=True)
        if self.args.stereo_fixed_see_center:
            self.T_wc, info = sam.fixed_see_center_camera_poses(
                self.T_wc_measured, self.frozen_joints, sam.DEFAULT_STEREO_CORRECTION)
            print(f'Fixed see_center stereo correction: max joint difference '
                  f'{info["max_joint_difference_deg"]:.2f} deg', flush=True)
        self_fix = {'applied': False, 'reason': 'disabled'}
        if not MULTI.no_self_correct:
            self.T_wc, self_fix = correct_right_rotation(self.T_wc, self.frozen, sam.K, sam.DIST)
            if self_fix['applied']:
                print(f'STEREO SELF-CORRECTION: right camera {self_fix["rotation_fix_deg"]:.2f} deg, '
                      f'epipolar {self_fix["median_before_px"]:.1f} -> {self_fix["median_after_px"]:.1f} px '
                      f'({self_fix["points"]} scene points)', flush=True)
            else:
                print(f'STEREO SELF-CORRECTION not applied: {self_fix.get("reason")} '
                      f'{ {k: v for k, v in self_fix.items() if k.startswith("median")} }', flush=True)
        metadata = {'capture_time_unix': float(self.capture_time_unix),
                    'joint_positions_rad': self._joint_dict(self.frozen_joints),
                    'stereo_fixed_see_center': bool(self.args.stereo_fixed_see_center),
                    'stereo_self_correction': self_fix}
        started = time.monotonic()
        report, overlay = self.perception.run(self.frozen, self.multi_rois, self.T_wc, folder,
                                              metadata, progress=print)
        print(f'TIMING MULTI perception: {time.monotonic()-started:.1f} s', flush=True)
        self.result = overlay
        objects = report['objects']
        self.topview = self.topdown_map(objects)
        cv2.imwrite(str(folder / 'topdown_map.png'), self.topview)
        self.capture_consumed = True
        self.multi_folder = folder
        # The base UI's reset() after each place clears capture_time_unix;
        # keep the recognition time separately.
        self.multi_capture_time = float(self.capture_time_unix)
        if not objects:
            self.status = 'NO CONFIRMED OBJECT - R RECAPTURE'
            print('No object passed the stereo association. R to recapture.', flush=True)
            return
        for obj in objects:
            xyz = obj['center_xyz_mm']
            print(f'  {obj["id"]} L{obj["left_roi"]}-R{obj["right_roi"]}: '
                  f'XYZ {np.round(xyz, 1).tolist()} mm, long yaw {obj["object_long_yaw_deg"]:+.1f} deg, '
                  f'{obj["valid_points"]} points', flush=True)
        if MULTI.order_ids:
            by_id = {obj['id']: obj for obj in objects}
            missing = sorted(set(MULTI.order_ids) - by_id.keys())
            if missing or len(set(MULTI.order_ids)) != len(MULTI.order_ids):
                self.status = 'ORDER IDS INVALID - R RECAPTURE'
                print(f'{self.status}: missing={missing}, repeated IDs are not allowed', flush=True)
                return
            objects = [by_id[object_id] for object_id in MULTI.order_ids]
        assignments = alternating_assignments(len(objects), MULTI.first_arm)
        self.queue = self._order_by_side(objects, assignments)
        self.work = WorkState()
        self.see_center_z = self._load_see_center_z()
        self.queue_total = len(objects)
        self.queue_started = True
        (folder / 'queue.json').write_text(json.dumps(self.queue, indent=2, ensure_ascii=False))
        print(f'STATE QUEUE: {len(objects)} object(s); '
              f'assignment={[item["assigned_arm"] for item in self.queue]}.',
              flush=True)
        self.start_next_object()

    def topdown_map(self, objects, size=(560, 640), margin=50):
        """Top view in right_base_link: objects (pick order), case, arm bases."""
        height, width = size
        image = np.full((height, width, 3), 255, np.uint8)
        bases = [('R base', np.zeros(2))]
        try:
            bases.append(('L base', self.live_poses.T_right_left[:2, 3] * 1000.0))
        except Exception:
            pass
        case_corners = None
        try:
            import yaml
            case = yaml.safe_load((_pkg_share('jetcobot_dual_control', '~/pinky_ws/src/jetcobot_dual/jetcobot_dual_control') / 'config' / 'tin_case.yaml').read_text())
            cx, cy = case['bottom_center_xyz_mm'][:2]
            case_corners = np.array([[cx-37, cy-49], [cx+37, cy-49], [cx+37, cy+49], [cx-37, cy+49]], float)
        except Exception:
            pass
        shapes = []
        for obj in objects:
            c = np.asarray(obj['center_xyz_mm'][:2], float)
            length, widthm = (obj.get('estimated_footprint_mm') or [90.0, 35.0])[:2]
            yaw = np.deg2rad(obj['object_long_yaw_deg'])
            u = np.array([np.cos(yaw), np.sin(yaw)]); v = np.array([-u[1], u[0]])
            shapes.append((obj, c, np.array([c + a*length/2*u + b*widthm/2*v
                                             for a, b in ((1, 1), (1, -1), (-1, -1), (-1, 1))])))
        points = [b for _, b in bases] + [p for _, _, k in shapes for p in k]
        if case_corners is not None:
            points += list(case_corners)
        points = np.array(points)
        low, high = points.min(axis=0) - 30, points.max(axis=0) + 30
        # screen: +X up, +Y left (view from above the robot base frame)
        scale = min((height - 2*margin) / (high[0]-low[0]), (width - 2*margin) / (high[1]-low[1]))

        def px(xy):
            return (int(width - margin - (xy[1]-low[1]) * scale), int(height - margin - (xy[0]-low[0]) * scale))
        for step in range(-600, 601, 50):
            cv2.line(image, px((step, -600)), px((step, 600)), (235, 235, 235), 1)
            cv2.line(image, px((-600, step)), px((600, step)), (235, 235, 235), 1)
        for name, b in bases:
            cv2.circle(image, px(b), 8, (60, 60, 60), -1)
            cv2.putText(image, name, (px(b)[0] + 10, px(b)[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, .45, (60, 60, 60), 1)
        if case_corners is not None:
            cv2.polylines(image, [np.array([px(k) for k in case_corners])], True, (40, 120, 220), 2)
            centre = px(case_corners.mean(axis=0))
            cv2.putText(image, 'CASE', (centre[0] - 20, centre[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, .5, (40, 120, 220), 2)
        for order, (obj, c, corners) in enumerate(shapes, 1):
            color = (0, 150, 0)
            cv2.polylines(image, [np.array([px(k) for k in corners])], True, color, 2)
            cv2.putText(image, str(order), (px(c)[0] - 6, px(c)[1] + 6), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)
            cv2.putText(image, f'{order}: {obj["id"]} [{c[0]:.0f}, {c[1]:.0f}] mm, {obj["object_long_yaw_deg"]:+.0f} deg',
                        (10, 18 + 18 * order), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 0), 1)
        cv2.putText(image, 'Top view, right_base_link (+X up, +Y left), grid 50 mm. Numbers = pick order',
                    (10, height - 10), cv2.FONT_HERSHEY_SIMPLEX, .42, (90, 90, 90), 1)
        return image

    def _order_by_side(self, objects, assignments):
        """Keep strict alternation, but give each arm's turn the remaining object
        most on its own side (2026-11-19: a far reach tilted and could not lift)."""
        try:
            bases = {'right': np.zeros(2),
                     'left': np.asarray(self.live_poses.T_right_left[:2, 3]) * 1000.0}
        except Exception as exc:  # keep ROI order if the base transform is missing
            print(f'STATE: left base unknown ({exc}); keeping ROI order.', flush=True)
            return [dict(obj, assigned_arm=arm) for obj, arm in zip(objects, assignments)]
        remaining, queue = list(objects), []
        for arm in assignments:
            other = 'left' if arm == 'right' else 'right'

            def side_score(obj):
                xy = np.asarray(obj['center_xyz_mm'][:2], float)
                return np.linalg.norm(xy - bases[arm]) - np.linalg.norm(xy - bases[other])
            best = min(remaining, key=side_score)
            remaining.remove(best)
            queue.append(dict(best, assigned_arm=arm))
        return queue

    @staticmethod
    def _joint_dict(joints):
        joints = np.asarray(joints)
        return {f'{side}_{i}_Joint': float(joints[index, i-1])
                for index, side in enumerate(('left', 'right')) for i in range(1, 7)}

    # --------------------------------------------------------------- queue
    def _load_see_center_z(self):
        """Compute each TCP's base-frame Z from MoveIt's actual named state."""
        import xml.etree.ElementTree as ET
        from rcl_interfaces.srv import GetParameters
        node = self.live_poses.node
        client = node.create_client(GetParameters,
                                    '/jetcobot_dual/move_group/get_parameters')
        if not client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('Cannot read MoveIt see_center states')
        future = client.call_async(GetParameters.Request(
            names=['robot_description_semantic']))
        deadline = time.monotonic() + 8.0
        while not future.done() and time.monotonic() < deadline:
            self.live_poses.ros.spin_once(node, timeout_sec=0.05)
        if not future.done():
            raise RuntimeError('MoveIt see_center state query timed out')
        values = future.result().values
        if len(values) != 1 or not values[0].string_value:
            raise RuntimeError('MoveIt semantic model unavailable')
        xml = ET.fromstring(values[0].string_value)
        heights = {}
        self.see_center_joints = {}
        for side in ('left', 'right'):
            rows = [row for row in xml.findall('group_state')
                    if row.get('name') == 'see_center' and
                    row.get('group') == f'arm_{side}']
            if len(rows) != 1:
                raise RuntimeError(f'No unique {side} see_center state')
            joints = {row.get('name'): float(row.get('value'))
                      for row in rows[0].findall('joint')}
            if len(joints) != 6:
                raise RuntimeError(f'Incomplete {side} see_center state')
            tcp = self.live_poses.wrist_transform(side, joints) @ self.tcp_mount[side]
            heights[side] = float(tcp[2, 3])
            self.see_center_joints[side] = joints
        print(f'STATE see_center TCP Z (each base): '
              f'{ {side: round(z*1000, 1) for side, z in heights.items()} } mm', flush=True)
        return heights

    def _watch_pick_height(self):
        side = self.work.pick_owner
        if (side is None or side not in self.see_center_z or self.work.stopped or
                self.work.arms[side] in ('paused', 'error')):
            return
        # The arm starts its approach at see_center. That initial height must
        # not release the pick area before the object has actually been lifted.
        if not any(job['arm'] == side for job in self.held_jobs):
            return
        sample = self.live_poses.samples.get(side)
        if sample is None or time.monotonic() - sample[0] > 1.5:
            return
        tcp = self.live_poses.wrist_transform(side, sample[2]) @ self.tcp_mount[side]
        z = float(tcp[2, 3])
        if self.work.release_pick_at_height(side, z, self.see_center_z[side]):
            print(f'STATE PICK RELEASED by {side.upper()}: TCP Z={z*1000:.1f} mm '
                  f'>= see_center {self.see_center_z[side]*1000:.1f} mm', flush=True)

    def _await_pick_release(self, side, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.work.pick_owner == side and time.monotonic() < deadline:
            self.live_poses.poll()
            self._watch_pick_height()
            if self.work.pick_owner == side:
                time.sleep(0.05)
        return self.work.pick_owner != side

    def _write_object_target(self, obj, number, arm, joints, stamps, assumed=None):
        folder = self.multi_folder / f'run_{number:02d}_{obj["id"]}'
        folder.mkdir(parents=True, exist_ok=True)
        yaw = float(obj['closing_axis_yaw_deg'])
        long_yaw = float(obj['object_long_yaw_deg'])
        # Position/yaw come from the single recognition (world frame). Capture
        # time and joints are refreshed so the existing stale-capture checks
        # compare against the current pose; the object is assumed not to move.
        multi = {'index': number, 'total': self.queue_total, 'id': obj['id'],
                 'original_capture_time_unix': self.multi_capture_time,
                 'assigned_arm': arm, 'peer_holding': False}
        if assumed:
            multi['assumed_start_rad'] = dict(assumed)
        target = {
            'frame': 'right_base_link',
            'object_type': 'chocobar' if assumed else None,
            'center_xyz_mm': [float(v) for v in obj['center_xyz_mm']],
            'closing_axis_yaw_deg': yaw,
            'selected_arm': arm,
            'yaw_by_side': obj.get('yaw_by_side') or {
                side: {'closing_axis_yaw_deg': yaw, 'object_long_yaw_deg': long_yaw}
                for side in ('left', 'right')},
            'selected_yaw_camera': obj.get('selected_yaw_camera') or 'right',
            'yaw_view_scores': {},
            'yaw_view_details': {},
            'object_long_yaw_deg': long_yaw,
            'valid_points': int(obj['valid_points']),
            'warnings': list(obj.get('warnings', [])) + [
                'multi-object queue: recognised once at the first capture'],
            'capture_time_unix': time.time(),
            'joint_positions_rad': self._joint_dict(joints),
            'joint_stamps_unix': np.asarray(stamps).tolist(),
            'urdf': str(self.args.urdf),
            'approach_mode': 'tcp_above_target',
            'observation_tcp_xy_m': [0.0, 0.0],
            'observation_above_mm': float(self.args.above_mm),
            'source': str(self.multi_folder),
            'multi_object': multi,
        }
        preview = folder / 'moveit_target.json'
        preview.write_text(json.dumps(target, indent=2, ensure_ascii=False))
        return preview, target

    def _apply_object(self, arm, preview, target, joints, stamps):
        self.active_arm = arm
        self.args.arm = arm
        self.args.group = f'arm_{arm}'
        self.args.tip_link = f'{arm}_TCP'
        self.preview_file = preview
        self.frozen_joints = np.asarray(joints)
        self.frozen_stamps = stamps
        self.capture_time_unix = target['capture_time_unix']
        self.capture_consumed = True

    def start_next_object(self):
        if not self.queue:
            self.queue_started = False
            self.status = f'ALL {self.queue_total} OBJECTS DONE - R NEW CAPTURE'
            print(self.status, flush=True)
            return
        obj = self.queue[0]
        number = self.queue_total - len(self.queue) + 1
        assigned_arm = obj['assigned_arm']
        self._wait_arms_still()
        try:
            _, joints, stamps = self.live_poses.snapshot()
        except RuntimeError as exc:
            self.status = 'MULTI: NO FRESH JOINTS - CHECK PI / R'
            print(f'Cannot start object {number}: {exc}', flush=True)
            self.next_start_retry_at = time.monotonic() + 2.0
            return
        if not self.work.request_pick(assigned_arm):
            self.status = f'{assigned_arm.upper()} PICK WAITING - PRODUCT AREA OCCUPIED'
            print(self.status, flush=True)
            self.next_start_retry_at = time.monotonic() + 0.5
            return
        self.active_object = self.queue.pop(0)
        self.next_start_retry_at = 0.0
        preview, target = self._write_object_target(obj, number, assigned_arm, joints, stamps)
        self._apply_object(assigned_arm, preview, target, joints, stamps)
        print(f'STATE OBJECT {number}/{self.queue_total}: {obj["id"]} -> {assigned_arm.upper()} '
              f'XYZ {np.round(target["center_xyz_mm"], 1).tolist()} mm', flush=True)
        self.auto_execute_pending = self.queue_auto_started or (MULTI.auto_execute and not self.manual_restart)
        self.select_object_type('chocobar')
        if self.motion_child is None:
            self.pause_motion('approach planner did not start', stage='PLAN')

    # ------------------------------------------- background next-arm plan
    def _start_background_plan(self, placing_arm, endpoint, *, before_execution=False):
        """Plan the next approach from both arms' projected return state."""
        if self.bg is not None or not self.queue:
            return
        obj = self.queue[0]
        arm = obj['assigned_arm']
        names = [[f'{side}_{i}_Joint' for i in range(1, 7)]
                 for side in ('left', 'right')]
        if (arm == placing_arm or self.work.arms[arm] not in ('idle', 'pick_waiting') or
                set(endpoint) != {name for row in names for name in row}):
            return
        try:
            _, _, stamps = self.live_poses.snapshot()
        except RuntimeError as exc:
            print(f'STATE: no background plan ({exc}); will plan after placement.', flush=True)
            return
        joints = np.array([[endpoint[name] for name in row] for row in names], dtype=float)
        if not np.isfinite(joints).all():
            return
        assumed = self._joint_dict(joints)
        number = self.queue_total - len(self.queue) + 1
        preview, target = self._write_object_target(obj, number, arm, joints, stamps, assumed)
        cmd = self.approach_command(preview)
        logpath = preview.parent / 'moveit_plan_background.log'
        log = logpath.open('w')
        try:
            # Own node name: two simultaneous 'dual_arm_coordinator' nodes lost
            # the place worker's get_planning_scene response (2026-11-20).
            env = dict(os.environ, JETCOBOT_COORD_NODE='dual_arm_coordinator_next_plan')
            child = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, env=env)
        except Exception as exc:
            log.close()
            print(f'STATE: background plan did not start ({exc}).', flush=True)
            return
        self.bg = {'obj': obj, 'number': number, 'arm': arm, 'preview': preview,
                   'target': target, 'joints': joints, 'stamps': stamps, 'child': child,
                   'log': log, 'logpath': logpath, 'code': None, 'started': time.monotonic()}
        timing = ('before first execution' if before_execution
                  else f'while {placing_arm.upper()} returns to see_center')
        self.bg['timing'] = timing
        print(f'STATE NEXT PLAN: object {number} -> {arm.upper()} {timing}; log={logpath}',
              flush=True)

    def _poll_background(self):
        bg = self.bg
        if bg is None or bg['child'] is None:
            return
        code = bg['child'].poll()
        if code is None:
            return
        bg['log'].close()
        bg['child'] = None
        bg['code'] = code
        print(f'STATE NEXT PLAN exit={code} in {time.monotonic() - bg["started"]:.1f} s; '
              f'log={bg["logpath"]}', flush=True)
        if code != 0:
            try:
                print(''.join(bg['logpath'].read_text().splitlines(True)[-12:]), end='', flush=True)
            except OSError:
                pass
        if self.bg_waiting:
            self._record_stationary_plan_wait()
            self._adopt_background()
        elif not self.queue_auto_started and self.plan_ready and self._background_usable(bg):
            self.status = 'APPROACH PLANS READY - E EXECUTE'

    def _background_usable(self, bg):
        if bg['code'] != 0:
            return False
        try:
            summary = json.loads((bg['preview'].parent / 'moveit_plan_summary.json').read_text())
        except (OSError, ValueError):
            return False
        age = time.time() - bg['target']['capture_time_unix']
        return (summary.get('selected_arm') == bg['arm'] and
                summary.get('execution_allowed') is not False and
                age < self.args.max_capture_age - 15)

    def _prepare_next_at_return(self, placing_arm, endpoint):
        bg = self.bg
        if bg is not None and bg['child'] is None:
            assumed = bg['target']['multi_object']['assumed_start_rad']
            drift = max(abs(endpoint[name] - value) for name, value in assumed.items())
            if self._background_usable(bg) and np.rad2deg(drift) <= self.args.max_state_change_deg:
                return
            print('STATE: advance plan does not match the return; planning from its endpoint.',
                  flush=True)
            self.bg = None
        self._start_background_plan(placing_arm, endpoint)

    def _adopt_background(self):
        bg, self.bg = self.bg, None
        self.bg_waiting = False
        arm = bg['arm']
        usable = bool(self.queue and self.queue[0] is bg['obj'] and
                      self._background_usable(bg))
        super().reset()  # per-object UI state only; queue and work state are kept
        if not usable:
            print(f'STATE: background plan not usable (exit={bg["code"]}); '
                  'planning from the current pose.', flush=True)
            self.start_next_object()
            return
        if not self.work.request_pick(arm):
            self.status = f'{arm.upper()} PICK WAITING - PRODUCT AREA OCCUPIED'
            print(self.status, flush=True)
            self.next_start_retry_at = time.monotonic() + 0.5
            return
        self.active_object = self.queue.pop(0)
        self._apply_object(arm, bg['preview'], bg['target'], bg['joints'], bg['stamps'])
        self.selected_object_type = 'chocobar'
        self.args.object_type = 'chocobar'
        self.plan_ready = True
        print(f'STATE OBJECT {bg["number"]}/{self.queue_total}: {bg["obj"]["id"]} -> '
              f'{arm.upper()} (approach planned {bg["timing"]})', flush=True)
        now = time.monotonic()
        self.return_settling = {'after': now, 'deadline': now + 8.0,
                                'sample': None, 'since': None}
        self.status = 'WAITING FOR FRESH STABLE JOINTS'

    def _poll_return_settling(self):
        settling = self.return_settling
        now = time.monotonic()
        if now >= settling['deadline']:
            self.return_settling = None
            self.pause_motion('joint feedback did not settle after see_center return', stage='PLAN')
            return
        samples = [self.live_poses.samples.get(side) for side in ('left', 'right')]
        if any(sample is None or sample[0] < settling['after'] or now - sample[0] > 1.5
               for sample in samples):
            settling.update(sample=None, since=None)
            return
        received = tuple(sample[0] for sample in samples)
        if abs(received[0] - received[1]) > 0.75:
            settling.update(sample=None, since=None)
            return
        previous = settling['sample']
        if previous and not all(new > old for new, old in zip(received, previous[0])):
            return
        joints = np.array([list(sample[2].values()) for sample in samples])
        if previous is None or np.rad2deg(np.max(np.abs(joints - previous[1]))) > 0.3:
            settling['since'] = min(received)
            settling['sample'] = (received, joints)
            return
        if min(received) - settling['since'] < 0.5:
            return
        self.return_settling = None
        if self.queue_auto_started or (MULTI.auto_execute and not self.manual_restart):
            self.start_motion(execute=True)
        else:
            self.status = 'PLAN READY - E EXECUTE'

    def _wait_arms_still(self, timeout=8.0, tol_deg=0.3, needed=2):
        """After a return the joints still settle (1.14 deg in 1 s, 2026-11-19)."""
        deadline = time.monotonic() + timeout
        previous, still = None, 0
        while time.monotonic() < deadline:
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                self.live_poses.poll()
                time.sleep(0.02)
            try:
                _, joints, _ = self.live_poses.snapshot()
            except RuntimeError:
                continue
            if previous is not None and np.rad2deg(np.max(np.abs(joints - previous))) <= tol_deg:
                still += 1
                if still >= needed:
                    return True
            else:
                still = 0
            previous = joints
        print('MULTI: arms did not settle within 8 s; continuing.', flush=True)
        return False

    def _refresh_target_joints(self):
        """Queue targets are world-frame: re-stamp joints/time to the current pose."""
        _, joints, stamps = self.live_poses.snapshot()
        target = json.loads(self.preview_file.read_text())
        target['joint_positions_rad'] = self._joint_dict(joints)
        target['joint_stamps_unix'] = np.asarray(stamps).tolist()
        target['capture_time_unix'] = time.time()
        target.get('multi_object', {}).pop('assumed_start_rad', None)
        self.preview_file.write_text(json.dumps(target, indent=2, ensure_ascii=False))
        self.frozen_joints = np.asarray(joints)
        self.frozen_stamps = stamps
        self.capture_time_unix = target['capture_time_unix']

    def start_motion(self, execute=False):
        if self.return_settling is not None:
            return
        if (self.queue_started and self.execute_phase == 'approach' and self.preview_file is not None
                and self.frozen_joints is not None):
            try:
                _, joints, _ = self.live_poses.snapshot()
                drift = float(np.rad2deg(np.max(np.abs(joints - self.frozen_joints))))
            except RuntimeError:
                drift = 0.0
            if drift > self.args.max_state_change_deg:
                print(f'MULTI: arms moved {drift:.2f} deg since this object\'s plan; '
                      're-planning from the current pose (object position is world-frame).', flush=True)
                self._wait_arms_still()
                self._refresh_target_joints()
                self.plan_ready = False
                self.auto_execute_pending = self.queue_auto_started or (MULTI.auto_execute and not self.manual_restart)
                return super().start_motion(execute=False)
        result = super().start_motion(execute=execute)
        if execute and self.execute_phase == 'approach' and self.motion_child is not None:
            self.queue_auto_started = True
            self.manual_restart = False
        return result

    def _launch_worker(self, stage, script, args, folder):
        if self.motion_child is not None:
            raise RuntimeError('Another state_pick worker is already active')
        folder.mkdir(parents=True, exist_ok=True)
        logpath = folder / f'{stage.lower()}_{datetime.now():%Y%m%d_%H%M%S_%f}.log'
        self.motion_log_writer = logpath.open('w')
        self.motion_log_reader = logpath.open('r')
        try:
            self.motion_child = subprocess.Popen(
                [str(self.args.motion_python), str(script), *args],
                stdout=self.motion_log_writer, stderr=subprocess.STDOUT,
                start_new_session=True)
        except Exception:
            self.motion_log_reader.close()
            self.motion_log_writer.close()
            self.motion_log_reader = self.motion_log_writer = None
            raise
        self.motion_stage = stage
        self.place_planning = False
        self.status = f'STATE {stage}: {self.motion_child.pid}'
        print(f'{self.status}; log={logpath}', flush=True)

    def _start_place(self, job):
        side = job['arm']
        if self.work.basket_owner != side:
            if not self.work.request_place(side):
                self.status = f'{side.upper()} PLACE WAITING - BASKET OCCUPIED'
                print(self.status, flush=True)
                return
        script = HERE.parent / 'right_place_trial' / 'coordinated_right_case.py'
        checkpoint = job['folder'] / 'placement_checkpoint.json'
        checkpoint.unlink(missing_ok=True)
        self._launch_worker('PLACE', script,
                            ['--execute', '--place', '--from-pick', '--arm', side,
                             '--checkpoint', str(checkpoint.resolve())], job['folder'])

    def _finish_custom(self, child, stage):
        rest = self.motion_log_reader.read()
        if rest:
            print(rest, end='', flush=True)
        self.motion_log_reader.close()
        self.motion_log_writer.close()
        self.motion_log_reader = self.motion_log_writer = self.motion_child = None
        self.motion_stage = None
        code = child.returncode
        print(f'STATE {stage} worker exit={code}', flush=True)
        if code != 0:
            self.bg_waiting = False
            self.pause_motion(f'{stage} worker exit={code}', stage=stage)
            return
        job = self.held_jobs[0]
        if self.work.pick_owner == job['arm'] and not self._await_pick_release(job['arm']):
            self.pause_motion('TCP has not reached see_center Z after placement', stage='PLACE')
            return
        self.work.returned_from_place(job['arm'])
        self.held_jobs.pop(0)
        self.active_arm = None
        self.active_object = None
        if self.held_jobs:
            self._start_place(self.held_jobs[0])
        elif self.bg is not None:
            self.bg_waiting = True
            if self.bg['child'] is None:
                self._adopt_background()
            else:
                self.status = f'WAITING FOR {self.bg["arm"].upper()} APPROACH PLAN'
                print(f'STATE: {self.status}', flush=True)
        elif self.queue:
            super().reset()
            self.start_next_object()
        else:
            self.queue_started = False
            self.status = f'ALL {self.queue_total} OBJECTS DONE - R NEW CAPTURE'
            print(self.status, flush=True)

    def _record_stationary_plan_wait(self):
        now = time.monotonic()
        waiting = (self.queue_started and not self.operator_paused and
                   ((self.motion_stage == 'PLAN' and self.motion_child is not None and
                     self.motion_child.poll() is None) or
                    (self.motion_stage == 'PLACE' and self.place_planning) or
                    (self.bg_waiting and self.bg and self.bg['child'] is not None)))
        samples = [self.live_poses.samples.get(side) for side in ('left', 'right')]
        if waiting and all(sample is not None and now - sample[0] <= 0.5 for sample in samples):
            received = tuple(sample[0] for sample in samples)
            previous = self.plan_wait_sample
            if previous is not None and not all(new > old for new, old in zip(received, previous[1])):
                return
            joints = np.array([list(sample[2].values()) for sample in samples])
            if previous is not None and np.rad2deg(np.max(np.abs(joints - previous[2]))) <= 0.3:
                elapsed = now - previous[0]
                if elapsed <= 0.5:
                    self.plan_wait_seconds += elapsed
            self.plan_wait_sample = (now, received, joints)
            return
        if not waiting and self.plan_wait_seconds > 0:
            seconds = self.plan_wait_seconds
            print(f'TIMING BOTH ARMS STATIONARY WAITING FOR PLAN: {seconds:.3f} s', flush=True)
            try:
                with (self.multi_folder / 'stationary_plan_wait.jsonl').open('a') as log:
                    obj = self.active_object or (self.queue[0] if self.queue else None)
                    log.write(json.dumps({'unix_time': time.time(), 'seconds': round(seconds, 3),
                                          'object': obj['id'] if obj else None}) + '\n')
            except OSError as exc:
                print(f'STATE: could not write planning wait measurement: {exc}', flush=True)
            self.plan_wait_seconds = 0.0
        self.plan_wait_sample = None

    def poll_motion(self):
        self._record_stationary_plan_wait()
        self._poll_background()
        if self.operator_paused:
            child = self.motion_child
            if child is not None:
                pending = self.motion_log_reader.read()
                if pending:
                    print(pending, end='', flush=True)
                code = child.poll()
                if code is not None:
                    self._timing(code)
                    self.motion_log_reader.close()
                    self.motion_log_writer.close()
                    self.motion_log_reader = self.motion_log_writer = self.motion_child = None
                    self.motion_stage = None
                    self.status = 'PAUSED - CHECK ROBOT AND GATE'
                    self._write_recovery()
                    print(f'STATE: paused worker exited ({code}); verify robot and gate.',
                          flush=True)
            return
        if self.return_settling is not None:
            self._poll_return_settling()
            return
        if self.queue_started:
            self._watch_pick_height()
            if (self.motion_child is None and self.active_arm is None and self.queue and
                    self.bg is None and
                    self.work.pick_owner is None and self.work.basket_owner is None and
                    not self.work.stopped and
                    self.work.arms[self.queue[0]['assigned_arm']] in ('idle', 'pick_waiting') and
                    time.monotonic() >= self.next_start_retry_at):
                self.start_next_object()
                return
        child, stage = self.motion_child, self.motion_stage
        if stage == 'PLACE':
            if child is None:
                return
            pending = self.motion_log_reader.read()
            if pending:
                print(pending, end='', flush=True)
                for line in pending.splitlines():
                    if line == 'STATE PLACE PLANNING BEGIN':
                        self.place_planning = True
                    elif line == 'STATE PLACE PLANNING END':
                        self.place_planning = False
                        self._record_stationary_plan_wait()
                    prefix = 'SEE_CENTER_RETURN_ENDPOINT '
                    if self.bg_pending_arm and line.startswith(prefix):
                        try:
                            endpoint = json.loads(line[len(prefix):])
                        except ValueError:
                            continue
                        side, self.bg_pending_arm = self.bg_pending_arm, None
                        if child.poll() is None:
                            self._prepare_next_at_return(side, endpoint)
            if child.poll() is not None:
                self.place_planning = False
                self._record_stationary_plan_wait()
                self.bg_pending_arm = None
                try:
                    self._finish_custom(child, stage)
                except Exception as exc:
                    self.pause_motion(f'{stage} handoff failed: {exc}', stage=stage)
            return
        self._keep_queue = self.queue_started
        try:
            try:
                super().poll_motion()
            except Exception as exc:
                self.pause_motion(f'Pick worker handoff failed: {exc}', stage=stage)
                return
        finally:
            self._keep_queue = False
        finished = child is not None and self.motion_child is None
        if not finished:
            return
        code = child.returncode
        if code != 0 and stage in ('PLAN', 'EXECUTE', 'FINE') and self.active_arm:
            self.pause_motion(f'{stage} worker exit={code}', stage=stage)
            return
        if stage == 'PLAN' and code == 0 and self.auto_execute_pending and self.plan_ready:
            self.auto_execute_pending = False
            print('STATE: auto-execute next assigned arm approach.', flush=True)
            self.start_motion(execute=True)
        elif (stage == 'PLAN' and code == 0 and self.plan_ready and self.queue_started
              and not self.queue_auto_started and self.args.enable_execute):
            center = {name: value for joints in self.see_center_joints.values()
                      for name, value in joints.items()}
            self._start_background_plan(self.active_arm, center, before_execution=True)

    def start_place_after_lift(self):
        """Carry and place at once (the gate runs one goal at a time, so holding
        at see_center only added two returns). The other arm's approach is
        planned in the background meanwhile."""
        side = self.active_arm
        if side is None or any(job['arm'] == side for job in self.held_jobs):
            raise RuntimeError('No unique assigned arm for the held object')
        job = {'arm': side, 'folder': self.preview_file.parent}
        self.held_jobs.append(job)
        self._watch_pick_height()
        try:
            self._start_place(job)
        except Exception as exc:
            self.pause_motion(f'Cannot start carry/place worker: {exc}', stage='PLACE')
            return
        # Wait until the return path is planned and its endpoint is available.
        if self.work.basket_owner == side and self.motion_child is not None:
            self.bg_pending_arm = side

    def start_return_see_center(self, open_grippers=True):
        if self.queue_started:
            print('STATE: C return blocked during an active queue; inspect the '
                  'robot and worker log before manual recovery.', flush=True)
            return
        return super().start_return_see_center(open_grippers=open_grippers)

    def start_open_gripper(self):
        if self.queue_started:
            print('STATE: O blocked during active queue; held-object ownership '
                  'must be resolved before opening a gripper.', flush=True)
            return
        return super().start_open_gripper()

    def start_lift(self):
        if any(job['arm'] == self.active_arm for job in self.held_jobs):
            print('STATE: this arm already completed its grasp lift.', flush=True)
            return
        return super().start_lift()

    def pause_motion(self, reason='operator pressed P', *, stage=None):
        if not self.operator_paused:
            stage = stage or self.motion_stage
            if stage is None and self.return_settling is not None:
                stage = 'PLAN'
            self.operator_paused = True
            self._record_stationary_plan_wait()
            self.pending = False
            self.return_settling = None
            self.auto_execute_pending = False
            self.auto_grasp_cancelled = True
            self.bg_waiting = False
            self.bg_pending_arm = None
            side = self.active_arm or self.work.basket_owner or self.work.pick_owner
            if side and not self.work.stopped and self.work.arms[side] != 'paused':
                self.work.pause(side, reason, release_pick=(stage == 'PLAN'))
            if self.active_object is not None:
                self.recovery = {'object': self.active_object['id'], 'arm': side,
                                 'stage': stage, 'phase': self.execute_phase,
                                 'reason': reason, 'pick_owner': self.work.pick_owner,
                                 'basket_owner': self.work.basket_owner}
                self._write_recovery()
            if self.args.auto_fine:
                self.pause_auto_fine(reason)
        self.status = ('PAUSED - STOP REQUESTED' if self.motion_child
                       else f'PAUSED - {reason}')
        if self.recovery is not None:
            print('STATE: see recovery.json; U to recover after checking the robot and gate.',
                  flush=True)
        print(f'STATE: {self.status}', flush=True)
        self.stop_motion()

    def _write_recovery(self):
        if self.recovery is None:
            return
        checkpoint = {}
        if self.held_jobs:
            path = self.held_jobs[0]['folder'] / 'placement_checkpoint.json'
            try:
                checkpoint = json.loads(path.read_text())
            except (OSError, ValueError):
                pass
        self.recovery['placement'] = checkpoint.get('stage', 'unconfirmed')
        self.recovery['pending_objects'] = [obj['id'] for obj in self.queue]
        try:
            path = self.multi_folder / 'recovery.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.recovery, indent=2, ensure_ascii=False))
            temporary.replace(path)
        except OSError as exc:
            print(f'STATE: could not write recovery record: {exc}', flush=True)

    def recover_work(self):
        if not self.operator_paused or self.recovery is None:
            return
        if self.motion_child is not None or (self.bg and self.bg['child'] is not None):
            print('STATE: wait for workers to exit before recovery.', flush=True)
            return
        self._write_recovery()
        recovery = self.recovery
        retry = (recovery['stage'] == 'PLAN' and not self.held_jobs and
                 self.work.pick_owner is None and self.work.basket_owner is None)
        if not retry:
            if recovery['placement'] not in ('scene_detached', 'returned'):
                print('STATE: physical progress unconfirmed; inspect robot, gate and recovery.json.',
                      flush=True)
                return
            try:
                _, joints, _ = self.live_poses.snapshot()
                actual = self._joint_dict(joints)
                error = max(abs(actual[name] - value) for center in self.see_center_joints.values()
                            for name, value in center.items())
            except (RuntimeError, ValueError, KeyError) as exc:
                print(f'STATE: cannot confirm see_center for recovery ({exc}).', flush=True)
                return
            if np.rad2deg(error) > self.args.max_state_change_deg:
                print('STATE: confirm the item is in the basket and recover both arms to see_center.',
                      flush=True)
                return
        if time.time() - self.multi_capture_time > self.args.max_capture_age and (retry or self.queue):
            print('STATE: item observations expired; recapture before continuing.', flush=True)
            return
        recovery['resolution'] = 'replan_pick' if retry else 'placement_confirmed_by_operator'
        if retry:
            self.queue.insert(0, self.active_object)
        self._write_recovery()
        self.active_object = self.active_arm = None
        self.held_jobs.clear()
        self.bg = None
        self.work = WorkState()
        self.queue_auto_started = False
        self.manual_restart = True
        self.operator_paused = False
        self.recovery = None
        super().reset()
        self.start_next_object()

    def stop_motion(self):
        super().stop_motion()
        bg = self.bg
        if bg is not None and bg['child'] is not None and bg['child'].poll() is None:
            try:
                os.killpg(bg['child'].pid, signal.SIGTERM)
                bg['child'].wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f'STATE: background worker exit unconfirmed ({exc}).', flush=True)
                return
            bg['log'].close()
            self.bg = None
            self.bg_waiting = False
            print('STATE: background approach plan stopped.', flush=True)


if __name__ == '__main__':
    # Same defaults as moveit_pick/manual_roi_drag.py.
    defaults = [('--fine-roi', ()), ('--match-rotation', ('--no-match-rotation',)),
                ('--enable-execute', ('--plan-only',))]
    if '--plan-only' not in sys.argv:
        defaults.append(('--auto-fine', ('--no-auto-fine', '--auto-left-fine',
                                        '--no-auto-left-fine')))
    for flag, alternatives in defaults:
        if flag not in sys.argv and not any(a in sys.argv for a in alternatives):
            sys.argv.insert(1, flag)
    sam.SAMYawApp = MultiApp
    sam.TOP_WINDOW = 'Top view | objects, case, pick order'
    sam.main(max_state_change_deg=3.0, pause_key=True)
