"""FleetManager는 두 코어를 잇는다: 내부 호출은 안에 머물고 옛 출력은 그대로 나온다. 로봇 흉내 drive()는 받은 이동, 물러나기, 도킹을 곧바로 성공시킨다."""
from core_helpers import cmds
from fleet_core.common.commands import (
    CancelDispatchCmd,
    DispatchCmd,
    DispatchFeedback,
    DispatchResult,
    DockCmd,
    MoveCmd,
    MoveOrderCmd,
    OrderRecord,
    ReleaseCmd,
    RetreatCmd,
    RobotFaultCmd,
    RobotIsolated,
)
from fleet_core.common.params import load_params
from fleet_core.fleet_map import load_fleet_map
from fleet_core.manager import FleetManager
from fleet_core.common.commands import FILL
from fleet_core.common.commands import ArmFailedCmd
from fleet_core.common.commands import ArmFault
from fleet_core.common.commands import ArmFillCmd

INTERNAL = (DispatchCmd, CancelDispatchCmd, ReleaseCmd, RobotFaultCmd, DispatchFeedback,
            DispatchResult, RobotIsolated)
O1 = OrderRecord("O-001", {"A": 1})
O2 = OrderRecord("O-002", {"B": 1})
SITE = load_fleet_map("d2")


def make(nodes, orders=()):
    now = [0.0]
    core = FleetManager(SITE, load_params(), list(nodes), lambda: now[0])
    for robot, node in nodes.items():
        report(core, robot, node)
    core.on_order_board("RUN", list(orders))
    return core, now


def report_at(core, robot, x, y, state="IDLE"):
    for _ in range(core.params.release_confirm_samples):
        core.on_robot_state(robot, x, y, 0.0, state)


def report(core, robot, node, state="IDLE"):
    report_at(core, robot, *core.site.node_pose(node), state)


def finish(core, robots, out):
    """out 가운데 robots에 온 이동, 물러나기, 도킹을 곧바로 성공시킨다. order는 base 끝까지 가서 horizon이 남으면 update를 기다린다."""
    runs = core.__dict__.setdefault("_test_order_runs", {})  # 로봇 -> (goal id, 끝낸 seq)
    for c in out:
        robot = getattr(c, "robot", None)
        if robot not in robots:
            continue
        if isinstance(c, MoveOrderCmd):
            seq = 0
            if c.update_id and robot in runs:
                old, seq = runs[robot]
                core.on_move_result(robot, old, False, "SUPERSEDED", seq)
            if c.base:
                report_at(core, robot, c.base[-1].x, c.base[-1].y)
                seq = c.base[-1].seq_id
            runs[robot] = (c.goal_id, seq)
            if c.horizon:
                core.on_move_feedback(robot, c.goal_id, seq, True)
            else:
                del runs[robot]
                core.on_move_result(robot, c.goal_id, True, "", seq)
        elif isinstance(c, MoveCmd):
            report_at(core, robot, c.x, c.y)
            core.on_move_result(robot, c.goal_id, True, "")
        elif isinstance(c, RetreatCmd):
            x, y = c.path[-1][:2]
            report_at(core, robot, x, y)
            core.on_retreat_result(robot, c.goal_id, True, "")
        elif isinstance(c, DockCmd):
            core.on_dock_result(robot, c.goal_id, True)


def drive(core, robots, until, first=(), limit=300):
    """관제를 돌리며 robots(이름 하나 또는 여럿)에 온 일을 finish로 끝낸다. first는 이미
    받아 둔 출력이다. 다른 로봇은 선다."""
    robots = (robots,) if isinstance(robots, str) else tuple(robots)
    finish(core, robots, first)
    seen = []
    for _ in range(limit):
        out = core.tick()
        seen += out
        finish(core, robots, out)
        if until():
            return seen + core.tick()
    raise AssertionError(f"not reached: {[core.robot_view(r) for r in robots]}")


