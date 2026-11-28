"""운반 건 넘기기 기록 core 시험 (시험 DB)."""
import datetime
import logging

import pytest

from service_bridge.core.dispatch import (
    head_item, record_ack, record_failure, record_not_ready, retry_delay)
from service_bridge.core.intake import place_order
from service_bridge.core.types import OrderLine, OrderRequest


def _place(conn, seeded, kind_qty, key):
    items = tuple(OrderLine(seeded["products"][k], q) for k, q in kind_qty)
    res = place_order(conn, OrderRequest(seeded["accounts"]["tester"], items), key)
    assert not hasattr(res, "code"), res
    return res


def _fail(conn, trip_id, n, result="TIMEOUT"):
    for _ in range(n):
        last = record_failure(conn, trip_id, result)
    return last


def _alerts(conn, trip_id):
    return conn.execute(
        "SELECT level, code, source, order_id, trip_id, status, detail, resolved_at "
        "FROM alert WHERE code = 'DISPATCH_STALLED' AND trip_id = %s", (trip_id,)).fetchall()


def _row(conn, trip_id):
    return conn.execute(
        "SELECT attempts, next_attempt_at, unknown_since, last_result, central_acked_at "
        "FROM trip_dispatch WHERE trip_id = %s", (trip_id,)).fetchone()


def test_head_is_oldest_pending(conn, seeded):
    a = _place(conn, seeded, [("mentos", 5)], "k1")      # -1, -2
    b = _place(conn, seeded, [("choco_bar", 1)], "k2")
    assert head_item(conn).trip_id == a.trip_ids[0]
    record_ack(conn, a.trip_ids[0], True, "")
    assert head_item(conn).trip_id == a.trip_ids[1]
    record_ack(conn, a.trip_ids[1], True, "")
    assert head_item(conn).trip_id == b.trip_ids[0]
    record_ack(conn, b.trip_ids[0], True, "")
    assert head_item(conn) is None


def test_head_item_fields(conn, seeded):
    res = _place(conn, seeded, [("mentos", 3), ("choco_bar", 3)], "k1")   # -1: A3 B1, -2: B2
    h = head_item(conn)
    assert (h.trip_id, h.order_id, h.trip_index, h.trip_count) == (
        res.trip_ids[0], res.order_id, 1, 2)
    assert h.items == {"mentos": 3, "choco_bar": 1}
    assert h.attempts == 0 and h.next_attempt_at is None
    created = conn.execute("SELECT created_at FROM trip WHERE trip_id = %s",
                           (h.trip_id,)).fetchone()[0]
    assert h.submitted_at == created
    record_ack(conn, res.trip_ids[0], True, "")
    h2 = head_item(conn)
    assert (h2.trip_index, h2.trip_count, h2.items) == (2, 2, {"choco_bar": 2})


def test_ack_marks_and_resolves_alert(conn, seeded):
    res = _place(conn, seeded, [("mentos", 5)], "k1")
    t = res.trip_ids[0]
    _fail(conn, t, 5)
    assert [a[5] for a in _alerts(conn, t)] == ["OPEN"]
    record_ack(conn, t, True, "")
    attempts, _, unknown, last, acked = _row(conn, t)
    assert acked is not None and last == "ACCEPTED" and attempts == 6 and unknown is None
    alerts = _alerts(conn, t)
    assert len(alerts) == 1 and alerts[0][5] == "RESOLVED" and alerts[0][7] is not None


def test_ack_rejected_records_reason(conn, seeded):
    t = _place(conn, seeded, [("mentos", 1)], "k1").trip_ids[0]
    record_ack(conn, t, False, "BAD_ITEMS")
    attempts, _, _, last, acked = _row(conn, t)
    assert (attempts, last) == (1, "BAD_ITEMS") and acked is not None


def test_four_failures_no_rest(conn, seeded):
    t = _place(conn, seeded, [("mentos", 1)], "k1").trip_ids[0]
    assert _fail(conn, t, 4, "NOT_READY") == 4
    attempts, next_at, unknown, last, _ = _row(conn, t)
    assert (attempts, next_at, unknown, last) == (4, None, None, "NOT_READY")
    assert _alerts(conn, t) == []


def test_fifth_failure_rests_and_alerts(conn, seeded, caplog):
    res = _place(conn, seeded, [("mentos", 5)], "k1")     # 두 건: 뒤에 한 건
    t = res.trip_ids[0]
    _fail(conn, t, 4)
    # ROS 환경에서는 이 로거의 propagate가 꺼져 있어 caplog 핸들러를 로거에 직접 단다
    dispatch_log = logging.getLogger("service_bridge.dispatch")
    dispatch_log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="service_bridge.dispatch"):
            assert record_failure(conn, t, "TIMEOUT") == 5
    finally:
        dispatch_log.removeHandler(caplog.handler)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    _, next_at, unknown, _, _ = _row(conn, t)
    now = conn.execute("SELECT now()").fetchone()[0]
    assert unknown is not None
    assert abs((next_at - now).total_seconds() - 30) < 5
    (level, code, source, order_id, trip_id, status, detail, _), = _alerts(conn, t)
    assert (level, code, source, order_id, trip_id, status) == (
        "WARN", "DISPATCH_STALLED", "service_bridge", res.order_id, t, "OPEN")
    assert detail == {"attempts": 5, "last_result": "TIMEOUT",
                      "unknown_since": unknown.isoformat(), "waiting_trips": 1}


def test_more_failures_keep_one_alert(conn, seeded):
    t = _place(conn, seeded, [("mentos", 1)], "k1").trip_ids[0]
    _fail(conn, t, 5)
    first = _row(conn, t)[2]
    _fail(conn, t, 2, "NOT_READY")
    alerts = _alerts(conn, t)
    assert len(alerts) == 1
    detail = alerts[0][6]
    assert detail["attempts"] == 7 and detail["last_result"] == "NOT_READY"
    assert detail["unknown_since"] == first.isoformat()
    assert _row(conn, t)[2] == first
    assert detail["waiting_trips"] == 0


def test_rest_survives_restart(conn, seeded):
    res = _place(conn, seeded, [("mentos", 1)], "k1")
    _place(conn, seeded, [("choco_bar", 1)], "k2")
    t = res.trip_ids[0]
    _fail(conn, t, 5)
    h = head_item(conn)          # 새 프로세스가 읽어도 같은 건 + 쉼이 DB에 남아 있음
    now = conn.execute("SELECT now()").fetchone()[0]
    assert h.trip_id == t and h.attempts == 5
    assert h.next_attempt_at > now + datetime.timedelta(seconds=20)


def test_not_ready_keeps_attempts(conn, seeded):
    # NOT_READY는 central이 "아직 못 받음"이라고 확정한 답: 시도로 세지 않는다 (설계 6.4 규칙 1)
    t = _place(conn, seeded, [("mentos", 1)], "k1").trip_ids[0]
    _fail(conn, t, 2)
    for _ in range(6):
        record_not_ready(conn, t)
    attempts, next_at, unknown, last, acked = _row(conn, t)
    assert (attempts, next_at, unknown, last, acked) == (2, None, None, "NOT_READY", None)
    assert _alerts(conn, t) == []


def test_retry_delay():
    assert retry_delay(0, lambda: 0.5) == 1.0
    assert retry_delay(4, lambda: 0.0) == pytest.approx(0.8)
    assert retry_delay(5) is None
