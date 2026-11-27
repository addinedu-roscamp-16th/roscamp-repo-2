from fleet_core.common.commands import (
    TRIP_ASSIGNED,
    DispatchCmd,
    EventOut,
    OrderRecord,
    ReleaseCmd,
    ReportTripCmd,
)
from fleet_core.common.params import load_params
from fleet_core.orders.orchestrator import OrderOrchestrator
from stations import STATIONS

T1, T2 = OrderRecord("T-1", {"A": 1}), OrderRecord("T-2", {"B": 2})


def make(robots=("r1", "r2")):
    return OrderOrchestrator(STATIONS, load_params(), list(robots), lambda: 0.0)


def test_a_free_robot_reaching_work_is_bound_and_reported_assigned():
    """묶을 자격이 있는 로봇이 닿으면 기록에 묶이고 ORDER_BOUND와 ASSIGNED가 나가는지 지킨다."""
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    assert o.robot_view("r1") == {"stage": "FILLING", "basket": "EMPTY", "order": "T-1",
                                  "placed": {}}
    assert o.take_output() == [EventOut("INFO", "ORDER_BOUND", "T-1", "r1"),
                               ReportTripCmd("T-1", "r1", TRIP_ASSIGNED, {}, {})]


def test_a_robot_reaching_work_with_nothing_waiting_is_released():
    """묶을 주문이 사라졌는데 로봇을 작업대 구역에 붙잡아 두는 일을 막는다."""
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o._final = {"T-1"}  # 호출은 열린 채 주문만 대기에서 빠진 상태(주문판을 비우면 호출도 거둬져 이 분기에 못 옴)
    o.on_dispatch_result(1, True, "r1")
    assert ReleaseCmd("r1") in o.take_output()
    assert o.order_of("r1") == ""


def test_a_held_robot_is_neither_bound_nor_released():
    """held 로봇을 놓아 주거나 묶어서 붙잡아 둔 뜻이 사라지는 일을 막는다."""
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o._robots["r1"].held = "ARM_FAULT"
    o.on_dispatch_result(1, True, "r1")
    out = o.take_output()
    assert not any(isinstance(c, ReleaseCmd) for c in out)
    assert o.order_of("r1") == ""


def test_final_and_held_ids_not_on_the_new_board_are_forgotten():
    """주문판에서 빠진 id가 최종 보고 집합이나 보류 목록에 남아 쌓이는 일을 막는다."""
    o = make()
    o._final = {"T-1"}
    o._holds = {"T-2": ("r2", "ROBOT_FAULT"), "T-3": ("r1", "ARM_FAULT")}
    o.on_order_board("RUN", [T2])
    assert o._final == set()
    assert o._holds == {"T-2": ("r2", "ROBOT_FAULT")}


def test_final_or_held_orders_are_not_waiting():
    """이미 끝났거나 보류 중인 주문에 WORK 호출을 열어 로봇을 부르는 일을 막는다."""
    o = make()
    o._final = {"T-1"}
    o._holds = {"T-2": ("r2", "ROBOT_FAULT")}
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == []


def test_a_held_robots_order_leaving_the_board_clears_only_its_order():
    """held 로봇의 주문이 주문판에서 빠질 때 로봇까지 풀어 버리는 일을 막는다(설계 4.6)."""
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    o._robots["r1"].held = "ARM_FAULT"
    o.on_order_board("RUN", [])
    assert o.order_of("r1") == "" and o._robots["r1"].held == "ARM_FAULT"
    assert not any(isinstance(c, DispatchCmd) for c in o.take_output())
