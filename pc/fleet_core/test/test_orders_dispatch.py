from fleet_core.common.commands import CancelDispatchCmd, DispatchCmd, EventOut, OrderRecord
from fleet_core.common.params import load_params
from fleet_core.orders.orchestrator import OrderOrchestrator
from stations import STATIONS

T1, T2 = OrderRecord("T-1", {"A": 1}), OrderRecord("T-2", {"B": 2})


def make(robots=("r1", "r2")):
    return OrderOrchestrator(STATIONS, load_params(), list(robots), lambda: 0.0)


def test_each_waiting_order_opens_one_unnamed_work_call():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == [DispatchCmd(1, "WORK"), DispatchCmd(2, "WORK")]


def test_the_same_board_again_opens_nothing_more():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == []


def test_a_shorter_board_cancels_the_newest_call_first():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_order_board("RUN", [T1])
    assert o.take_output() == [CancelDispatchCmd(2)]


def test_the_run_mode_does_not_hold_calls_back():
    o = make()
    o.on_order_board("PAUSE", [T1])
    assert o.take_output() == [DispatchCmd(1, "WORK")]


def test_four_items_fit_the_basket():
    o = make()
    o.on_order_board("RUN", [OrderRecord("T-4", {"A": 4})])
    assert o.take_output() == [DispatchCmd(1, "WORK")]


def test_an_order_over_capacity_warns_once_and_opens_no_call():
    o = make()
    big = OrderRecord("T-9", {"A": 3, "B": 2})
    o.on_order_board("RUN", [big])
    assert o.take_output() == [
        EventOut("WARN", "ORDER_OVER_CAPACITY", "T-9: 5 items exceed the basket capacity of 4")]
    o.on_order_board("RUN", [big])
    assert o.take_output() == []


def test_an_order_that_left_the_board_can_warn_again():
    o = make()
    big = OrderRecord("T-9", {"A": 5})
    o.on_order_board("RUN", [big])
    o.take_output()
    o.on_order_board("RUN", [])
    o.on_order_board("RUN", [big])
    assert [e.code for e in o.take_output()] == ["ORDER_OVER_CAPACITY"]
