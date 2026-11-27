from fleet_core.common.commands import (
    TRIP_ASSIGNED,
    ArmDeclined,
    ArmFailedCmd,
    ArmFault,
    ArmFillCmd,
    ArmFillPaused,
    ArmFillPlaced,
    ArmFillProgress,
    ArmFillResult,
    ArmFillResumed,
    ArmLate,
    ArmLost,
    ArmResetCmd,
    ArmSwapCmd,
    ArmStationBlocked,
    ArmSwapResult,
    CancelDispatchCmd,
    DispatchCmd,
    EventOut,
    OrderRecord,
    ReleaseCmd,
    ReportOrderCmd,
    ReportTripCmd,
    RobotFaultCmd,
)
from fleet_core.common.params import load_params
from fleet_core.orders.orchestrator import OrderOrchestrator
from orders_and_arm import OrdersAndArm
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


def bound(o, order=T1):
    """주문판에 주문을 올려 r1에 묶고 출력을 비운 채 돌려준다(시험 도움 함수)."""
    o.on_order_board("RUN", [order])
    o.on_dispatch_result(1, True, "r1")
    o.take_output()
    return o


def test_step_asks_for_the_remaining_items_and_reports_filling():
    """첫 step이 FILLING 보고 뒤 남은 상품으로 ArmFillCmd를 내고 asked를 fill로 두는지 지킨다."""
    o = bound(make())
    o.step("r1")
    assert o.take_output() == [ReportTripCmd("T-1", "r1", "FILLING", {}, {}),
                               ArmFillCmd("r1", "WORK", "T-1", {"A": 1})]
    assert o._robots["r1"].asked == "fill"


def test_step_waits_while_a_fill_is_asked():
    """답을 기다리는 중에 같은 채우기를 또 요청해 두 번 담는 일을 막는다."""
    o = bound(make())
    o.step("r1")
    o.take_output()
    o.step("r1")
    assert o.take_output() == []


def test_filling_is_reported_once_per_order():
    """ArmDeclined 뒤 재요청에서 FILLING 보고를 다시 내지 않는지 지킨다(전역 제약 3)."""
    o = bound(make())
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmDeclined("r1"))
    o.step("r1")
    assert o.take_output() == [ArmFillCmd("r1", "WORK", "T-1", {"A": 1})]


def test_progress_reports_placed_so_far_and_only_real_missing():
    """진행 보고가 담은 만큼만 싣고 남은 상품을 missing으로 싣지 않는지 지킨다."""
    o = bound(make(), OrderRecord("T-1", {"A": 2, "B": 1}))
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmFillProgress("r1", {"A": 1}))
    assert o.take_output() == [ReportTripCmd("T-1", "r1", "FILLING", {"A": 1}, {})]


def test_progress_is_not_counted_twice_when_the_result_comes():
    """진행 보고 뒤 결과가 오면 placed를 두 번 더하지 않고 progress를 비우는지 지킨다(전역 제약 2)."""
    o = bound(make(), OrderRecord("T-1", {"A": 2, "B": 1}))
    o.step("r1")
    o.on_arm_report(ArmFillProgress("r1", {"A": 1}))
    o.on_arm_report(ArmFillResult("r1", True, {"A": 2, "B": 1}))
    order = o._robots["r1"].order
    assert order.placed == {"A": 2, "B": 1} and order.progress == {}


def test_a_failed_fill_adds_what_was_placed_and_reports_the_failure():
    """실패한 채우기가 담은 만큼을 더하고 ArmFailedCmd를 내며 asked를 비우는지 지킨다."""
    o = bound(make())
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmFillResult("r1", False, {"A": 1}, "NOT_READY"))
    assert o._robots["r1"].order.placed == {"A": 1}
    assert ArmFailedCmd("r1") in o.take_output()
    assert o._robots["r1"].asked == ""


