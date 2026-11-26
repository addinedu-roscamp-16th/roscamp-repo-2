"""보고 대기열 시험(ROS 없이 돈다). 시계는 now 인자로 넣는다."""
import pytest
from fleet_core.common.commands import (
    CANCELED,
    HELD,
    HOLD_ROBOT_FAULT,
    SHIPPED,
    ReportOrderCmd,
    ReportTripCmd,
)
from fleet_manager.report_queue import HELLO, ORDER, TRIP, ReportQueue

READY = {HELLO: True, TRIP: True, ORDER: True}


def new_queue(max_in_flight=4):
    # 지연 배수 1.0: 시각 시험이 지터 없이 돈다
    return ReportQueue("boot-1", 1.0, 10.0, 3.0, max_in_flight, rand=lambda: 0.5)


def ready_queue(**kw):
    # hello에 epoch 7을 받은 큐
    q = new_queue(**kw)
    [h] = q.due(0.0, READY)
    q.answer(h.key, 0.0, True, 7)
    return q


def trip(trip_id="T-1", status="ASSIGNED"):
    return ReportTripCmd(trip_id, "pode1", status, {}, {})


def test_hello_is_sent_first_and_resent_with_the_same_boot_id():
    q = new_queue()
    [s] = q.due(0.0, READY)
    assert (s.kind, s.payload) == (HELLO, "boot-1")
    assert q.expire(3.0) == [s.key]
    assert q.due(3.9, READY) == [] and q.due(4.0, READY)[0].payload == "boot-1"


def test_reports_wait_for_the_epoch():
    q = new_queue()
    q.add(ReportTripCmd("T-1", "pode1", "ASSIGNED", {}, {}))
    [h] = q.due(0.0, READY)
    assert h.kind == HELLO and q.epoch is None
    q.answer(h.key, 0.0, True, 7)
    assert q.epoch == 7
    [s] = q.due(0.1, READY)
    assert s.kind == TRIP and (s.payload.epoch, s.payload.n, s.payload.status) == (7, 1, "ASSIGNED")


def test_backoff_doubles_to_ten_seconds_after_each_failure():
    # 답 없음 → 3초 한도 → 1, 2, 4, 8, 10, 10초 뒤 다시 보냄. 다시 보낼 때 n은 같음
    q = ready_queue()
    q.add(trip())
    sent_at = []  # due가 TRIP을 낸 시각 목록
    sends = []
    for i in range(140):
        t = i * 0.5
        q.expire(t)
        for s in q.due(t, READY):
            sent_at.append(t)
            sends.append(s)
    gaps = [b - a for a, b in zip(sent_at, sent_at[1:], strict=False)][:6]
    assert gaps == [3 + 1, 3 + 2, 3 + 4, 3 + 8, 3 + 10, 3 + 10]
    assert {s.payload.n for s in sends} == {1}


def test_a_trip_refusal_is_retried_like_no_answer():
    q = ready_queue()
    q.add(trip())
    [s] = q.due(1.0, READY)
    assert q.answer(s.key, 1.5, False) is None
    assert q.due(2.4, READY) == []
    [again] = q.due(2.5, READY)
    assert again.kind == TRIP and again.payload.n == s.payload.n
    assert again.key != s.key


def test_a_waiting_report_is_replaced_by_the_newest():
    q = ready_queue()
    q.add(trip(status="ASSIGNED"))
    [s] = q.due(1.0, READY)
    q.add(trip(status="FILLING"))
    q.add(trip(status="TRANSPORTING"))
    assert q.due(1.1, READY) == []
    q.answer(s.key, 1.2, True)
    [nxt] = q.due(1.2, READY)
    assert (nxt.payload.status, nxt.payload.n) == ("TRANSPORTING", 2)
    q.answer(nxt.key, 1.3, True)
    assert q.due(60.0, READY) == []


def test_a_replacing_report_after_a_failure_gets_a_new_number():
    q = ready_queue()
    q.add(trip(status="ASSIGNED"))
    [s] = q.due(1.0, READY)
    q.add(trip(status="FILLING"))
    q.answer(s.key, 1.5, None)
    [nxt] = q.due(2.5, READY)
    assert (nxt.payload.status, nxt.payload.n, nxt.payload.stamp) == ("FILLING", 2, 2.5)


def test_numbers_count_across_trips():
    q = ready_queue()
    q.add(trip("T-1"))
    q.add(trip("T-2"))
    sends = q.due(1.0, READY)
    assert [(s.payload.trip_id, s.payload.n) for s in sends] == [("T-1", 1), ("T-2", 2)]


def test_one_trip_has_one_request_in_flight():
    q = ready_queue()
    q.add(trip(status="ASSIGNED"))
    [s] = q.due(1.0, READY)
    q.add(trip(status="FILLING"))
    assert q.due(1.5, READY) == []
    assert q.due(2.9, READY) == []


def test_final_report_goes_to_central_only_after_the_bridge_accepts():
    q = ready_queue()
    q.add(ReportOrderCmd("T-1", "pode1", SHIPPED, {"A": 1}, {}, ""))
    [t] = q.due(1.0, READY)
    assert t.kind == TRIP and t.payload.status == "SHIPPED"
    assert t.payload.placed == {"A": 1}
    assert q.due(1.1, READY) == []  # 아직 ORDER 없음
    q.answer(t.key, 1.2, True)
    [o] = q.due(1.2, READY)
    assert o.kind == ORDER and o.payload.status == SHIPPED