def test_inside_calls_never_reach_the_ros_side():
    core, _ = make({"r1": "P-1"})
    call(core, 1000)
    out = core.tick()
    out += drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING", first=out)
    assert out, "the walk produced commands"
    assert not [c for c in out if isinstance(c, INTERNAL)]


def test_the_view_shows_the_old_station_phases_not_the_traffic_one():
    core, _ = make({"r1": "P-1"})
    call(core, 1000)
    drive(core, "r1", lambda: core.traffic.robot_view("r1")["phase"] == "AT_STATION")
    assert core.robot_view("r1")["phase"] == "FILLING"
    assert [a.task_type for a in core.assignments_view()] == ["FILL"]


def test_goal_ids_of_moves_and_arm_jobs_share_one_sequence():
    core, _ = make({"r1": "P-1"})
    call(core, 1000)
    out = drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING")
    out += ask_fill(core)
    assert cmds(out, "FillCmd")
    ids = [c.goal_id for c in out
           if type(c).__name__ in ("MoveCmd", "MoveOrderCmd", "RetreatCmd", "DockCmd", "FillCmd")]
    assert ids == sorted(ids) and len(ids) == len(set(ids))


def test_a_refused_confirm_clear_keeps_the_order_on_its_robot():
    core, _ = make({"r1": "P-1"}, [O1])
    drive(core, "r1", lambda: core.robot_view("r1")["order"] == "O-001")
    assert core.confirm_clear("r1", ["NOPE"], "typo") is None
    assert core.confirm_clear("r1", [], "forgot the list") is None
    assert core.robot_view("r1")["order"] == "O-001"
    assert core.robot_view("r1")["state"] != "OUT_OF_SERVICE"


def _arm_held():
    """팔이 재시도 한도를 넘어 주문이 ARM_FAULT로 보류된 코어."""
    core, now = make({"r1": "P-1"}, [O1])
    out = drive(core, "r1", lambda: core.robot_view("r1")["order"] == "O-001")
    goal = cmds(out, "FillCmd")[0].goal_id
    seen = []
    for _ in range(core.params.move_retry_limit + 1):
        core.on_fill_result(goal, False, {}, {}, "GRIPPER")
        now[0] += core.params.arm_retry_wait_s
        report(core, "r1", "W", "DOCKED")
        out = core.tick()
        seen += out
        fills = cmds(out, "FillCmd")
        if fills:
            goal = fills[0].goal_id
    return core, now, seen


def _filled_and_leaving(robot="r1"):
    """실제 결과로 채워지고 나가는 중인 바구니. 화이트박스 없음."""
    core, now = make({robot: "P-1"}, [O1])
    out = drive(core, robot, lambda: core.robot_view(robot)["order"] == "O-001")
    core.on_fill_result(cmds(out, "FillCmd")[0].goal_id, True, {"A": 1}, {}, "")
    report(core, robot, "W", "DOCKED")
    leaving = core.tick()
    return core, now, leaving


def test_a_robot_at_a_station_waits_for_nobody():
    """스테이션에 선 로봇은 구역을 기다리지 않는다(대기 순환에 낄 수 없다)."""
    core, _ = make({"r1": "P-1", "r2": "P-2"})
    call(core, 1000)
    pair = ("r1", "r2")
    def docked():
        return [r for r in pair if core.traffic.robot_view(r)["phase"] == "AT_STATION"]
    drive(core, pair, docked)
    robot = docked()[0]
    assert (status(core, robot).wait_zone, status(core, robot).wait_robots) == ("", ())


# ---- /fleet/robots ----------------------------------------------------------------------


def status(core, robot):
    return {view.robot_id: view for view in core.robots_view()}[robot]


