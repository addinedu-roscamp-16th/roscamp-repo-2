"""주문 처리 자리의 약속: FleetManager가 기대는 이름과 조회 모양, 그리고 아무 출력도 없음."""
from fleet_core.common.commands import ArmDeclined, OrderRecord
from fleet_core.common.params import load_params
from fleet_core.orders.orchestrator import FILLING, SWAPPING, OrderOrchestrator
from stations import STATIONS


def make():
    return OrderOrchestrator(STATIONS, load_params(), ["r1"], lambda: 0.0)


def test_no_input_gives_any_output():
    orders = make()
    for feed in (lambda: orders.on_order_board("RUN", [OrderRecord("O-001", {"A": 1})]),
                 lambda: orders.on_dispatch_feedback(1, "r1", "NO_ROBOT"),
                 lambda: orders.on_dispatch_result(1, True, "r1"),
                 lambda: orders.on_arm_report(ArmDeclined("r1")),
                 lambda: orders.on_robot_removed("r1"),
                 lambda: orders.on_robot_reinstated("r1", "EMPTY"),
                 lambda: orders.on_robot_isolated("r1", "NAV_ABORTED"),
                 lambda: orders.step("r1")):
        feed()
        assert orders.take_output() == []


def test_a_known_robot_has_no_order_and_an_empty_basket():
    orders = make()
    assert orders.order_of("r1") == ""
    assert orders.robot_view("r1") == {"stage": "", "basket": "EMPTY", "order": None,
                                       "placed": {}}


def test_an_unknown_robot_has_nothing():
    orders = make()
    assert orders.order_of("ghost") == ""
    assert orders.robot_view("ghost") == {}


def test_nothing_shipped_and_the_station_phase_names_are_there():
    assert make().shipped == 0
    assert (FILLING, SWAPPING) == ("FILLING", "SWAPPING")
