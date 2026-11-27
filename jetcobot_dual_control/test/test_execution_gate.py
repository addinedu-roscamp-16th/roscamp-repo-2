import math

import pytest
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint

from jetcobot_dual_control.coordinated_route import ordered_endpoint, ordered_points
from jetcobot_dual_control.execution_gate import validate_trial, validate_gripper


def request(side='left', target=None):
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'{side}_{i}_Joint' for i in range(1, 7)]
    goal.trajectory.points = [JointTrajectoryPoint(positions=[0.]*6),
                              JointTrajectoryPoint(positions=target or [math.radians(3)]+[0.]*5)]
    goal.trajectory.points[-1].time_from_start.sec = 2
    return goal


def test_small_single_joint_goal_allowed():
    assert validate_trial(request(), 'left', [0.]*6)[0] == math.radians(3)


def test_wrong_arm_out_of_bounds_or_stale_plan_rejected():
    for goal, current in [(request('right'), [0.]*6),
                          (request(target=[math.radians(169)]+[0.]*5), [0.]*6),
                          (request(), [.03]+[0.]*5)]:
        with pytest.raises(ValueError):
            validate_trial(goal, 'left', current)


def test_left_j5_sdk_limit_differs_from_right():
    target = [0.] * 6
    target[4] = math.radians(161.44)
    left = request('left', target=target)
    right = request('right', target=target)
    left.trajectory.points[-1].time_from_start.sec = 60
    right.trajectory.points[-1].time_from_start.sec = 60
    with pytest.raises(ValueError, match='hardware limit'):
        validate_trial(left, 'left', [0.] * 6)
    assert validate_trial(right, 'right', [0.] * 6) == pytest.approx(target)


def test_multiple_joint_target_allowed_but_detours_still_rejected():
    target = [math.radians(v) for v in [20, -15, 10, 5, -8, 12]]
    goal = request(target=target)
    goal.trajectory.points[-1].time_from_start.sec = 10
    middle = JointTrajectoryPoint(positions=[value*.5 for value in target])
    middle.time_from_start.sec = 5
    goal.trajectory.points.insert(1, middle)
    assert validate_trial(goal, 'left', [0.]*6) == target
    goal.trajectory.points[1].positions[1] += .02
    with pytest.raises(ValueError, match='detours'):
        validate_trial(goal, 'left', [0.]*6)


def test_preview_detour_rejected():
    goal = request()
    middle = JointTrajectoryPoint(positions=[math.radians(1.5), .015, 0., 0., 0., 0.])
    middle.time_from_start.sec = 1
    goal.trajectory.points.insert(1, middle)
    with pytest.raises(ValueError, match='detours'):
        validate_trial(goal, 'left', [0.]*6)


def test_path_mode_accepts_bounded_detour_but_keeps_velocity_and_limits():
    goal = request()
    middle = JointTrajectoryPoint(positions=[math.radians(1.5), .015, 0., 0., 0., 0.])
    middle.time_from_start.sec = 1
    goal.trajectory.points.insert(1, middle)
    assert validate_trial(goal, 'left', [0.]*6, allow_path=True)[0] == math.radians(3)
    goal.trajectory.points[1].positions[1] = .2
    with pytest.raises(ValueError, match='velocity'):
        validate_trial(goal, 'left', [0.]*6, allow_path=True)


def test_home_corrects_multiple_offsets_and_keeps_hardware_bounds():
    current = [math.radians(v) for v in [3, -.8, .6, -.3, .7, .4]]
    goal = request(target=[0.]*6)
    goal.trajectory.points[0].positions = current
    assert validate_trial(goal, 'left', current) == [0.]*6
    # Permit planner endpoint tolerance around zero, rather than requiring bitwise zero.
    goal.trajectory.points[-1].positions = [math.radians(.05)]*6
    assert validate_trial(goal, 'left', current)
    current[0] = math.radians(6)
    goal.trajectory.points[0].positions = current
    assert validate_trial(goal, 'left', current)
    current[0] = math.radians(169)
    goal.trajectory.points[0].positions = current
    with pytest.raises(ValueError, match='hardware limit'):
        validate_trial(goal, 'left', current)


def test_notebook_allows_over_five_degrees_without_relaxing_other_guards():
    goal = request(target=[math.radians(30)]+[0.]*5)
    goal.trajectory.points[-1].time_from_start.sec = 12
    assert validate_trial(goal, 'left', [0.]*6)[0] == math.radians(30)
    goal.trajectory.points[-1].positions[0] = math.radians(169)
    with pytest.raises(ValueError, match='hardware limit'):
        validate_trial(goal, 'left', [0.]*6)