def test_the_status_says_what_the_pode_reported_and_where_it_is():
    core, _ = make({"r1": "P-1"})
    x, y = core.site.node_pose("P-1")
    core.on_robot_state("r1", x, y, 0.5, "WAITING", 87.5)
    view = status(core, "r1")
    assert (view.fsm_state, view.x, view.y, view.yaw, view.battery) == ("WAITING", x, y, 0.5, 87.5)
    assert (view.fault, view.fault_reason) == ("NONE", "")
    assert (view.wait_zone, view.wait_robots) == ("", ())


def test_a_robot_nobody_has_heard_from_still_has_a_status():
    core = FleetManager(SITE, load_params(), ["r1"], lambda: 0.0)
    view = status(core, "r1")
    assert (view.fsm_state, view.task_type, view.order_id, view.fault) == ("", "", "", "NONE")


def test_comm_loss_shows_in_the_status_and_goes_away():
    core, now = make({"r1": "P-1"})
    now[0] = core.params.comm_timeout_s + 0.1
    core.tick()
    assert (status(core, "r1").fault, status(core, "r1").fault_reason) == ("COMM_LOST", "NO_STATE")
    report(core, "r1", "P-1")
    assert status(core, "r1").fault == "RECOVERING"
    now[0] += core.params.recover_check_s + 0.1
    report(core, "r1", "P-1")
    core.tick()
    assert (status(core, "r1").fault, status(core, "r1").fault_reason) == ("NONE", "")


def test_an_error_shows_its_cause():
    core, _ = make({"r1": "P-1"})
    core.traffic.on_robot_fault("r1", "NAV_ABORTED")
    assert (status(core, "r1").fault, status(core, "r1").fault_reason) == ("ERROR", "NAV_ABORTED")


def test_a_cleared_robot_is_out_of_service_with_no_task():
    core, _ = make({"r1": "P-1"})
    core.confirm_clear("r1", core.zones.held_by("r1"), "lifted off")
    view = status(core, "r1")
    assert (view.fault, view.fault_reason, view.task_type) == ("OUT_OF_SERVICE", "lifted off", "")


def test_a_waiting_robot_shows_the_zone_and_who_is_in_the_way():
    """a가 작업 도크 앞목(X_W)에서 고장 나 섰고 불린 b는 J_W를 a 때문에 기다린다(도크에 선 로봇 뒤의 차례는 DOCK_QUEUED다)."""
    core, _ = make({"a": "X_W", "b": "P-2"})
    core.traffic.on_robot_fault("a", "NAV_ABORTED")
    core.on_order_board("RUN", [O1])
    core.tick()
    core.tick()
    view = status(core, "b")
    assert view.wait_zone == "J_W" and view.wait_robots == ("a",)
    assert status(core, "a").wait_zone == ""
    core.confirm_clear("a", core.zones.held_by("a"), "lifted off")
    core.tick()
    core.tick()
    assert (status(core, "b").wait_zone, status(core, "b").wait_robots) == ("", ())
    assert "J_W" in core.zones.held_by("b")


def test_sr17_every_robot_state_can_be_told_apart():
    """대기, 이동, 도킹, 담기 대기, 출고 대기, 오류 + 원인."""
    core, _ = make({"r1": "P-1", "r2": "P-3"})
    assert status(core, "r1").task_type == "WAIT"
    call(core, 1000)
    first = core.tick()
    assert status(core, "r1").task_type == "TO_WORK"
    drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING", first=first)
    assert status(core, "r1").task_type == "FILL"
    send_out(core, "r1", 1001)
    report(core, "r1", "W", "DOCKED")
    first = core.tick()
    assert status(core, "r1").task_type == "TO_OUTBOUND"
    drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "SWAPPING", first=first)
    assert status(core, "r1").task_type == "SWAP"
    x, y = core.site.node_pose("P-3")
    core.on_robot_state("r2", x, y, 0.0, "DOCKING")
    assert status(core, "r2").fsm_state == "DOCKING"
    core.traffic.on_robot_fault("r2", "DOCK_FAILED")
    assert (status(core, "r2").fault, status(core, "r2").fault_reason) == ("ERROR", "DOCK_FAILED")


