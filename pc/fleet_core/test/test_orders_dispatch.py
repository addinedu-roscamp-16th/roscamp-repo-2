from fleet_core.common.commands import (
    NO_ROBOT,
    TRIP_ASSIGNED,
    CancelDispatchCmd,
    DispatchCmd,
    EventOut,
    OrderRecord,
    ReportTripCmd,
)
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


def bound_to_r1():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    return o


def test_an_answered_call_binds_the_first_waiting_order_to_that_pode():
    o = bound_to_r1()
    assert o.take_output() == [EventOut("INFO", "ORDER_BOUND", "T-1", "r1"),
                               ReportTripCmd("T-1", "r1", TRIP_ASSIGNED, {}, {})]
    assert o.order_of("r1") == "T-1"
    assert o.robot_view("r1") == {"stage": "FILLING", "basket": "EMPTY", "order": "T-1",
                                  "placed": {}}


def test_the_first_waiting_order_is_bound_whatever_call_answers():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_result(2, True, "r2")
    assert o.order_of("r2") == "T-1"
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    assert o.order_of("r1") == "T-2"


def test_a_bound_order_stays_bound_and_is_not_called_again():
    o = bound_to_r1()
    o.take_output()
    o.on_order_board("RUN", [T2])
    assert o.take_output() == []
    assert o.order_of("r1") == "T-1"
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == []


def test_a_failed_call_is_reopened_with_a_new_id():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, False, "", "NO_SUCH_ROBOT")
    assert o.take_output() == [DispatchCmd(2, "WORK")]
    assert o.order_of("r1") == "" and o.order_of("r2") == ""


def test_an_answer_from_an_unknown_robot_reopens_the_call():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, True, "ghost")
    assert o.take_output() == [DispatchCmd(2, "WORK")]


def test_an_answer_to_a_call_that_is_not_open_is_ignored():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(7, True, "r1")
    assert o.take_output() == [] and o.order_of("r1") == ""


def test_no_robot_feedback_names_the_waiting_orders():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_feedback(1, "", NO_ROBOT)
    assert o.take_output() == [EventOut("WARN", "NO_ROBOT_AVAILABLE",
                                        "['T-1', 'T-2'] wait and no robot can take them")]
    o.on_dispatch_feedback(1, "r1", "MOVING")
    assert o.take_output() == []