def gripper_request(side='left', target=-.83):
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'{side}_gripper_controller']
    goal.trajectory.points = [JointTrajectoryPoint(positions=[.535]),
                              JointTrajectoryPoint(positions=[target])]
    goal.trajectory.points[-1].time_from_start.sec = 60
    return goal


def test_gripper_open_close_and_wrong_side_bounds():
    assert validate_gripper(gripper_request(), 'left') == -.83
    assert validate_gripper(gripper_request(target=.535), 'left') == .535
    for goal in (gripper_request('right'), gripper_request(target=.6), gripper_request(target=float('nan'))):
        with pytest.raises(ValueError):
            validate_gripper(goal, 'left')


def test_gripper_reversal_and_malformed_points_rejected():
    goal = gripper_request()
    middle = JointTrajectoryPoint(positions=[.5351])
    middle.time_from_start.sec = 1
    goal.trajectory.points.insert(1, middle)
    with pytest.raises(ValueError):
        validate_gripper(goal, 'left')
    goal = gripper_request()
    goal.trajectory.points[0].positions = [0., 0.]
    with pytest.raises(ValueError):
        validate_gripper(goal, 'left')


def test_pi_default_gripper_query_omits_type_argument(monkeypatch):
    import importlib.util
    from pathlib import Path
    import sys
    import types
    parent = types.ModuleType('pymycobot')
    child = types.ModuleType('pymycobot.mycobot280')
    child.MyCobot280 = object
    monkeypatch.setitem(sys.modules, 'pymycobot', parent)
    monkeypatch.setitem(sys.modules, 'pymycobot.mycobot280', child)
    path = Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge/scripts/read_joint_states.py'
    spec = importlib.util.spec_from_file_location('test_gripper_reader', path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    calls = []
    sdk = types.SimpleNamespace(get_gripper_value=lambda *args: calls.append(args) or (181 if args else 0))
    stub = types.SimpleNamespace(mc=sdk, gripper_type=0)
    assert reader.ReadOnlyState.read_gripper_value(stub) == 0
    assert calls[-1] == ()
    stub.gripper_type = 3
    assert reader.ReadOnlyState.read_gripper_value(stub) == 181
    assert calls[-1] == (3,)


def test_gripper_short_state_gap_does_not_relax_arm_or_unbounded_outage():
    import time
    from types import SimpleNamespace
    from jetcobot_dual_control.execution_gate import ExecutionGate
    q = {f'{side}_{i}_Joint': 0. for side in ('left', 'right') for i in range(1, 7)}
    stamp = 100_000_000_000
    clock = SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=102_000_000_000))
    stub = SimpleNamespace(latest=(time.monotonic(), stamp, q), get_clock=lambda: clock)
    with pytest.raises(ValueError):
        ExecutionGate.positions(stub, 'left')
    assert ExecutionGate.positions(stub, 'left', max_age=3.) == ([0.]*6, [0.]*6)
    clock.now = lambda: SimpleNamespace(nanoseconds=104_000_000_000)
    with pytest.raises(ValueError):
        ExecutionGate.positions(stub, 'left', max_age=3.)



def test_endpoint_has_one_untimed_target_and_holds_measured_peer():
    goal = request(target=[0.] * 5 + [math.radians(90.7)])
    goal.trajectory.points[-1].time_from_start.sec = 5
    measured = [0.] * 6 + [0.02] * 6
    with pytest.raises(ValueError, match='0.08 rad/s'):
        ordered_points(goal.trajectory, measured)

    goal.trajectory.points = [JointTrajectoryPoint(positions=[0.] * 5 + [math.radians(90.7)])]
    assert ordered_endpoint(goal.trajectory, measured) == (
        *(0.,) * 5, math.radians(90.7), *measured[6:])
    goal.trajectory.points[0].positions[-1] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        ordered_endpoint(goal.trajectory, measured)