def test_a_full_fill_goes_to_outbound_and_reports_transporting():
    """다 담으면 ArmResetCmd, OUTBOUND 호출, TRANSPORTING 보고 순으로 나가고 basket이 FULL이 되는지 지킨다."""
    o = bound(make())
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, {"A": 1}))
    o.take_output()
    o.step("r1")
    goal = o._robots["r1"].outbound_goal
    assert o.take_output() == [ArmResetCmd("r1"), DispatchCmd(goal, "OUTBOUND", "r1"),
                               ReportTripCmd("T-1", "r1", "TRANSPORTING", {"A": 1}, {})]
    assert o.robot_view("r1")["basket"] == "FULL"


def test_a_partial_fill_still_ships():
    """일부만 담겨도 missing을 기록하고 OUTBOUND로 내보내는지 지킨다(pseudocode 4단계)."""
    o = bound(make(), OrderRecord("T-1", {"A": 2}))
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, {"A": 1}))
    o.take_output()
    o.step("r1")
    assert o._robots["r1"].order.missing == {"A": 1}
    assert o._robots["r1"].outbound_goal is not None
    assert o.robot_view("r1")["basket"] == "FULL"


def test_nothing_placed_is_canceled_and_released():
    """하나도 못 담으면 CANCELED 보고와 ReleaseCmd를 내고 최종 보고 집합에 넣는지 지킨다."""
    o = bound(make())
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, {}))
    o.take_output()
    o.step("r1")
    assert o.take_output() == [
        ArmResetCmd("r1"),
        ReportOrderCmd("T-1", "r1", "CANCELED", {}, {"A": 1},
                       "nothing could be placed, missing {'A': 1}"),
        ReleaseCmd("r1")]
    assert "T-1" in o._final and o.robot_view("r1")["stage"] == ""


# ---- Task 3: 출고 이동 · 교환 · 끝 상태 보고 · 빈 바구니 ----
def make_clocked(robots=("r1", "r2")):
    """시각을 바꿀 수 있는 시계로 코어를 만든다. (코어, 시각 목록)을 돌려준다(시험 도움 함수)."""
    now = [0.0]
    return OrderOrchestrator(STATIONS, load_params(), list(robots), lambda: now[0]), now


def at_outbound(order=T1, placed=None):
    """r1이 채우기를 끝내고 OUTBOUND에 닿아 교환 단계가 된 코어를 돌려준다(시험 도움 함수)."""
    o, now = make_clocked()
    bound(o, order)
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, placed if placed is not None else dict(order.items)))
    o.step("r1")
    o.on_dispatch_result(o._robots["r1"].outbound_goal, True, "r1")
    o.take_output()
    return o, now


def test_reaching_outbound_starts_the_swap():
    """OUTBOUND에 닿으면 SWAPPING이 되고 step이 찬 바구니를 빼고 빈 바구니를 놓는 교환을 묻는지 지킨다."""
    o, _ = at_outbound()
    o.step("r1")
    assert o.take_output() == [ArmSwapCmd("r1", "OUTBOUND", "T-1", True, True)]
    assert o._robots["r1"].asked == "swap"


def test_taking_the_full_basket_reports_shipped():
    """찬 바구니를 뺀 순간 SHIPPED를 보고하고 shipped를 올리며, 빈 바구니를 못 놓았으면 ArmFailedCmd를 내는지 지킨다."""
    o, _ = at_outbound()
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmSwapResult("r1", True, False))
    assert o.take_output() == [ReportOrderCmd("T-1", "r1", "SHIPPED", {"A": 1}, {}, ""),
                               ArmFailedCmd("r1")]
    assert o.shipped == 1 and o.robot_view("r1")["basket"] == "NONE"
    assert o.order_of("r1") == "" and "T-1" in o._final


def test_putting_the_empty_basket_releases_the_robot():
    """빈 바구니까지 놓으면 ArmResetCmd와 ReleaseCmd로 로봇을 쉼(stage "")으로 돌리는지 지킨다."""
    o, _ = at_outbound()
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmSwapResult("r1", True, True))
    out = o.take_output()
    assert out[1:] == [ArmResetCmd("r1"), ReleaseCmd("r1")]
    assert out[0].status == "SHIPPED"
    assert o.robot_view("r1")["basket"] == "EMPTY" and o.robot_view("r1")["stage"] == ""


