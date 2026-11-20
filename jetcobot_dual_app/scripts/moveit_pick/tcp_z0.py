#!/usr/bin/env python3
"""Plan the selected arm TCP at base Z=0 with local +Z pointing straight down.

Uses the current TCP X/Y. Planning is the default; --execute sends the plan
through the existing sdk_drag_hybrid MoveIt gate.
"""
import argparse
import math
from pathlib import Path

import numpy as np
from rclpy.time import Time

from dual_arm_coordinator_real import DualArmCoordinator
from move_right_sam_yaw import closing_axis_to_tcp_orientation

DEFAULT_URDF = Path.home() / 'jetcobot_moveit2_real_upload/test_urdf/urdf/jetcobot_dual_real.urdf'


def current_tcp(coordinator, arm):
    for _ in range(200):
        coordinator.spin()
        if coordinator.buffer.can_transform('right_base_link', f'{arm}_TCP', Time()):
            return coordinator.buffer.lookup_transform('right_base_link', f'{arm}_TCP', Time()).transform
    raise RuntimeError(f'No current {arm}_TCP TF; start dual_real.launch.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('left', 'right'), required=True)
    parser.add_argument('--execute', action='store_true', help='execute after a successful plan')
    parser.add_argument('--urdf', type=Path, default=DEFAULT_URDF)
    args = parser.parse_args()
    coordinator = DualArmCoordinator(args.urdf, plan_time=3., max_plans=2, planning_budget=10.)
    try:
        current = coordinator.ready()
        tf = current_tcp(coordinator, args.arm)
        t, q = tf.translation, tf.rotation
        target = np.array([t.x, t.y, 0.0], dtype=float)
        # Preserve the current horizontal direction of TCP local X when tipping down.
        x_x = 1. - 2. * (q.y*q.y + q.z*q.z)
        x_y = 2. * (q.x*q.y + q.z*q.w)
        yaw = math.degrees(math.atan2(-x_y, -x_x)) if math.hypot(x_x, x_y) > .1 else 0.
        print(f'Current {args.arm}_TCP (right_base_link) mm: {[round(t.x*1000, 1), round(t.y*1000, 1), round(t.z*1000, 1)]}', flush=True)
        print(f'Goal {args.arm}_TCP mm: {np.round(target*1000, 1).tolist()}, local +Z straight down', flush=True)
        options = []
        for angle in (yaw, yaw + 180.):
            quat, _ = closing_axis_to_tcp_orientation(angle, 'x', 0.)
            plan = coordinator.plan_pose_goal(args.arm, current, target, quat,
                                              position_tolerance_m=.001,
                                              orientation_tolerance_deg=2.)
            if plan is None:
                continue
            trajectory = plan.trajectory.joint_trajectory
            if not trajectory.points:
                continue
            names = trajectory.joint_names
            prev = np.array([current[n] for n in names])
            cost = 0.
            for point in trajectory.points:
                values = np.array(point.positions)
                cost += float(np.sum(np.abs(values-prev)))
                prev = values
            options.append((cost, angle, plan))
        if not options:
            raise RuntimeError(f'No MoveIt plan for vertical {args.arm}_TCP at Z=0 and current X/Y')
        _, selected_yaw, plan = min(options, key=lambda item: item[0])
        chosen = {'phases': [{'side': args.arm, 'result': plan, 'start': current.copy(), 'route': 'auto'}],
                  'selected_arm': args.arm}
        print(f'Chosen vertical yaw: {selected_yaw:.1f} deg; trajectory points: {len(plan.trajectory.joint_trajectory.points)}', flush=True)
        coordinator.display(chosen)
        if args.execute:
            coordinator.execute(chosen)
            print(f'{args.arm.upper()} TCP Z=0 execution finished', flush=True)
        else:
            print('PLAN ONLY. Run again with --execute to replan from the live state and move.', flush=True)
    finally:
        coordinator.close()


if __name__ == '__main__':
    main()
