"""주문판에서 Pode 배정까지: 실제 교통 층과 주문 코어를 FleetManager로 묶어 데이터가 오가는지 본다."""
import json

from fleet_core.common.commands import AssignmentView, OrderRecord
from fleet_core.common.params import load_params
from fleet_core.fleet_map import load_fleet_map
from fleet_core.manager import FleetManager
from test_manager import finish, report

PODES = {"r1": "P-1", "r2": "P-3"}


def start(robots=PODES):
    lines = []
    core = FleetManager(load_fleet_map("d2"), load_params(), list(robots), lambda: 0.0)
    core.record_to(lines.append)
    for robot, node in robots.items():
        report(core, robot, node)
    return core, lines


def run_until(core, done, limit=300):
    for _ in range(limit):
        finish(core, tuple(core.traffic.robots), core.tick())
        if done():
            finish(core, tuple(core.traffic.robots), core.tick())  # 묶은 알림은 다음 tick에 나온다
            return
    raise AssertionError("no pode was bound")


def bound(core):
    return {r: core.orchestrator.order_of(r) for r in core.traffic.robots
            if core.orchestrator.order_of(r)}


def test_an_order_on_the_board_is_bound_to_a_pode_through_the_traffic_layer():
    core, lines = start()
    core.on_order_board("RUN", [OrderRecord("T-1", {"A": 1})])
    run_until(core, lambda: bound(core))
    (pode, order), = bound(core).items()
    assert order == "T-1"
    rows = [json.loads(x) for x in lines[1:]]
    kinds = [r.get("in") or r.get("inside") or r.get("out") for r in rows]
    board = kinds.index("on_order_board")
    call = kinds.index("DispatchCmd", board)
    answer = kinds.index("DispatchResult", call)
    bound_at = next(i for i, r in enumerate(rows) if i > answer and r.get("out") == "EventOut"
                    and r["code"] == "ORDER_BOUND")
    trip = kinds.index("ReportTripCmd", bound_at)
    assert rows[call]["station_id"] == "WORK" and rows[call]["robot_id"] == ""
    assert rows[answer]["success"] is True and rows[answer]["robot_id"] == pode
    assert rows[bound_at]["detail"] == "T-1" and rows[bound_at]["robot"] == pode
    assert rows[trip]["status"] == "ASSIGNED" and rows[trip]["robot"] == pode
    assert rows[trip]["trip_id"] == "T-1"
    assert AssignmentView(pode, "T-1", "WORK", "FILL") in core.assignments_view()
    for r in (rows[board], rows[call], rows[answer], rows[bound_at], rows[trip]):
        print(json.dumps(r, ensure_ascii=False))


def test_with_two_orders_the_first_pode_takes_the_first_order_and_the_other_waits_in_queue():
    core, lines = start()
    core.on_order_board("RUN", [OrderRecord("T-1", {"A": 1}), OrderRecord("T-2", {"B": 1})])
    run_until(core, lambda: bound(core))
    for _ in range(20):
        finish(core, tuple(core.traffic.robots), core.tick())
    # 채우기가 아직 없어 묶인 Pode가 작업대를 붙잡으니, 다른 Pode는 대기 자리에서 기다린다
    assert bound(core) == {"r1": "T-1"}
    rows = [json.loads(x) for x in lines[1:]]
    assert any(r.get("out") == "EventOut" and r["code"] == "DOCK_QUEUED" and r["robot"] == "r2"
               for r in rows)


def test_an_order_over_capacity_is_warned_and_the_other_is_still_bound():
    core, lines = start({"r1": "P-1"})
    core.on_order_board("RUN", [OrderRecord("T-9", {"A": 5}), OrderRecord("T-1", {"A": 1})])
    run_until(core, lambda: bound(core))
    assert bound(core) == {"r1": "T-1"}
    rows = [json.loads(x) for x in lines[1:]]
    assert [r["code"] for r in rows if r.get("out") == "EventOut"
            and r["code"] == "ORDER_OVER_CAPACITY"] == ["ORDER_OVER_CAPACITY"]