def test_missing_items_report_partial():
    """못 담은 품목이 있으면 PARTIAL로 보고하고 shipped에도 센다(설계 4.7)."""
    o, _ = at_outbound(OrderRecord("T-1", {"A": 2}), {"A": 1})
    o.step("r1")
    o.take_output()
    o.on_arm_report(ArmSwapResult("r1", True, True))
    assert o.take_output()[0] == ReportOrderCmd("T-1", "r1", "PARTIAL", {"A": 1}, {"A": 1}, "")
    assert o.shipped == 1


def test_a_swap_that_took_nothing_does_not_report_again():
    """이미 빼낸 뒤 재요청한 교환(basket NONE)이 끝 상태 보고를 두 번 내는 일을 막는다."""
    o, _ = at_outbound()
    o.step("r1")
    o.on_arm_report(ArmSwapResult("r1", True, False))
    o.take_output()
    o.step("r1")
    assert o.take_output() == [ArmSwapCmd("r1", "OUTBOUND", "T-1", False, True)]
    o.on_arm_report(ArmSwapResult("r1", False, True))
    out = o.take_output()
    assert not any(isinstance(c, ReportOrderCmd) for c in out) and o.shipped == 1


def test_swapping_with_an_empty_basket_just_releases():
    """바구니가 이미 비어 있으면 교환을 다시 묻지 않고 stage를 비워 놓아 주는지 지킨다."""
    o, _ = at_outbound()
    o._robots["r1"].basket = "EMPTY"
    o.step("r1")
    assert o.take_output() == [ReleaseCmd("r1")] and o.robot_view("r1")["stage"] == ""


def test_a_failed_outbound_call_is_retried_after_the_wait():
    """실패한 OUTBOUND 호출을 2.0초 기다린 뒤에만 다시 부르고 DISPATCH_RETRY를 알리는지 지킨다."""
    o, now = make_clocked()
    bound(o)
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, {"A": 1}))
    o.step("r1")
    o.take_output()
    now[0] = 1.0
    o.on_dispatch_result(o._robots["r1"].outbound_goal, False, "r1", "no path")
    now[0] = 2.9
    o.step("r1")
    assert o.take_output() == []
    now[0] = 3.0
    o.step("r1")
    out = o.take_output()
    assert out[0].level == "WARN" and out[0].code == "DISPATCH_RETRY" and "r1" in out[0].detail
    assert out[1] == DispatchCmd(o._robots["r1"].outbound_goal, "OUTBOUND", "r1")


def test_too_many_outbound_failures_fault_the_robot():
    """실패가 한도(3)를 넘는 4번째면 RobotFaultCmd와 DISPATCH_FAILED만 내고 더 부르지 않는지 지킨다."""
    o, now = make_clocked()
    bound(o)
    o.step("r1")
    o.on_arm_report(ArmFillResult("r1", True, {"A": 1}))
    o.step("r1")
    for n in range(4):
        o.on_dispatch_result(o._robots["r1"].outbound_goal, False, "r1", "no path")
        now[0] += 2.0
        o.take_output()
        o.step("r1")
    out = o.take_output()
    assert out[0] == RobotFaultCmd("r1", "no path")
    assert out[1].level == "ERROR" and out[1].code == "DISPATCH_FAILED" and "r1" in out[1].detail
    assert len(out) == 2
    now[0] += 10.0
    o.step("r1")
    assert o.take_output() == []


def test_a_robot_without_a_basket_fetches_an_empty_one():
    """바구니 없는(NONE) 쉬는 로봇이 빈 바구니를 받으러 OUTBOUND 호출을 내고, 기다리는 동안 또 내지 않는지 지킨다."""
    o, _ = make_clocked()
    o._robots["r1"].basket = "NONE"
    o.step("r1")
    goal = o._robots["r1"].outbound_goal
    assert o.take_output() == [DispatchCmd(goal, "OUTBOUND", "r1")]
    o.step("r1")
    assert o.take_output() == []


