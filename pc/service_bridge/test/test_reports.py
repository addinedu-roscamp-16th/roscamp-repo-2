"""apply_report (F-09, 설계 6.4) 시험: 실제 PostgreSQL 시험 DB."""
import itertools
import time

import psycopg
import pytest

from service_bridge.core.db import make_pool
from service_bridge.core.reports import apply_report
from service_bridge.core.types import ReportIn, ReportOut

E = 7
_seq = itertools.count(1)


def D() -> float:
    return time.monotonic() + 2.5


def make_trip(conn, account_id, products, items: dict[str, int]) -> str:
    """웹이 넣는 것처럼 주문 · 운반 건 하나(RECEIVED)와 접수 이력 두 줄을 넣는다. Task 7에서 바꿈."""
    order_id = f"O-T-{next(_seq):04d}"
    trip_id = f"{order_id}-1"
    conn.execute(
        "insert into orders (order_id, account_id, status) values (%s, %s, 'RECEIVED')",
        (order_id, account_id),
    )
    for kind, q in items.items():
        conn.execute(
            "insert into order_item (order_id, product_id, qty_ordered) values (%s, %s, %s)",
            (order_id, products[kind], q),
        )
    conn.execute(
        "insert into trip (trip_id, order_id, seq, status) values (%s, %s, 1, 'RECEIVED')",
        (trip_id, order_id),
    )
    for kind, q in items.items():
        conn.execute(
            "insert into trip_item (trip_id, product_id, qty_ordered) values (%s, %s, %s)",
            (trip_id, products[kind], q),
        )
    for tid in (trip_id, None):
        conn.execute(
            "insert into status_history (order_id, trip_id, from_status, to_status, actor, "
            "account_id) values (%s, %s, NULL, 'RECEIVED', 'USER', %s)",
            (order_id, tid, account_id),
        )
    return trip_id


def trip_of(seeded, conn, items):
    return make_trip(conn, seeded["account_id"], seeded["products"], items)


def rep_for(trip):
    def rep(n, status, **kw):
        base = dict(trip_id=trip, epoch=E, n=n, status=status, reason="", robot_id="pode1",
                    placed={}, missing={})
        base.update(kw)
        return ReportIn(**base)
    return rep


def count(conn, frag, *args) -> int:
    return conn.execute(f"select count(*) from {frag}", args).fetchone()[0]


def order_status(conn, trip) -> str:
    return conn.execute(
        "select o.status from orders o join trip t using (order_id) where t.trip_id = %s", (trip,)
    ).fetchone()[0]


def trip_state(conn, trip):
    return conn.execute(
        "select status, report_epoch, report_n, updated_at from trip where trip_id = %s", (trip,)
    ).fetchone()


def test_normal_flow_applies_four_reports(pool, conn, seeded):  # 완료 기준 2
    t = trip_of(seeded, conn, {"A": 1, "C": 1})
    rep = rep_for(t)
    for n, st in enumerate(["ASSIGNED", "FILLING", "TRANSPORTING", "SHIPPED"], 1):
        placed = {"A": 1, "C": 1} if st in ("TRANSPORTING", "SHIPPED") else {}
        assert apply_report(pool, rep(n, st, placed=placed), D()) == ReportOut(True, "APPLIED")
    row = conn.execute(
        "select status, report_epoch, report_n, finished_at is not null, "
        "placed_at is not null, first_assigned_at is not null from trip "
        "where trip_id=%s", (t,)).fetchone()
    assert row == ("SHIPPED", 7, 4, True, True, True)
    assert count(conn, "status_history where trip_id=%s", t) == 5  # 접수 1 + 보고 4
    assert order_status(conn, t) == "SHIPPED"
    assert count(conn, "ws_event") > 0


def test_same_report_twice_is_duplicate_and_changes_nothing(pool, conn, seeded):  # 완료 기준 3
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    assert apply_report(pool, rep(1, "ASSIGNED"), D()) == ReportOut(True, "APPLIED")
    before = trip_state(conn, t)
    hist = count(conn, "status_history")
    assert apply_report(pool, rep(1, "ASSIGNED"), D()) == ReportOut(True, "DUPLICATE")
    assert trip_state(conn, t) == before
    assert count(conn, "status_history") == hist