# ---- 팔 상태와 담기 결과 칸이 매니저를 지난다 ----


def _filling():
    core, now = make({"r1": "P-1"}, [O1])
    out = drive(core, "r1", lambda: core.robot_view("r1")["order"] == "O-001")
    return core, now, cmds(out, "FillCmd")[0].goal_id


def test_an_arm_state_input_is_recorded():
    lines = []
    core = FleetManager(SITE, load_params(), ["r1"], lambda: 0.0)
    core.record_to(lines.append)
    core.on_arm_state("arm_left", "ERROR", "X", True)
    # 입력 줄 뒤에 작업대 막힘 같은 내부 줄이 이어질 수 있다
    assert any('"in": "on_arm_state"' in line for line in lines[1:])


def call(core, goal_id, station="", robot=""):
    """주문 쪽이 낼 배차를 FleetManager의 전달 길로 넣는다. 이름 없으면 WORK 열린 호출."""
    core._route(DispatchCmd(goal_id, station or core._work, robot))


def send_out(core, robot, goal_id):
    """WORK에 선 로봇을 놓아 주고 이름을 붙여 OUTBOUND로 부른다."""
    core._route(ReleaseCmd(robot))
    call(core, goal_id, core._outbound, robot)


def ask_fill(core, robot="r1"):
    core._route(ArmFillCmd(robot, core._work, "O-001", {"A": 1}))
    return core.tick()


def test_an_arm_past_its_retries_is_an_arm_fault_and_the_robot_stays_available():
    core, now = make({"r1": "P-1"})
    reports = []
    core.orchestrator.on_arm_report = reports.append
    call(core, 1000)
    drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING")
    goal = cmds(ask_fill(core), "FillCmd")[0].goal_id
    for _ in range(core.params.move_retry_limit + 1):
        core.on_fill_result(goal, False, {}, {}, "GRIPPER")
        core._route(ArmFailedCmd("r1"))
        now[0] += core.params.arm_retry_wait_s
        report(core, "r1", "W", "DOCKED")
        fills = cmds(ask_fill(core), "FillCmd")
        if fills:
            goal = fills[0].goal_id
    faults = [r for r in reports if isinstance(r, ArmFault)]
    assert [(f.robot, f.kind, f.reason) for f in faults] == [("r1", FILL, "workcell: GRIPPER")]
    assert core.robot_view("r1")["state"] != "ERROR"
    assert core.traffic.available("r1")


def test_force_ship_goes_to_the_orders_side_and_returns_its_answer():
    core, _ = make({"r1": "P-1"})
    assert core.force_ship("O-001") == (False, "NOT_IMPLEMENTED")
    refused = [e for e in cmds(core.tick(), "EventOut") if e.code == "INPUT_REJECTED"]
    assert len(refused) == 1 and "O-001" in refused[0].detail
    asked = []
    core.orchestrator.on_force_ship = lambda order_id: asked.append(order_id) or (True, "")
    assert core.force_ship("O-002") == (True, "") and asked == ["O-002"]
    assert not [e for e in cmds(core.tick(), "EventOut") if e.code == "INPUT_REJECTED"]


def test_recovery_on_the_way_out_still_heads_to_outbound():
    core, now = make({"r1": "P-1"})
    call(core, 1000)
    drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING")
    send_out(core, "r1", 1001)
    report(core, "r1", "W", "DOCKED")
    core.tick()
    now[0] += core.params.comm_timeout_s + 0.1
    core.tick()
    assert core.robot_view("r1")["state"] == "COMM_LOST"
    now[0] += 1.0
    report(core, "r1", "W")
    core.tick()
    now[0] += core.params.recover_check_s
    report(core, "r1", "W")
    core.tick()
    assert core.robot_view("r1")["state"] == "TO_OUTBOUND"
    assert core.robot_view("r1")["destination"] == "OUTBOUND"