def test_a_shipped_order_left_on_the_board_is_not_bound_again():
    """출고를 보고한 주문이 주문판에 남아 있어도 WORK 호출을 다시 열지 않는지 지킨다(전역 제약 4)."""
    o, _ = at_outbound()
    o.step("r1")
    o.on_arm_report(ArmSwapResult("r1", True, True))
    o.take_output()
    o.on_order_board("RUN", [T1])
    assert not any(isinstance(c, DispatchCmd) for c in o.take_output())


# ---- 팔 관제 합의 보고와 팔 고장(설계 4.4 · 4.5, Task 4) ----
def filling(order=T1):
    """주문을 묶고 첫 step으로 담기를 묻고 출력을 비운 코어(시험 도움 함수)."""
    o = bound(make(), order)
    o.step("r1")
    o.take_output()
    return o


def test_a_paused_fill_reports_filling_pause_and_is_not_asked_again():
    """담기 일시 중지가 FILLING_PAUSE로 보고되고 관리자가 풀 때까지 step이 다시 묻지 않는지 지킨다."""
    o = filling()
    o.on_arm_report(ArmFillPaused("r1", "workcell", "RECOGNITION_ERROR", "workcell: x"))
    assert o.take_output() == [ReportTripCmd("T-1", "r1", "FILLING_PAUSE", {}, {},
                                             "RECOGNITION_ERROR")]
    assert o._robots["r1"].asked == "paused"
    o.step("r1")
    assert o.take_output() == []


def test_a_failed_result_then_pause_keeps_the_placed_count():
    """실패 결과 뒤 같은 pump의 일시 중지 보고가 결과로 늘어난 placed를 싣는지 지킨다."""
    o = filling(OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillResult("r1", False, {"A": 1}, "RECOGNITION_ERROR"))
    o.take_output()
    o.on_arm_report(ArmFillPaused("r1", "workcell", "RECOGNITION_ERROR", "d"))
    assert o.take_output() == [ReportTripCmd("T-1", "r1", "FILLING_PAUSE", {"A": 1}, {},
                                             "RECOGNITION_ERROR")]


def test_no_fill_is_asked_while_waiting_for_resume():
    """재시작(ArmFillPlaced) 뒤 [복구] 전에는 수량만 더하고 남은 상품을 다시 묻지 않는지 지킨다(전역 제약 1)."""
    o = filling(OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillPlaced("r1", {"A": 1}))
    assert o.take_output() == []
    assert o._robots["r1"].order.placed == {"A": 1}
    assert o._robots["r1"].asked == "resume_wait"
    o.step("r1")
    assert o.take_output() == []


def test_resume_asks_only_for_the_rest():
    """[복구] 뒤 step이 이미 담은 수를 뺀 남은 상품만 새 담기 목표로 요청하는지 지킨다."""
    o = filling(OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillPlaced("r1", {"A": 1}))
    o.on_arm_report(ArmFillResumed("r1"))
    o.step("r1")
    assert o.take_output() == [ArmFillCmd("r1", "WORK", "T-1", {"A": 1})]


def test_placed_and_resumed_only_act_in_their_asked_state():
    """묻지 않은 상태의 Placed와 resume_wait 아닌 상태의 Resumed가 수량이나 asked를 건드리지 않는지 지킨다."""
    o = bound(make(), OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillPlaced("r1", {"A": 1}))  # 아직 담기를 묻지 않음(asked "")
    assert o._robots["r1"].order.placed == {} and o._robots["r1"].asked == ""
    o.step("r1")
    o.on_arm_report(ArmFillResumed("r1"))  # asked는 fill
    assert o._robots["r1"].asked == "fill"


def test_a_stray_progress_after_the_result_changes_nothing():
    """결과가 온 뒤 늦게 온 진행 보고가 progress를 되살려 placed를 두 번 세게 하지 않는지 지킨다."""
    o = filling(OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillResult("r1", True, {"A": 2}))
    o.take_output()
    o.on_arm_report(ArmFillProgress("r1", {"A": 2}))
    assert o._robots["r1"].order.progress == {}
    assert o.take_output() == []


