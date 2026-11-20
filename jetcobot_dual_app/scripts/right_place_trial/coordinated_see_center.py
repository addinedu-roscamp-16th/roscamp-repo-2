#!/usr/bin/env python3
"""Return from the case with the left arm yielding before the right passes.

Default is plan only. With --execute, plan each phase again from fresh measured
joint states and send it to the coordinated gate. Grippers are never commanded.
"""
import argparse
import math
import time
import xml.etree.ElementTree as ET

from rcl_interfaces.srv import GetParameters

from coordinated_right_case import (ALL_JOINTS, require_coordinated_gate,
                                    plan_phase, execute_phases, joint_travel,
                                    simple_enough)
from paired_progress_route import (sample_arm, first_safe_overlap, make_phase,
                                   PlanningMargin)
from dual_arm_coordinator_real import DualArmCoordinator
import held_object  # noqa: E402  (path added by coordinated_right_case)
from place_right_in_case import URDF


def see_center_goals(coord):
    client = coord.node.create_client(
        GetParameters, '/jetcobot_dual/move_group/get_parameters')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt semantic model unavailable')
    response = coord.wait(client.call_async(GetParameters.Request(
        names=['robot_description_semantic'])), 8.0)
    root = ET.fromstring(response.values[0].string_value)
    goals = {}
    for side in ('left', 'right'):
        rows = [row for row in root.findall('group_state')
                if row.get('name') == 'see_center' and
                row.get('group') == f'arm_{side}']
        if len(rows) != 1:
            raise RuntimeError(f'Missing unique {side} see_center state')
        goals[side] = {joint.get('name'): float(joint.get('value'))
                       for joint in rows[0].findall('joint')}
        if len(goals[side]) != 6:
            raise RuntimeError(f'Incomplete {side} see_center state')
    return goals


def plan_simple(coord, state, side, goal, label, tries=5, shortest=False):
    """Plan one arm with the other held; use the first simple RRT result.

    Winding results (>1.6x direct joint travel) are kept aside; if none is
    simple, the least winding one is used when it is <= 2.5x (same policy as
    the case arrival, 2026-11-19).
    """
    from coordinated_right_case import travel_ratio
    options, winding = [], []
    for _ in range(tries):
        phase = plan_phase(coord, state, side, goal, label)
        if phase is None:
            continue
        if simple_enough(phase):
            options.append(phase)
            if not shortest or len(options) >= 3:
                break
        else:
            winding.append(phase)
    if not options and winding:
        best = min(winding, key=travel_ratio)
        if travel_ratio(best) <= 2.5:
            print(f'{label}: no simple path; using least winding '
                  f'({travel_ratio(best):.2f}x direct).', flush=True)
            options = [best]
    if not options:
        return None
    best = min(options, key=joint_travel)
    print(f'{label}: shortest of {len(options)} path(s) '
          f'({math.degrees(joint_travel(best)):.0f} deg joint travel).',
          flush=True)
    return best