def test_dual_endpoint_requires_complete_joints_and_one_target_point():
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'{side}_{i}_Joint' for side in ('right', 'left')
                                   for i in range(1, 7)]
    goal.trajectory.points = [JointTrajectoryPoint(positions=[.1] * 6 + [.2] * 6)]
    assert ordered_endpoint(goal.trajectory, [0.] * 12) == (*([.2] * 6), *([.1] * 6))
    goal.trajectory.points.append(JointTrajectoryPoint(positions=[0.] * 12))
    with pytest.raises(ValueError, match='one target point'):
        ordered_endpoint(goal.trajectory, [0.] * 12)

    goal.trajectory.points.pop()
    goal.trajectory.joint_names[-1] = goal.trajectory.joint_names[0]
    with pytest.raises(ValueError, match='complete arm'):
        ordered_endpoint(goal.trajectory, [0.] * 12)


# ---- 2026-11-25 per-arm lanes ----------------------------------------------
import threading
import time
from types import SimpleNamespace

from jetcobot_dual_control.execution_gate import ExecutionGate


def lane_gate():
    gate = ExecutionGate.__new__(ExecutionGate)
    gate.guard = threading.Lock()
    gate.cv = threading.Condition(gate.guard)
    gate.active = False
    gate.lanes = {'left': None, 'right': None}
    gate.exclusive_waiting = 0
    gate.faulted = False
    gate.get_logger = lambda: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None)
    return gate


def handle():
    return SimpleNamespace(is_cancel_requested=False)


def test_two_arm_lanes_run_together_and_exclusive_waits_for_both():
    gate = lane_gate()
    gate._acquire(handle(), 'left')
    gate._acquire(handle(), 'right')          # does not wait for the left lane
    order = []
    worker = threading.Thread(target=lambda: (gate._acquire(handle()), order.append('both')))
    worker.start()
    time.sleep(.3)
    assert order == []                         # stage 2 waits while an arm moves
    gate._release('left')
    time.sleep(.3)
    assert order == []
    gate._release('right')
    worker.join(2)
    assert order == ['both'] and gate.active
    gate._release()


def test_lane_waits_while_exclusive_runs_or_waits():
    gate = lane_gate()
    gate._acquire(handle())
    started = []
    worker = threading.Thread(target=lambda: (gate._acquire(handle(), 'right'), started.append(1)))
    worker.start()
    time.sleep(.3)
    assert started == []
    gate._release()
    worker.join(2)
    assert started == [1]


def test_waiting_goal_stops_on_fault_without_motion():
    gate = lane_gate()
    gate._acquire(handle(), 'left')
    errors = []

    def wait_left():
        try:
            gate._acquire(handle(), 'left')
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=wait_left)
    worker.start()
    time.sleep(.2)
    gate.faulted = True
    worker.join(2)
    assert errors and 'no motion sent' in errors[0]


def test_arm_goal_checks_every_pair_with_the_other_arms_remaining_path():
    gate = lane_gate()
    seen = []

    def valid_many(states, cache):
        seen.extend(states)
        return all(abs(q[0]) + abs(q[6]) < math.radians(50) for q in states)
    gate._combined_valid_many = valid_many
    gate._combined_valid = lambda q: True
    measured = (0.,) * 12
    gate.lanes['right'] = {'target': (math.radians(30),) + (0.,) * 5}
    ok, peer_moving = gate._arm_goal_path_valid('left', measured, (math.radians(10),) + (0.,) * 5)
    assert ok and peer_moving
    # left 0..10 deg x right 0..30 deg at 1 deg: every combination was sampled.
    assert len({(round(math.degrees(q[0])), round(math.degrees(q[6]))) for q in seen}) == 11 * 31
    ok, _ = gate._arm_goal_path_valid('left', measured, (math.radians(30),) + (0.,) * 5)
    assert not ok                              # left 30 + right 30 collides mid-way


def test_goal_accepted_during_joint_state_gap_but_bad_goal_still_rejected():
    from rclpy.action import GoalResponse
    gate = lane_gate()
    gate.latest = None                         # Pi->PC joint states missing
    ready = SimpleNamespace(server_is_ready=lambda: True)
    gate.sdk_clients = {(side, 'arm'): ready for side in ('left', 'right')}
    gate.drag_hybrid_clients = {'left': ready, 'right': ready}
    logs = []
    gate.get_logger = lambda: SimpleNamespace(warning=logs.append, info=logs.append)
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'left_{i}_Joint' for i in range(1, 7)]
    goal.trajectory.points = [JointTrajectoryPoint(positions=[0.] * 6)]
    assert gate.goal_combined(goal, endpoint_only=True) == GoalResponse.ACCEPT
    assert any('joint-state gap' in str(line) for line in logs)
    gate.faulted = True
    assert gate.goal_combined(goal, endpoint_only=True) == GoalResponse.REJECT
