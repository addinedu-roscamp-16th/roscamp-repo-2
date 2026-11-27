#!/usr/bin/env python3
"""Stage 3 placement: pre_place -> descent joint goal -> open gripper -> pre_place.

The placing arm starts at pre_place (stage 2 entry done). In concurrent mode the
other arm keeps picking meanwhile; the gate runs both arms' goals per arm.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from coordinated_right_case import named_goals, require_coordinated_gate, write_json_atomic
from dual_arm_coordinator_real import DualArmCoordinator
from place_right_in_case import (
    CASE_CONFIG, URDF, case_arrival_pose, case_center_and_rim, check_case_scene,
    measured_tcp, wait_action,
)
from joint_goal import JOINT_GOAL_ACTION, endpoint_request, pose_joint_goal
import held_object
import place_steps


def use_arm(arm):
    place_steps.ARM = arm
    place_steps.ARM_JOINTS = tuple(f'{arm}_{i}_Joint' for i in range(1, 7))


def place_down(coord, state, arm, target):
    pose = measured_tcp(coord, state, f'{arm}_TCP')
    q = pose.orientation
    goal = pose_joint_goal(coord.node, coord.wait, state, arm, target, (q.x, q.y, q.z, q.w))
    if goal is None:
        raise RuntimeError(f'{arm} has no placement IK goal')
    return goal


def send_joint_goal(coord, goal, label):
    from control_msgs.action import FollowJointTrajectory

    action = held_object.cached_action(coord.node, FollowJointTrajectory, JOINT_GOAL_ACTION)
    wait_action(coord, action, endpoint_request(goal), label, timeout=150.0)


def place(coord, arm, checkpoint, case_xy, rim_z_mm):
    wait = lambda future, timeout: coord.wait(future, timeout)
    kind = held_object.held_type(coord.node, wait, arm)
    if not kind:
        raise RuntimeError('Placement arm has no modeled object')
    record = json.loads(checkpoint.read_text())

    def mark(stage):
        record['stage'] = stage
        write_json_atomic(checkpoint, record)

    state = coord.state()
    target = np.asarray(case_arrival_pose(case_xy, rim_z_mm, arm)[0], float)
    send_joint_goal(coord, place_down(coord, state, arm, target), 'PLACE DESCENT JOINT GOAL')

    use_arm(arm)
    place_steps.open_slightly(coord, {'mentos_pair': 37.0}.get(kind, 40.0))
    mark('gripper_released')
    held_object.apply(coord.node, wait, arm=arm, remove=True)
    mark('scene_detached')
    send_joint_goal(coord, named_goals(coord, 'pre_place')[arm], 'PLACE RISE TO PRE_PLACE')
    mark('at_pre_place_after_release')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--placing', choices=('left', 'right'), required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    case_xy, rim_z_mm = case_center_and_rim(CASE_CONFIG)
    coord = DualArmCoordinator(URDF, velocity=.10, acceleration=.10, plan_time=8.0)
    try:
        coord.ready()
        require_coordinated_gate(coord)
        check_case_scene(coord, case_xy, CASE_CONFIG, allow_absent=True)
        place(coord, args.placing, args.checkpoint, case_xy, rim_z_mm)
    finally:
        coord.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'STAGE3 PLACE STOP: {type(exc).__name__}: {exc}')