@pytest.mark.parametrize("status, hold, trip_status, reason", [
    (CANCELED, "", "CANCELLED", ""), (HELD, HOLD_ROBOT_FAULT, HELD, HOLD_ROBOT_FAULT)])
def test_order_status_is_spelled_for_the_bridge(status, hold, trip_status, reason):
    q = ready_queue()
    cmd = ReportOrderCmd("T-1", "pode1", status, {}, {"A": 1}, "detail", hold)
    q.add(cmd)
    [t] = q.due(1.0, READY)
    assert (t.payload.status, t.payload.reason) == (trip_status, reason)
    q.answer(t.key, 1.1, True)
    [o] = q.due(1.1, READY)
    assert o.payload == cmd


def test_central_refusal_is_returned_and_not_retried():
    q = ready_queue()
    cmd = ReportOrderCmd("T-1", "pode1", SHIPPED, {"A": 1}, {}, "")
    q.add(cmd)
    [t] = q.due(1.0, READY)
    q.answer(t.key, 1.0, True)
    [o] = q.due(1.0, READY)
    now = 1.5
    assert q.answer(o.key, now, False) == cmd
    assert q.due(now + 60, READY) == []


def test_central_acceptance_returns_nothing():
    q = ready_queue()
    q.add(ReportOrderCmd("T-1", "pode1", SHIPPED, {}, {}, ""))
    [t] = q.due(1.0, READY)
    q.answer(t.key, 1.0, True)
    [o] = q.due(1.0, READY)
    assert q.answer(o.key, 1.5, True) is None
    assert q.due(60.0, READY) == []


def test_order_without_answer_is_retried():
    q = ready_queue()
    cmd = ReportOrderCmd("T-1", "pode1", SHIPPED, {}, {}, "")
    q.add(cmd)
    [t] = q.due(1.0, READY)
    q.answer(t.key, 1.0, True)
    [o] = q.due(1.0, READY)
    assert q.expire(4.0) == [o.key]
    assert q.due(4.9, READY) == []
    [again] = q.due(5.0, READY)
    assert (again.kind, again.payload) == (ORDER, cmd)


def test_order_reports_of_one_trip_replace_the_waiting_one():
    q = ready_queue()
    held = ReportOrderCmd("T-1", "pode1", HELD, {}, {"A": 1}, "", HOLD_ROBOT_FAULT)
    shipped = ReportOrderCmd("T-1", "pode1", SHIPPED, {"A": 1}, {}, "")
    q.add(held)
    [t1] = q.due(1.0, READY)
    q.answer(t1.key, 1.0, True)
    [o1] = q.due(1.0, READY)
    assert o1.payload == held
    q.add(shipped)
    [t2] = q.due(1.1, READY)
    assert t2.kind == TRIP and t2.payload.status == SHIPPED
    q.answer(t2.key, 1.2, True)
    assert q.due(1.2, READY) == []  # HELD의 ORDER가 아직 보내는 중
    q.answer(o1.key, 1.5, None)
    [o2] = q.due(2.5, READY)
    assert (o2.kind, o2.payload) == (ORDER, shipped)


def test_in_flight_limit_counts_trip_and_order():
    q = ready_queue(max_in_flight=1)
    q.add(trip("T-1"))
    q.add(trip("T-2"))
    [s1] = q.due(1.0, READY)
    assert s1.payload.trip_id == "T-1"
    assert q.due(1.1, READY) == []
    q.answer(s1.key, 1.2, True)
    [s2] = q.due(1.2, READY)
    assert s2.payload.trip_id == "T-2"


def test_in_flight_limit_counts_order_requests_too():
    q = ready_queue(max_in_flight=1)
    q.add(ReportOrderCmd("T-1", "pode1", SHIPPED, {}, {}, ""))
    [t] = q.due(1.0, READY)
    q.answer(t.key, 1.0, True)
    [o] = q.due(1.0, READY)
    assert o.kind == ORDER
    q.add(trip("T-2"))
    assert q.due(1.1, READY) == []
    q.answer(o.key, 1.2, True)
    [s] = q.due(1.2, READY)
    assert s.payload.trip_id == "T-2"


def test_not_ready_sends_nothing_and_does_not_back_off():
    q = ready_queue()
    q.add(trip())
    not_ready = {HELLO: True, TRIP: False, ORDER: True}
    assert q.due(1.0, not_ready) == []
    assert q.due(4.0, not_ready) == []
    [s] = q.due(5.0, READY)
    assert s.payload.n == 1
    q.expire(8.0)
    assert q.due(8.9, READY) == []
    assert q.due(9.0, READY)[0].payload.n == 1


def test_missing_ready_entry_means_not_ready():
    q = ready_queue()
    q.add(trip())
    assert q.due(1.0, {HELLO: True}) == []
    assert len(q.due(1.0, READY)) == 1


def test_a_late_answer_after_expiry_is_ignored():
    q = ready_queue()
    q.add(ReportOrderCmd("T-1", "pode1", SHIPPED, {}, {}, ""))
    [t] = q.due(1.0, READY)
    assert q.expire(4.0) == [t.key]
    assert q.answer(t.key, 4.1, True) is None
    assert q.due(4.5, READY) == []  # ORDER 줄에 오르지 않음
    [again] = q.due(5.0, READY)
    assert again.kind == TRIP
    q.answer(again.key, 5.1, True)
    [o] = q.due(5.1, READY)
    assert o.kind == ORDER
    assert q.due(5.2, READY) == []
    assert q.answer(t.key, 5.3, True) is None
    assert q.due(5.4, READY) == []
