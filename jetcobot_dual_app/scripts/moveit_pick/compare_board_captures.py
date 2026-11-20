#!/usr/bin/env python3
"""Compare several board captures taken with the board NOT moved (2026-11-20).

For each capture, each camera's board pose is put in the world frame using the
measured joints + hand-eye (no stereo correction). If the board did not move,
every capture should give the same world board pose per camera. A change while
the joints stay the same means that camera/arm is physically somewhere else
(mount play or joint backlash). Also prints how much the stereo correction
would change. Read-only: no robot command, no file written.

Usage:
  python3 compare_board_captures.py            # last 3 board captures
  python3 compare_board_captures.py -n 4
  python3 compare_board_captures.py DIR1 DIR2 DIR3
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from make_stereo_correction import angle_deg, board_pose

HERE = Path(__file__).resolve().parent
CAPTURES = HERE.parent / 'sam_yaw_output'


def diff(A, B):
    D = np.linalg.inv(A) @ B
    return np.linalg.norm(B[:3, 3] - A[:3, 3]) * 1000.0, angle_deg(D[:3, :3])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('captures', nargs='*', type=Path)
    ap.add_argument('-n', type=int, default=3, help='use the last N board captures')
    args = ap.parse_args()
    caps = args.captures or sorted(CAPTURES.glob('board_capture_*'))[-args.n:]
    if len(caps) < 2:
        raise SystemExit(f'Need at least 2 board captures in {CAPTURES}')
    rows = []
    for cap in caps:
        d = np.load(cap / 'triangulation.npz', allow_pickle=True)
        poses = []
        for name, key in (('left.png', 'T_world_left_camera_measured'),
                          ('right.png', 'T_world_right_camera_measured')):
            T_cb, info = board_pose(cv2.imread(str(cap / name)), d['K'], d['DIST'], 6, 2.0)
            poses.append((d[key] @ T_cb, info['pnp_rmse_px']))
        (bl, rl), (br, rr) = poses
        correction = bl @ np.linalg.inv(br)  # = make_stereo_correction's C
        rows.append({'name': cap.name, 'joints': np.rad2deg(d['measured_joint_positions_rad']),
                     'bl': bl, 'br': br, 'corr': correction, 'rmse': (rl, rr)})
    ref = rows[0]
    print(f'reference: {ref["name"]}  (PnP RMSE L {ref["rmse"][0]:.2f} / R {ref["rmse"][1]:.2f} px)')
    print('                              joint change max   | board seen by LEFT cam | '
          'board seen by RIGHT cam | stereo correction')
    for row in rows[1:]:
        dj = np.abs(row['joints'] - ref['joints']).max(axis=1)
        lm, la = diff(ref['bl'], row['bl'])
        rm, ra = diff(ref['br'], row['br'])
        cm, ca = diff(ref['corr'], row['corr'])
        print(f'{row["name"][-29:]}: L {dj[0]:4.2f} R {dj[1]:4.2f} deg | '
              f'{lm:5.1f} mm {la:4.2f} deg      | {rm:5.1f} mm {ra:4.2f} deg       | '
              f'{cm:5.1f} mm {ca:4.2f} deg')
    print('\nRead: same capture repeated (no motion) shows the noise floor (~1 mm, ~0.1 deg).\n'
          'After a return, only one camera changing -> that camera/arm moved physically\n'
          '(mount or backlash). Both change the same way -> the board moved.')


if __name__ == '__main__':
    main()
