#!/usr/bin/env python3
"""Reach the case with the left arm held or yielded before the right passes.

Default is plan only. --execute closes the right gripper and replans later
phases from fresh measured joints through the combined gate.
There is no case insertion or gripper opening.
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

import sys
# Append (not insert): moveit_pick has same-named older modules
# (dual_arm_coordinator_real.py, move_right_sam_yaw.py) that must not shadow ours.
sys.path.append(str(Path(__file__).resolve().parents[1] / 'moveit_pick'))
import held_object  # noqa: E402
from dual_arm_coordinator_real import DualArmCoordinator
from move_right_sam_yaw import GRASP_CLOSED_TIP_OFFSET_MM, tcp_down_tilt_deg
from place_right_in_case import (
    CASE_CONFIG, URDF, RIGHT_ARRIVAL_JOINT_DEG, RIGHT_JOINTS,
    case_center_and_rim, check_case_scene, close_gripper, measured_tcp,
    right_see_center, wait_action,
)
from paired_progress_route import (sample_arm, first_safe_overlap, make_phase,
                                   PlanningMargin)

ALL_JOINTS = tuple(f'{side}_{index}_Joint'
                   for side in ('left', 'right') for index in range(1, 7))

_T0 = time.monotonic()
_T_LAST = [_T0]


def mark(label):
    """Timing line: time since the previous mark and since start (2026-11-19)."""
    now = time.monotonic()
    print(f'TIME {label}: {now-_T_LAST[0]:.1f} s (total {now-_T0:.1f} s)', flush=True)
    _T_LAST[0] = now


def save_checked_plan(folder, arm, held_type, case_xy, rim_z_mm, strategy, phases):
    """Persist only an already checked arrival route; no motion is sent."""
    from rclpy.serialization import serialize_message
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, phase in enumerate(phases, 1):
        name = f'phase_{index:02d}.cdr'
        (folder / name).write_bytes(serialize_message(phase['result']))
        rows.append({key: phase[key] for key in ('side', 'goal', 'label',
                                                  'start', 'end')} | {'file': name})

    def json_numpy(value):
        if isinstance(value, (np.ndarray, np.generic)):
            return value.tolist()
        raise TypeError(f'Cannot serialize {type(value).__name__}')

    manifest = {'arm': arm, 'held_type': held_type,
                'case_xy_m': case_xy.tolist(), 'rim_z_mm': rim_z_mm,
                'strategy': strategy, 'phases': rows}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2,
                                                    default=json_numpy))


def load_checked_plan(folder, coord, state, arm, held_type, case_xy, rim_z_mm):
    """Recheck a saved route against fresh measured joints and the live scene."""
    from rclpy.serialization import deserialize_message
    from moveit_msgs.msg import MotionPlanResponse
    manifest = json.loads((folder / 'manifest.json').read_text())
    if (manifest['arm'] != arm or manifest['held_type'] != held_type or
            np.linalg.norm(np.asarray(manifest['case_xy_m']) - case_xy) > 1e-6 or
            abs(manifest['rim_z_mm'] - rim_z_mm) > 1e-6):
        raise RuntimeError('Cached case plan target does not match this run')
    rows = manifest['phases']
    if not rows or not coord.valid(state, group_name='both_arms'):
        raise RuntimeError('Cached case plan is empty or current arms collide')
    phases = []
    projected = state.copy()
    for index, row in enumerate(rows):
        start = row['start']
        drift = max(abs(math.degrees(projected[name] - start[name]))
                    for name in ALL_JOINTS)
        if drift > 5.0:
            raise RuntimeError(f'Cached phase {index+1} start differs by {drift:.1f} deg')
        result = deserialize_message((folder / row['file']).read_bytes(),
                                     MotionPlanResponse)
        trajectory = result.trajectory.joint_trajectory
        if (set(trajectory.joint_names) != set(ALL_JOINTS) or
                not trajectory.points):
            raise RuntimeError('Cached route lacks a complete 12-joint path')
        if any(abs(float(value) - float(row['end'][name])) > 1e-5
               for name, value in zip(trajectory.joint_names,
                                      trajectory.points[-1].positions)):
            raise RuntimeError('Cached route endpoint does not match its manifest')
        if index == 0 and drift > 0:
            steps = max(1, math.ceil(math.radians(drift) / math.radians(1.0)))
            connection = []
            for step in range(steps+1):
                fraction = step / steps
                q = projected.copy()
                q.update({name: projected[name] + fraction * (start[name] - projected[name])
                          for name in ALL_JOINTS})
                connection.append(q)
            if not coord.valid_many(connection, group_name='both_arms'):
                raise RuntimeError('Measured-to-cached start connection collides')
        coord.check_trajectory(trajectory, projected, group_name='both_arms',
                               geometric_only=True)
        phase = {'side': row['side'], 'goal': row['goal'],
                 'label': row['label'], 'result': result,
                 'start': start, 'end': row['end']}
        phases.append(phase)
        projected = row['end'].copy()
    return manifest['strategy'], phases


def require_coordinated_gate(coord):
    from rcl_interfaces.srv import GetParameters
    client = coord.node.create_client(
        GetParameters, '/jetcobot_dual/selected_arm_execution_gate/get_parameters')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('Coordinated execution gate unavailable')
    response = coord.wait(client.call_async(GetParameters.Request(
        names=['execution_arm', 'trajectory_backend', 'coordinated_execution'])), 8.0)
    values = response.values
    if (len(values) != 3 or values[0].string_value != 'both' or
            values[1].string_value != 'sdk_drag_hybrid' or
            not values[2].bool_value):
        raise RuntimeError('Restart MoveIt with coordinated_execution:=true')


def plan_phase(coord, state, side, goal, label):
    """Plan one moving arm and validate it with the peer held in the scene."""
    if not coord.valid(state, group_name='both_arms'):
        raise RuntimeError(f'{label}: measured combined state collides in MoveIt scene')
    if not coord.valid(dict(state, **goal), group_name='both_arms'):
        print(f'{label}: target collides with held arm or case.', flush=True)
        return None
    attempts = 1 if side == 'both' else 3
    for attempt in range(1, attempts+1):
        print(f'{label}: plan attempt {attempt}/{attempts}', flush=True)
        started = time.monotonic()
        result = (plan_both(coord, state, goal) if side == 'both'
                  else coord.plan_joint_goal(side, state, goal))
        if result is None:
            print(f'{label}: planner returned no path in '
                  f'{time.monotonic()-started:.1f}s.', flush=True)
            continue
        trajectory = result.trajectory.joint_trajectory
        try:
            coord.check_trajectory(trajectory, state, group_name='both_arms',
                                   geometric_only=True)
        except RuntimeError as exc:
            if str(exc) != 'Planned trajectory collides after retiming':
                raise
            continue
        end = state.copy()
        end.update(zip(trajectory.joint_names, trajectory.points[-1].positions))
        print(f'{label}: checked path in {time.monotonic()-started:.1f}s.',
              flush=True)
        return {'side': side, 'goal': goal, 'label': label,
                'result': result, 'start': state.copy(), 'end': end}
    return None


def plan_both(coord, state, goal):
    """Request one joint-space plan in which both arms have distinct goals."""
    from moveit_msgs.msg import Constraints, JointConstraint
    from moveit_msgs.srv import GetMotionPlan
    request = GetMotionPlan.Request()
    motion = request.motion_plan_request
    motion.group_name = 'both_arms'
    motion.pipeline_id = 'ompl'
    motion.planner_id = 'RRTConnectkConfigDefault'
    motion.start_state = coord.robot_state(state)
    motion.num_planning_attempts = 2
    motion.allowed_planning_time = 8.0
    motion.max_velocity_scaling_factor = .10
    motion.max_acceleration_scaling_factor = .10
    motion.goal_constraints = [Constraints(joint_constraints=[JointConstraint(
        joint_name=name, position=float(goal[name]), tolerance_above=.004,
        tolerance_below=.004, weight=1.0) for name in ALL_JOINTS])]
    response = coord.wait(coord.planner.call_async(request), timeout=26.0).motion_plan_response
    if response.error_code.val != 1:
        return None
    if set(response.trajectory.joint_trajectory.joint_names) != set(ALL_JOINTS):
        raise RuntimeError('MoveIt returned incomplete dual-arm trajectory')
    return response


def execute_phases(coord, phases):
    """Use the freshly checked first plan; replan after later arm moves."""
    from control_msgs.action import FollowJointTrajectory
    from rclpy.action import ActionClient
    client = ActionClient(coord.node, FollowJointTrajectory,
        '/jetcobot_dual/guarded_both_controller/follow_joint_trajectory')
    for index, phase in enumerate(phases, 1):
        state = coord.state()
        side, goal, label = phase['side'], phase['goal'], phase['label']
        drift = max(abs(math.degrees(state[name]-phase['start'][name]))
                    for name in ALL_JOINTS)
        if index == 1 and drift <= 5.0 and coord.valid(state, group_name='both_arms'):
            print(f'PHASE {index}/{len(phases)}: use freshly checked {label} '
                  f'plan; start drift={drift:.1f} deg.', flush=True)
            current = phase
        else:
            print(f'PHASE {index}/{len(phases)}: replan {label} from measured arms.',
                  flush=True)
            current = plan_phase(coord, state, side, goal, label)
        if current is None:
            raise RuntimeError(f'{label}: no path from measured arms; '
                               'remaining phases were not sent')
        wait_action(coord, client, FollowJointTrajectory.Goal(
            trajectory=current['result'].trajectory.joint_trajectory),
            label, timeout=600.0)
        measured = coord.state()
        error = max(abs(math.degrees(measured[joint]-goal[joint])) for joint in goal)
        print(f'{label}: measured goal difference {error:.1f} deg.', flush=True)
        if error > 5.0:
            raise RuntimeError(f'{label}: measured goal differs by {error:.1f} deg; '
                               'remaining phases were not sent')


def check_arrival_geometry(coord, end, case_xy, rim_z_mm, arm='right'):
    pose = measured_tcp(coord, end, f'{arm}_TCP')
    xyz = np.array([pose.position.x, pose.position.y, pose.position.z])
    xy_error = float(np.linalg.norm(xyz[:2] - case_xy) * 1000.0)
    tip_above = (xyz[2] * 1000.0 - GRASP_CLOSED_TIP_OFFSET_MM[arm]
                 - rim_z_mm)
    q = pose.orientation
    down_tilt = tcp_down_tilt_deg([q.x, q.y, q.z, q.w])
    y_parallel = math.degrees(math.acos(min(1.0, abs(1.0-2.0*(q.x*q.x+q.z*q.z)))))
    print(f'COORDINATED GOAL: {arm} TCP mm={np.round(xyz*1000,1).tolist()}, '
          f'XY error={xy_error:.1f} mm, tips above rim={tip_above:.1f} mm, '
          f'down tilt={down_tilt:.1f} deg, Y parallel error={y_parallel:.1f} deg.',
          flush=True)
    # Hardware-scale limits (user: ~10 mm errors are normal).
    if (xy_error > 15.0 or not 20.0 <= tip_above <= 45.0 or
            down_tilt > 10.0 or y_parallel > 10.0):
        raise RuntimeError('Combined arrival geometry outside case range')


def joint_travel(phase):
    trajectory = phase['result'].trajectory.joint_trajectory
    previous = [phase['start'][name] for name in trajectory.joint_names]
    total = 0.0
    for point in trajectory.points:
        total += sum(abs(b-a) for a, b in zip(previous, point.positions))
        previous = point.positions
    return total


def travel_ratio(phase):
    trajectory = phase['result'].trajectory.joint_trajectory
    start = [phase['start'][name] for name in trajectory.joint_names]
    direct = sum(abs(b-a) for a, b in zip(start, trajectory.points[-1].positions))
    return joint_travel(phase) / max(direct, math.radians(1.0))


def simple_enough(phase):
    """Reject a winding 12-joint RRT path before it can reach the gate."""
    trajectory = phase['result'].trajectory.joint_trajectory
    names = trajectory.joint_names
    start = [phase['start'][name] for name in names]
    goal = list(trajectory.points[-1].positions)
    direct = sum(abs(b-a) for a, b in zip(start, goal))
    previous = start
    travel = 0.0
    for point in trajectory.points:
        current = point.positions
        travel += sum(abs(b-a) for a, b in zip(previous, current))
        previous = current
    ratio = travel / max(direct, math.radians(1.0))
    if ratio > 1.6:
        print(f'{phase["label"]}: winding route rejected '
              f'(joint travel {ratio:.2f}x direct); trying simpler sequence.',
              flush=True)
        return False
    return True


ARM_JOINTS = {side: tuple(f'{side}_{i}_Joint' for i in range(1, 7))
              for side in ('left', 'right')}
# Case-arrival joint goals (deg). Right: offline URDF estimate, validated on
# the robot 2026-11-19. Left: left_place_trial candidate (model only, not yet
# run on the robot).
ARRIVAL_JOINT_DEG = {'right': RIGHT_ARRIVAL_JOINT_DEG,
                     'left': (-133.56, -57.02, -0.05, -29.60, -0.66, -45.0)}


def other(arm):
    return 'left' if arm == 'right' else 'right'


def paired_validity(coord, state, order):
    """valid(q)/valid.many(rows) for tuples laid out as `order` joint names."""
    def valid(q):
        combined = state.copy()
        combined.update(zip(order, q))
        return coord.valid(combined, group_name='both_arms')

    def valid_many(rows):
        states = []
        for q in rows:
            combined = state.copy()
            combined.update(zip(order, q))
            states.append(combined)
        return coord.valid_many(states, group_name='both_arms')
    valid.many = valid_many
    return valid


# Yield candidates as (J1, J2, J3) deltas in rad; J1 sign "away" is the
# existing wide_yield direction (+ for left, - for right). wide_yield first.
YIELD_DELTAS = {
    'wide_yield':     (0.50, -0.30, 0.35),
    'rotate_away':    (0.50, 0.00, 0.00),
    'rotate_far':     (0.75, -0.15, 0.20),
    'rotate_mid':     (0.30, -0.30, 0.35),
    'lean_back':      (0.00, -0.40, 0.60),
    'lean_rotate':    (0.30, -0.50, 0.70),
    'rotate_toward':  (-0.40, -0.30, 0.35),
    'small_rotate':   (0.25, -0.15, 0.20),
}


def choose_faster_yield(coord, state, arm, peer, mover_phase, order):
    """Score yield poses by how long the placing arm must wait.

    Each candidate: straight joint move of the peer from its current pose.
    Rejected if the straight move collides with the placing arm still at its
    start, or if the final pose collides with the placing arm's path. Score =
    peer steps the placing arm must wait (first_safe_overlap), then peer
    travel. Coarse (every 3rd placing-arm sample) for speed; the chosen pose
    is re-planned and checked in full afterwards. All checks use the planning
    margin that is active in the caller.
    """
    direction = 1.0 if peer == 'left' else -1.0
    names = ARM_JOINTS[peer]
    start = tuple(state[n] for n in names)
    mover_points = sample_arm(mover_phase, ARM_JOINTS[arm])
    coarse_mover = mover_points[::3] + ([mover_points[-1]] if (len(mover_points)-1) % 3 else [])
    mover_start = mover_points[0]
    valid = paired_validity(coord, state, order)
    results = []
    for name, (d1, d2, d3) in YIELD_DELTAS.items():
        goal = list(start)
        for index, delta in ((0, direction*d1), (1, d2), (2, d3)):
            lo, hi = coord.limits[names[index]]
            goal[index] = min(hi, max(lo, goal[index] + delta))
        steps = max(1, math.ceil(max(abs(a-b) for a, b in zip(start, goal)) / math.radians(5.0)))
        peer_points = [tuple(a + (b-a)*i/steps for a, b in zip(start, goal)) for i in range(steps+1)]
        if not valid.many([q + mover_start for q in peer_points]):
            results.append((name, None, 'yield move blocked'))
            continue
        try:
            wait = first_safe_overlap(peer_points, coarse_mover, valid)
        except ValueError:
            results.append((name, None, 'blocks the placing path'))
            continue
        travel = sum(abs(a-b) for a, b in zip(start, goal))
        results.append((name, (wait, travel, dict(zip(names, goal))),
                        f'wait {wait}/{steps} steps'))
    print('YIELD CANDIDATES: ' + '; '.join(f'{n}: {info}' for n, _, info in results), flush=True)
    scored = [(score[0], score[1], name, score[2]) for name, score, _ in results if score]
    if not scored:
        return None, None
    _, _, best_name, best_goal = min(scored)
    return best_name, best_goal


def checked_goal(coord, state, case_xy, rim_z_mm, allow_staged=False, arm='right'):
    peer = other(arm)
    mover_goal = {name: math.radians(angle) for name, angle in
                  zip(ARM_JOINTS[arm], ARRIVAL_JOINT_DEG[arm])}
    # The small-yield dual route entered the padded collision geometry of
    # left_2_Link and right_gripper_left1 on 2026-11-19. Do not retry it.
    candidates = [(name, goal) for name, goal in
                  coord.yield_candidates(peer, state)
                  if name == 'wide_yield']
    # Plan each arm in a stable held-peer scene, then use a progress grid to
    # overlap only their independently safe suffixes. No 12-DOF random RRT
    # route or home fallback is used for this case approach.
    for name, peer_goal in candidates:
        print(f'PAIRED PLAN: {arm} places, {peer}={name}', flush=True)
        projected = state.copy()
        # RRT is random: a winding result is replanned instead of ending the run.
        peer_phase = None
        for _ in range(3):
            peer_phase = plan_phase(coord, projected, peer, peer_goal,
                                    f'{peer.upper()} YIELD ({name})')
            if peer_phase is None or simple_enough(peer_phase):
                break
            peer_phase = None
        if peer_phase is None:
            continue
        projected = peer_phase['end']
        # Keep the shortest of several simple RRT results so the placing arm
        # does not take an unnecessarily wide swing.
        label = f'{arm.upper()} CASE ARRIVAL'
        options, winding = [], []
        for attempt in range(5):
            option = plan_phase(coord, projected, arm, mover_goal, label)
            if option is None:
                continue
            if simple_enough(option):
                options.append(option)
                # Planning is now ~1 s per try, so keep the shortest of up to
                # 3 simple paths: a 429-deg first path cost ~6 s of motion.
                if len(options) >= 3:
                    break
            else:
                winding.append(option)
        if not options and winding:
            # Rather than failing the pick, accept the least winding route if
            # it is not extreme (2.5x direct joint travel).
            best = min(winding, key=travel_ratio)
            if travel_ratio(best) <= 2.5:
                print(f'{label}: no simple path; using least winding '
                      f'({travel_ratio(best):.2f}x direct).', flush=True)
                options = [best]
        if not options:
            continue
        mover_phase = min(options, key=joint_travel)
        print(f'{label}: shortest of {len(options)} simple path(s) '
              f'({math.degrees(joint_travel(mover_phase)):.0f} deg joint travel).',
              flush=True)
        check_arrival_geometry(coord, mover_phase['end'], case_xy, rim_z_mm, arm)
        order = ARM_JOINTS[peer] + ARM_JOINTS[arm]
        fallback = (peer_phase, name)
        best_name, best_goal = choose_faster_yield(coord, state, arm, peer, mover_phase, order)
        if best_name and best_name != name:
            better = plan_phase(coord, state.copy(), peer, best_goal,
                                f'{peer.upper()} YIELD ({best_name})')
            if better is not None and simple_enough(better):
                print(f'YIELD: using {best_name} instead of {name}.', flush=True)
                peer_phase, name = better, best_name
            else:
                print(f'YIELD: {best_name} could not be planned simply; keeping {name}.', flush=True)
        mover_points = sample_arm(mover_phase, ARM_JOINTS[arm])
        try:
            peer_points = sample_arm(peer_phase, ARM_JOINTS[peer])
            overlap = first_safe_overlap(peer_points, mover_points,
                                         paired_validity(coord, state, order))
        except ValueError:
            if peer_phase is fallback[0]:
                raise
            print(f'YIELD: {name} failed the full check; back to {fallback[1]}.', flush=True)
            peer_phase, name = fallback
            peer_points = sample_arm(peer_phase, ARM_JOINTS[peer])
            overlap = first_safe_overlap(peer_points, mover_points,
                                         paired_validity(coord, state, order))
        simultaneous = len(peer_points)-1-overlap
        print(f'PAIRED PROGRESS: {peer} yield {overlap}/{len(peer_points)-1} steps '
              f'before {arm} starts; {simultaneous} {peer} steps can overlap.',
              flush=True)
        if simultaneous == 0 and not allow_staged:
            raise RuntimeError('No independently safe simultaneous portion; '
                               'use --allow-staged for the simple sequential route')
        phase = make_phase(coord, state, peer_phase, mover_phase, overlap,
                           peer_points, mover_points, order)
        coord.check_trajectory(phase['result'].trajectory.joint_trajectory,
                               state, group_name='both_arms', geometric_only=True)
        check_arrival_geometry(coord, phase['end'], case_xy, rim_z_mm, arm)
        return f'paired {name}', [phase]
    raise RuntimeError('No simple paired case route in the live MoveIt scene')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-config', type=Path, default=CASE_CONFIG)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--arm', choices=('left', 'right'), default='right',
                        help='Arm that carries the object to the case (the other yields)')
    parser.add_argument('--from-pick', action='store_true',
                        help='Called by the pick UI after lift: start from the lifted pose '
                             '(not see_center) with the gripper already closed on the object')
    parser.add_argument('--held-object', choices=('chocobar', 'mentos_pair'),
                        help='Attach this object to right_TCP before planning (stand-alone test '
                             'without the camera pick; the pick UI attaches it automatically)')
    parser.add_argument('--place', action='store_true',
                        help='With --execute: after arrival descend, open slightly, lift, return to see_center')
    parser.add_argument('--below-rim-mm', type=float, default=-13.0,
                        help='Closed fingertips this far below the case rim at release; '
                             'negative = above. Default -13: object bottom ~10 mm above the rim '
                             '(drop release, user 2026-11-19)')
    parser.add_argument('--release-gap-mm', type=float, default=None,
                        help='Model finger gap when releasing (default object width + 5 mm, max 45)')
    parser.add_argument('--lift-mm', type=float, default=30.0)
    parser.add_argument('--descent-method', choices=('moveit', 'sdk'), default='moveit',
                        help='moveit: straight path via gate (default); sdk: Pi MoveL with grasp-pose compensation')
    parser.add_argument('--planning-extra-mm', type=float, default=15.0,
                        help='Extra gripper/camera padding used only while planning (execution keeps the launch value)')
    parser.add_argument('--allow-staged', action='store_true',
                        help='Allow the paired planner to choose no overlap if simultaneous motion is unsafe')
    parser.add_argument('--save-plan', type=Path,
                        help='Save a checked case-arrival plan for later execution; plan only')
    parser.add_argument('--use-plan', type=Path,
                        help='Recheck a saved arrival plan against the current scene before executing')
    args = parser.parse_args()
    if args.place and not args.execute:
        parser.error('--place requires --execute')
    if args.save_plan and args.execute:
        parser.error('--save-plan is plan only')
    case_xy, rim_z_mm = case_center_and_rim(args.case_config)
    coord = DualArmCoordinator(URDF, velocity=.10, acceleration=.10,
                               plan_time=8.0, max_plans=4, planning_budget=40.0)
    try:
        state = coord.ready()
        require_coordinated_gate(coord)
        check_case_scene(coord, case_xy, args.case_config, allow_absent=True)
        arm = args.arm
        wait = lambda f, t: coord.wait(f, t)
        if not args.from_pick:
            from coordinated_see_center import see_center_goals
            reference = see_center_goals(coord)[arm]
            error = max(abs(math.degrees(state[name]-reference[name])) for name in reference)
            if error > 5.0:
                raise RuntimeError(f'{arm} arm must start at see_center; max error {error:.1f} deg')
        if args.held_object:
            held_object.apply(coord.node, wait, args.held_object, arm)
        own_held_type = held_object.held_type(coord.node, wait, arm)
        if not own_held_type:
            print(f'WARNING: no held object modeled on {arm}_TCP; planning without it.',
                  flush=True)
            if args.save_plan or args.use_plan:
                raise RuntimeError('Cached placement requires a modeled held object')
        if args.from_pick:
            print(f'FROM PICK: {arm} gripper already closed on the object; '
                  'starting from the lifted pose.', flush=True)
        elif args.execute:
            close_gripper(coord, arm)
            state = coord.state()
        else:
            print(f'PLAN ONLY assumes the {arm} gripper is closed for collision '
                  'checking; no gripper command sent.', flush=True)
        state[f'{arm}_gripper_controller'] = held_object.planning_grip_value(
            coord.node, wait, arm)
        mark('setup (gate/scene checks, gripper)')
        phases = None
        strategy = None
        if args.use_plan:
            try:
                strategy, phases = load_checked_plan(
                    args.use_plan, coord, state, arm, own_held_type, case_xy, rim_z_mm)
                print(f'PREPLANNED CASE: {arm} {len(phases)} phase(s) rechecked '
                      'against current scene.', flush=True)
            except Exception as exc:
                print(f'PREPLANNED CASE unusable: {exc}; planning from measured state.',
                      flush=True)
                phases = None
        if phases is None:
            with PlanningMargin(coord, args.planning_extra_mm/1000.0):
                strategy, phases = checked_goal(coord, state, case_xy, rim_z_mm,
                                                allow_staged=args.allow_staged, arm=arm)
        mark('plan case arrival (RRT + paired checks)')
        if args.save_plan:
            save_checked_plan(args.save_plan, arm, own_held_type, case_xy,
                              rim_z_mm, strategy, phases)
            print(f'PREPLANNED CASE SAVED: {args.save_plan}', flush=True)
        print(f'COORDINATED CASE PLAN: {arm} carries, {other(arm)}={strategy}; '
              + ' -> '.join(phase['label'] for phase in phases), flush=True)
        print('Both arms have one paired path. The gate independently checks '
              'its Pi endpoint/drag sections before sending any arm command.',
              flush=True)
        held = held_object.attached_messages(coord.node, lambda f, t: coord.wait(f, t))
        for phase in phases:  # show the held object on the RViz path preview
            phase['result'].trajectory_start.attached_collision_objects = held
        coord.display({'phases': [dict(side=phase['side'], result=phase['result'],
                                       start=phase['start'], route='coordinated')
                                  for phase in phases]})
        if not args.execute:
            print('COORDINATED PLAN ONLY COMPLETE; no arm or gripper command sent.', flush=True)
            return 0
        execute_phases(coord, phases)
        mark('execute case arrival (gate preflight + motion)')
        print(f'COORDINATED ARRIVAL COMPLETE; {arm} gripper remains closed.', flush=True)
        if args.place:
            import place_steps
            from coordinated_see_center import see_center_goals, choose_return
            place_steps.place(coord, case_xy, rim_z_mm, below_rim_mm=args.below_rim_mm,
                              release_gap_mm=args.release_gap_mm, up_mm=args.lift_mm,
                              descent_method=args.descent_method, arm=arm)
            mark('place (center, lower, release, lift)')
            state = coord.state()
            goals = see_center_goals(coord)
            try:
                with PlanningMargin(coord, args.planning_extra_mm/1000.0):
                    strategy, phases = choose_return(coord, state, goals, arm)
            except RuntimeError as exc:
                if 'measured combined state collides' not in str(exc):
                    raise
                print('RETURN: start pose is inside the enlarged planning margin; '
                      'replanning with execution padding only.', flush=True)
                with PlanningMargin(coord, 0.0):
                    strategy, phases = choose_return(coord, state, goals, arm)
            mark('plan return to see_center')
            print(f'RETURN PLAN: {strategy}', flush=True)
            execute_phases(coord, phases)
            mark('execute return to see_center')
            print('PLACE AND RETURN COMPLETE.', flush=True)
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