def test_smaller_number_is_duplicate(pool, conn, seeded):
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    assert apply_report(pool, rep(1, "ASSIGNED", epoch=8), D()) == ReportOut(True, "APPLIED")
    before = trip_state(conn, t)
    assert apply_report(pool, rep(3, "FILLING"), D()) == ReportOut(True, "DUPLICATE")
    assert trip_state(conn, t) == before


def test_terminal_then_other_status_is_late_report_once(pool, conn, seeded):
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    assert apply_report(pool, rep(4, "SHIPPED", placed={"A": 1}), D()) == ReportOut(True, "APPLIED")
    before = trip_state(conn, t)
    late = ReportOut(True, "LATE_REPORT", "NOT_ALLOWED")
    assert apply_report(pool, rep(5, "FILLING"), D()) == late
    assert apply_report(pool, rep(5, "FILLING"), D()) == late
    assert trip_state(conn, t) == before
    rows = conn.execute(
        "select code, level, source, event_id, trip_id from alert").fetchall()
    assert rows == [("LATE_REPORT", "WARN", "fleet", f"LATE:{t}:7:5", t)]


def test_unknown_trip_is_late_report_not_found(pool, conn, seeded):
    rep = rep_for("O-NONE-1")
    assert apply_report(pool, rep(1, "ASSIGNED"), D()) == \
        ReportOut(True, "LATE_REPORT", "NOT_FOUND")
    row = conn.execute(
        "select trip_id, order_id, detail->>'trip_id', event_id from alert").fetchone()
    assert row == (None, None, "O-NONE-1", "LATE:O-NONE-1:7:1")


@pytest.mark.parametrize("kw", [dict(placed={"A": 2}), dict(placed={"Z": 1}),
                                dict(missing={"A": -1}), dict(n=2**31)])
def test_invalid_values_are_late_report(kw, pool, conn, seeded):
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    before = trip_state(conn, t)
    args = {"n": 1, "status": "FILLING", **kw}
    assert apply_report(pool, rep(**args), D()) == \
        ReportOut(True, "LATE_REPORT", "INVALID_REPORT")
    assert trip_state(conn, t) == before
    assert count(conn, "alert where code = 'LATE_REPORT'") == 1


def test_unknown_reason_applies_and_keeps_value(pool, conn, seeded):
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    assert apply_report(pool, rep(1, "FILLING", reason="DISTANCE_STOP"), D()) == \
        ReportOut(True, "APPLIED")
    row = conn.execute(
        "select reason, detail->>'reported_reason' from status_history "
        "where trip_id = %s and to_status = 'FILLING'", (t,)).fetchone()
    assert row == (None, "DISTANCE_STOP")


def test_db_down_gives_db_error():
    p = make_pool("postgresql://postgres:x@localhost:55999/none", open=True)
    try:
        start = time.monotonic()
        rep = ReportIn("O-X-1", E, 1, "ASSIGNED", "", "pode1", {}, {})
        assert apply_report(p, rep, D()) == ReportOut(False, reason="DB_ERROR")
        assert time.monotonic() - start < 2.5
    finally:
        p.close()


def test_exclusive_7001_gives_db_error_within_deadline(pool, conn, seeded):  # Review Focus 2
    t = trip_of(seeded, conn, {"A": 1})
    rep = rep_for(t)
    with psycopg.connect(pool.conninfo) as holder:  # autocommit 아님: 트랜잭션 안에서 잡음
        holder.execute("select pg_advisory_xact_lock(7001)")
        start = time.monotonic()
        assert apply_report(pool, rep(1, "ASSIGNED"), D()) == ReportOut(False, reason="DB_ERROR")
        assert time.monotonic() - start < 2.5
        holder.rollback()
    assert apply_report(pool, rep(1, "ASSIGNED"), D()) == ReportOut(True, "APPLIED")