def test_a_held_placed_report_adds_the_count_then_the_fault_holds():
    """쥔 채 재시작(held=True) Placed가 수량만 더하고, 이어진 ArmFault가 보류로 넘기는지 지킨다."""
    o = filling(OrderRecord("T-1", {"A": 2}))
    o.on_arm_report(ArmFillPlaced("r1", {"A": 1}, held=True))
    assert o.take_output() == []
    o.on_arm_report(ArmFault("r1", "fill", "workcell", "restarted holding"))
    assert o.take_output() == [ReportOrderCmd("T-1", "r1", "HELD", {"A": 1}, {"A": 1},
                                              "restarted holding", "ARM_FAULT")]


def test_an_arm_fault_holds_the_order_on_the_robot():
    """팔 고장이 주문을 로봇에 묶은 채 HELD로 보고하고 보류 목록에 넣고 step을 멈추는지 지킨다."""
    o = filling()
    o.on_arm_report(ArmFault("r1", "fill", "workcell", "boom"))
    assert o.take_output() == [ReportOrderCmd("T-1", "r1", "HELD", {}, {"A": 1}, "boom",
                                              "ARM_FAULT")]
    assert o._holds["T-1"] == ("r1", "ARM_FAULT")
    assert o._robots["r1"].held == "ARM_FAULT"
    assert o.order_of("r1") == "T-1"
    o.step("r1")
    assert o.take_output() == []


def test_held_report_keeps_the_progress_placed_before_an_arm_fault():
    """진행 보고로 받은 수가 팔 고장 HELD 보고의 placed에서 빠지지 않는지 지킨다(설계 4.4)."""
    o = filling(OrderRecord("T-1", {"A": 3}))
    o.on_arm_report(ArmFillProgress("r1", {"A": 2}))
    o.take_output()
    o.on_arm_report(ArmFault("r1", "fill", "workcell", "boom"))
    assert o.take_output() == [ReportOrderCmd("T-1", "r1", "HELD", {"A": 2}, {"A": 1}, "boom",
                                              "ARM_FAULT")]
    order = o._robots["r1"].order
    assert order.placed == {"A": 2} and order.progress == {}


def test_held_report_keeps_the_progress_placed_when_the_arm_was_lost():
    """답이 없어 ArmLost 뒤 ArmFault로 보류돼도 진행분이 placed에 남고 두 번 세지 않는지 지킨다."""
    o = filling(OrderRecord("T-1", {"A": 3}))
    o.on_arm_report(ArmFillProgress("r1", {"A": 2}))
    o.take_output()
    o.on_arm_report(ArmLost("r1", 5, "workcell", "fill"))
    o.on_arm_report(ArmFault("r1", "fill", "workcell", "no answer"))
    assert o.take_output() == [ReportOrderCmd("T-1", "r1", "HELD", {"A": 2}, {"A": 1},
                                              "no answer", "ARM_FAULT")]
    assert o.robot_view("r1")["placed"] == {"A": 2}


def test_a_lost_arm_job_only_clears_what_was_asked():
    """ArmLost가 asked만 비우고 아무것도 내지 않는지(이어지는 ArmFault가 보류함) 지킨다."""
    o = filling()
    o.on_arm_report(ArmLost("r1", 5, "workcell", "fill"))
    assert o._robots["r1"].asked == ""
    assert o.take_output() == []


def test_a_blocked_workcell_cancels_and_reopens_its_calls():
    """작업대가 막히면 열린 호출을 거두고 풀리면 대기 주문 수만큼 새 호출을 여는지 지킨다(설계 4.5)."""
    o = make()
    o.on_order_board("RUN", [T1])
    assert o.take_output() == [DispatchCmd(1, "WORK")]
    o.on_arm_report(ArmStationBlocked("WORK", "arm_left ERROR"))
    assert o.take_output() == [CancelDispatchCmd(1)]
    o.on_order_board("RUN", [T1])  # 막힌 동안 주문판이 다시 와도 열지 않는다
    assert o.take_output() == []
    o.on_arm_report(ArmStationBlocked("WORK", ""))
    assert o.take_output() == [DispatchCmd(2, "WORK")]


