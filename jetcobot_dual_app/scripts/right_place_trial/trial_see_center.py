#!/usr/bin/env python3
"""Preview or execute a return to the calibrated stereo observation pose."""
import argparse
import json
import math
from pathlib import Path

from coordinated_right_case import execute_phases, plan_phase, require_coordinated_gate
from coordinated_see_center import display_return, see_center_goals
from dual_arm_coordinator_real import DualArmCoordinator, StopRequested
from place_right_in_case import URDF


DEFAULT_STEREO_REFERENCE = (Path(__file__).resolve().parents[1] /
                            'moveit_pick/calibration/stereo_see_center_correction_20261117.json')

TRIAL_JOINTS_DEG = {
    'left': (-109.16, 46.86, -66.81, -48.35, -6.70, -1.95),
    'right': (161.00, 47.86, -68.13, -48.39, -6.25, -1.91),
}


def stereo_observation_goal(path):
    record = json.loads(path.read_text())
    angles = record['reference_joint_positions_rad']
    tolerance_deg = float(record['max_joint_difference_deg'])
    if (record.get('frame') != 'right_base_link' or len(angles) != 2 or
            any(len(row) != 6 or not all(math.isfinite(q) for q in row) for row in angles)
            or not math.isfinite(tolerance_deg) or tolerance_deg <= 0):
        raise RuntimeError('Invalid stereo observation pose')
    joints = {f'{side}_{i}_Joint': q
              for side, row in zip(('left', 'right'), angles)
              for i, q in enumerate(row, 1)}
    return joints, tolerance_deg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true',
                        help='Send the checked path to the combined execution gate')
    observation = parser.add_mutually_exclusive_group()
    observation.add_argument('--stereo-reference', type=Path,
                             help='Use a different stereo calibration file for the observation target')
    observation.add_argument('--named-see-center', action='store_true',
                             help='Return to the see_center state in the running MoveIt model')
    observation.add_argument('--trial-pose', action='store_true',
                             help='Use the saved candidate joint angles instead of the calibrated pose')
    observation.add_argument('--left-observation-deg', type=float, nargs=6,
                             metavar='DEG',
                             help='Test left J1–J6 while holding the right arm at its measured pose')
    args = parser.parse_args()

    coord = DualArmCoordinator(URDF, velocity=.10, acceleration=.10,
                               plan_time=8.0, max_plans=2, planning_budget=30.0)
    try:
        state = coord.ready()
        if args.execute:
            require_coordinated_gate(coord)
        tolerance_deg = None
        if args.named_see_center:
            goal = {name: value for joints in see_center_goals(coord).values()
                    for name, value in joints.items()}
        elif args.trial_pose:
            goal = {f'{side}_{i}_Joint': math.radians(value)
                    for side, angles in TRIAL_JOINTS_DEG.items()
                    for i, value in enumerate(angles, 1)}
        elif args.left_observation_deg is not None:
            goal = state.copy()
            for i, degrees in enumerate(args.left_observation_deg, 1):
                name = f'left_{i}_Joint'
                angle = math.radians(degrees)
                lower, upper = coord.limits[name]
                if not lower <= angle <= upper:
                    raise ValueError(f'{name} is outside the configured limits ({degrees:g}°)')
                goal[name] = angle
        else:
            reference = args.stereo_reference or DEFAULT_STEREO_REFERENCE
            print(f'Stereo observation reference: {reference}', flush=True)
            goal, tolerance_deg = stereo_observation_goal(reference)
        for side in ('left', 'right'):
            angles = [round(math.degrees(goal[f'{side}_{i}_Joint']), 2) for i in range(1, 7)]
            print(f'{side.upper()} observation target (deg): {angles}', flush=True)
        phase = plan_phase(coord, state, 'both', goal, 'OBSERVATION RETURN')
        if phase is None:
            raise RuntimeError('No checked path to the trial see_center')
        display_return(coord, [phase])
        if not args.execute:
            print('TRIAL SEE_CENTER PLAN ONLY COMPLETE.', flush=True)
            return
        execute_phases(coord, [phase])
        if tolerance_deg is not None:
            measured = coord.state()
            error = max(abs(math.degrees(measured[name] - q)) for name, q in goal.items())
            if error > tolerance_deg:
                raise RuntimeError(f'Observation pose is {error:.2f} deg from the stereo reference '
                                   f'(limit {tolerance_deg:.2f} deg)')
        print('TRIAL SEE_CENTER COMPLETE.', flush=True)
    finally:
        coord.close()


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, StopRequested):
        raise SystemExit('Trial interrupted. Inspect the arm positions before retrying.')
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'Trial failed: {exc}')