def choose_return(coord, state, goals, arm='right'):
    """Reverse of the case arrival: both arms start together, the peer finishes.

    `arm` is the arm that placed (it is at the case); the peer has yielded.
    The placing arm's path is planned with the peer held where it is; the
    peer's path is planned with the placing arm at see_center. No 12-joint
    RRT, no extra yield and no home fallback.
    """
    from coordinated_right_case import other, paired_validity
    peer = other(arm)
    peer_names, arm_names = tuple(goals[peer]), tuple(goals[arm])
    peer_error = max(abs(state[j]-goals[peer][j]) for j in peer_names)
    arm_phase = plan_simple(coord, state, arm, goals[arm],
                            f'{arm.upper()} SEE_CENTER', shortest=True)  # shortest of up to 3
    if arm_phase is None:
        raise RuntimeError(f'{arm.upper()} SEE_CENTER: no simple path with {peer} arm held')
    if peer_error <= math.radians(1.0):
        return f'{arm} only', [arm_phase]
    peer_phase = plan_simple(coord, arm_phase['end'], peer, goals[peer],
                             f'{peer.upper()} SEE_CENTER')
    if peer_phase is None:
        raise RuntimeError(f'{peer.upper()} SEE_CENTER: no simple path with {arm} at see_center')
    # Time-reversed problem = case arrival: peer "yields" from see_center
    # while the placing arm goes see_center -> current. Reverse the result.
    peer_points = sample_arm(peer_phase, peer_names)[::-1]
    arm_points = sample_arm(arm_phase, arm_names)[::-1]
    order = peer_names + arm_names
    overlap = first_safe_overlap(peer_points, arm_points,
                                 paired_validity(coord, state, order))
    print(f'PAIRED RETURN: {peer} moves {len(peer_points)-1-overlap}/{len(peer_points)-1} '
          f'steps together with {arm}, then finishes alone.', flush=True)
    phase = make_phase(coord, state, peer_phase, arm_phase, overlap,
                       peer_points, arm_points, order, reverse=True,
                       label='PAIRED SEE_CENTER RETURN')
    coord.check_trajectory(phase['result'].trajectory.joint_trajectory,
                           state, group_name='both_arms', geometric_only=True)
    return 'paired', [phase]


def display_return(coord, phases):
    from moveit_msgs.msg import DisplayTrajectory
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    publisher = coord.node.create_publisher(
        DisplayTrajectory, '/jetcobot_dual/display_planned_path', qos)
    message = DisplayTrajectory()
    message.model_id = 'jetcobot_dual'
    message.trajectory_start = phases[0]['result'].trajectory_start
    message.trajectory = [phase['result'].trajectory for phase in phases]
    publisher.publish(message)
    until = time.monotonic()+0.5
    while time.monotonic() < until:
        coord.spin()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--arm', choices=('left', 'right'), default='right',
                        help='Arm that is at the case (it returns first; the other finishes)')
    parser.add_argument('--planning-extra-mm', type=float, default=15.0,
                        help='Extra gripper/camera padding used only while planning (execution keeps the launch value)')
    args = parser.parse_args()
    coord = DualArmCoordinator(URDF, velocity=.10, acceleration=.10,
                               plan_time=8.0, max_plans=6, planning_budget=60.0)
    try:
        state = coord.ready()
        require_coordinated_gate(coord)
        goals = see_center_goals(coord)
        wait = lambda f, t: coord.wait(f, t)
        for side in ('left', 'right'):
            if held_object.held_type(coord.node, wait, side):
                state[f'{side}_gripper_controller'] = held_object.planning_grip_value(
                    coord.node, wait, side)
        try:
            with PlanningMargin(coord, args.planning_extra_mm/1000.0):
                strategy, phases = choose_return(coord, state, goals, args.arm)
        except RuntimeError as exc:
            # After a place the arms can start inside the enlarged planning
            # margin although the real (execution) padding is clear.
            if 'measured combined state collides' not in str(exc) or args.planning_extra_mm == 0:
                raise
            print('RETURN: start pose is inside the enlarged planning margin; '
                  'replanning with execution padding only.', flush=True)
            with PlanningMargin(coord, 0.0):
                strategy, phases = choose_return(coord, state, goals, args.arm)
        print(f'RETURN PLAN: left={strategy}; '
              + ' -> '.join(phase['label'] for phase in phases), flush=True)
        display_return(coord, phases)
        if not args.execute:
            print('RETURN PLAN ONLY COMPLETE; no arm or gripper command sent.',
                  flush=True)
            return 0
        execute_phases(coord, phases)
        measured = coord.state()
        for side in ('left', 'right'):
            error = max(abs(math.degrees(measured[joint]-goal))
                        for joint, goal in goals[side].items())
            if error > 5.0:
                raise RuntimeError(f'{side} stopped {error:.1f} deg from see_center')
        print('BOTH SEE_CENTER COMPLETE; grippers unchanged.', flush=True)
        return 0
    finally:
        coord.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('INTERRUPTED; inspect both robots before retrying.', flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR {type(exc).__name__}: {exc}', flush=True)
        raise SystemExit(1)