def test_a_late_arm_result_is_ignored_silently():
    """늦은 팔 결과(ArmLate)가 출력도 수량 변화도 내지 않는지 지킨다(1차는 잃은 작업 기록이 없음)."""
    o = filling()
    o.on_arm_report(ArmLate(9, "fill", {"A": 1}))
    assert o.take_output() == []
    assert o._robots["r1"].order.placed == {}


def test_orders_and_arm_run_one_order_to_shipped():
    """주문 코어와 실제 ArmJobs를 이어 묶기부터 SHIPPED 보고까지 한 주문이 흐르는지 지킨다."""
    core = OrdersAndArm(STATIONS, load_params(), ["r1", "r2"], lambda: 0.0)
    core.on_order_board("RUN", [T1])
    core.on_dispatch_result(core._open_calls[0], True, "r1")
    core.take_output()
    core.step("r1")
    fill = next(c for c in core.take_output() if type(c).__name__ == "FillCmd")
    core.on_fill_result(fill.goal_id, True, {"A": 1}, {})
    core.step("r1")
    goal = next(c for c in core.take_output() if isinstance(c, DispatchCmd))
    core.on_dispatch_result(goal.goal_id, True, "r1")
    core.step("r1")
    swap = next(c for c in core.take_output() if type(c).__name__ == "SwapCmd")
    core.on_swap_result(swap.goal_id, True, True, True)
    assert ReportOrderCmd("T-1", "r1", "SHIPPED", {"A": 1}, {}, "") in core.take_output()


# ---- Task 5: 서비스 정지(설계 4.8, 팔 관제 합의 ⑦, 회의 정리 8번) ----
def test_service_paused_follows_the_run_mode():
    """service_paused가 마지막 run_mode가 PAUSE일 때만 True인지 지킨다(STOP은 False)."""
    o = make()
    assert o.service_paused is False
    o.on_order_board("PAUSE", [])
    assert o.service_paused is True
    o.on_order_board("RUN", [])
    assert o.service_paused is False
    o.on_order_board("STOP", [])
    assert o.service_paused is False


def test_a_pause_cancels_open_work_calls_and_run_reopens_them():
    """PAUSE가 열린 WORK 호출을 거두고 RUN이 대기 주문 수만큼 새 호출로 다시 여는지 지킨다."""
    o = make()
    o.on_order_board("RUN", [T1])
    assert o.take_output() == [DispatchCmd(1, "WORK")]
    o.on_order_board("PAUSE", [T1])
    assert o.take_output() == [CancelDispatchCmd(1)]
    o.on_order_board("RUN", [T1])
    assert o.take_output() == [DispatchCmd(2, "WORK")]


def test_a_robot_reaching_work_during_a_pause_is_released_unbound():
    """정지 중에 작업대에 닿은 로봇을 묶지 않고 놓아 주는지 지킨다(설계 4.8)."""
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o._run_mode = "PAUSE"  # 호출을 거두기 전(같은 tick)에 정지가 와서 열린 호출 1이 남은 순간을 만든다
    o.on_dispatch_result(1, True, "r1")
    assert o.take_output() == [ReleaseCmd("r1")]
    assert o.order_of("r1") == ""


def test_a_bound_order_finishes_during_a_pause():
    """이미 묶인 주문은 정지 중에도 step이 채우기를 이어 가는지 지킨다(회의 정리 8번)."""
    o = bound(make())
    o.on_order_board("PAUSE", [T1])
    o.take_output()
    o.step("r1")
    assert o.take_output() == [ReportTripCmd("T-1", "r1", "FILLING", {}, {}),
                               ArmFillCmd("r1", "WORK", "T-1", {"A": 1})]


def test_no_empty_basket_is_fetched_during_a_pause():
    """정지 중에는 바구니 없는 쉬는 로봇이 빈 바구니를 받으러 가지 않고 RUN이면 간다는 것을 지킨다(설계 4.8)."""
    o, _ = make_clocked()
    o._robots["r1"].basket = "NONE"
    o.on_order_board("PAUSE", [])
    o.step("r1")
    assert o.take_output() == []
    o.on_order_board("RUN", [])
    o.step("r1")
    assert o.take_output() == [DispatchCmd(o._robots["r1"].outbound_goal, "OUTBOUND", "r1")]
