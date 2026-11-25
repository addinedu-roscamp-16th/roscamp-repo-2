"""fleet ReportTrip 반영 (F-09, 설계 6.4). 한 트랜잭션, ROS 의존 없음."""
import dataclasses
import logging

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

from service_bridge.core.retry import run_tx
from service_bridge.core.transitions import (
    STATUSES, TERMINAL, TRIP_REASONS, is_allowed, order_summary,
)
from service_bridge.core.types import ReportIn, ReportOut

log = logging.getLogger(__name__)

LOCK_LEDGER = 7001
INT_MIN, INT_MAX = -(2**31), 2**31 - 1


def apply_report(pool: ConnectionPool, rep: ReportIn, deadline: float) -> ReportOut:
    """보고 하나를 반영하고 커밋한 뒤 응답 값을 돌려준다. DB 오류는 accepted=false, DB_ERROR."""
    try:
        return run_tx(pool, lambda conn: _apply(conn, rep), deadline)
    except (psycopg.Error, PoolTimeout, PoolClosed) as e:
        log.error("보고 반영 실패 (DB 오류): %s (%s, %s) %s: %s",
                  rep.trip_id, rep.epoch, rep.n, rep.status, e)
        return ReportOut(False, reason="DB_ERROR")


def _apply(conn: psycopg.Connection, rep: ReportIn) -> ReportOut:
    # 1. 잠금 없이 order_id
    row = conn.execute("SELECT order_id FROM trip WHERE trip_id = %s", (rep.trip_id,)).fetchone()
    if row is None:
        return _late(conn, rep, None, "NOT_FOUND")
    order_id = row[0]

    # 2. 잠금 순서 (SB-05): 7001 공유 → orders → trip
    conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (LOCK_LEDGER,))
    order_cur = conn.execute(
        "SELECT status FROM orders WHERE order_id = %s FOR UPDATE", (order_id,)).fetchone()[0]
    cur, last_epoch, last_n, placed_at = conn.execute(
        "SELECT status, report_epoch, report_n, placed_at FROM trip WHERE trip_id = %s FOR UPDATE",
        (rep.trip_id,)).fetchone()

    # 3. 번호 비교 (SB-01): NULL은 무엇보다 작음
    if last_epoch is not None and (rep.epoch, rep.n) <= (last_epoch, last_n):
        return ReportOut(True, "DUPLICATE")

    # 4. 값 검사
    items = {kind: (pid, ordered, placed, missing) for kind, pid, ordered, placed, missing in
             conn.execute(
                 "SELECT p.kind, ti.product_id, ti.qty_ordered, ti.qty_placed, ti.qty_missing "
                 "FROM trip_item ti JOIN product p USING (product_id) WHERE ti.trip_id = %s",
                 (rep.trip_id,)).fetchall()}
    new_qty = _check_values(rep, items)
    if new_qty is None:
        return _late(conn, rep, order_id, "INVALID_REPORT")

    # 5. 허용 전이 (SB-03)
    if not is_allowed(cur, rep.status):
        return _late(conn, rep, order_id, "NOT_ALLOWED")

    # 6. 반영
    for pid, placed, missing in new_qty:
        conn.execute(
            "UPDATE trip_item SET qty_placed = %s, qty_missing = %s "
            "WHERE trip_id = %s AND product_id = %s",
            (placed, missing, rep.trip_id, pid))
    set_placed = bool(rep.placed) and placed_at is None

    if rep.status == cur:
        conn.execute(
            "UPDATE trip SET report_epoch = %s, report_n = %s, "
            "placed_at = CASE WHEN %s THEN now() ELSE placed_at END WHERE trip_id = %s",
            (rep.epoch, rep.n, set_placed, rep.trip_id))
        return ReportOut(True, "APPLIED")

    robot_id = rep.robot_id or None
    conn.execute(
        "UPDATE trip SET status = %s, robot_id = %s, report_epoch = %s, report_n = %s, "
        "placed_at = CASE WHEN %s THEN now() ELSE placed_at END, "
        "first_assigned_at = CASE WHEN %s THEN coalesce(first_assigned_at, now()) "
        "ELSE first_assigned_at END, "
        "finished_at = CASE WHEN %s THEN now() ELSE finished_at END "
        "WHERE trip_id = %s",
        (rep.status, robot_id, rep.epoch, rep.n, set_placed, rep.status == "ASSIGNED",
         rep.status in TERMINAL, rep.trip_id))

    reason, detail = _history_reason(rep)
    conn.execute(
        "INSERT INTO status_history (order_id, trip_id, from_status, to_status, reason, "
        "robot_id, actor, detail) VALUES (%s, %s, %s, %s, %s, %s, 'SYSTEM', %s)",
        (order_id, rep.trip_id, cur, rep.status, reason, robot_id, detail))

    # 주문 요약 다시 계산 (DB 설계 4.4)
    statuses = [s for (s,) in conn.execute(
        "SELECT status FROM trip WHERE order_id = %s", (order_id,)).fetchall()]
    order_new = order_summary(statuses)
    if order_new != order_cur:
        conn.execute(
            "UPDATE orders SET status = %s, "
            "finished_at = CASE WHEN %s THEN now() ELSE finished_at END WHERE order_id = %s",
            (order_new, order_new in TERMINAL, order_id))
        conn.execute(
            "INSERT INTO status_history (order_id, trip_id, from_status, to_status, actor) "
            "VALUES (%s, NULL, %s, %s, 'SYSTEM')",
            (order_id, order_cur, order_new))
    return ReportOut(True, "APPLIED")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_values(rep: ReportIn, items: dict) -> list[tuple[int, int, int]] | None:
    """값 검사(설계 6.4 4). 바르면 고칠 (product_id, 담은 수, 못 담은 수) 목록, 어기면 None.

    보고에 없는 상품은 지금 수량을 그대로 둔다.
    """
    if rep.status not in STATUSES:
        return None
    if not all(_is_int(v) and INT_MIN <= v <= INT_MAX for v in (rep.epoch, rep.n)):
        return None
    for qty in (rep.placed, rep.missing):
        for kind, v in qty.items():
            if kind not in items or not _is_int(v) or v < 0:
                return None
    out = []
    for kind in set(rep.placed) | set(rep.missing):
        pid, ordered, placed, missing = items[kind]
        placed = rep.placed.get(kind, placed)
        missing = rep.missing.get(kind, missing)
        if placed + missing > ordered:
            return None
        out.append((pid, placed, missing))
    return out


def _history_reason(rep: ReportIn) -> tuple[str | None, Jsonb | None]:
    if not rep.reason:
        return None, None
    if rep.reason in TRIP_REASONS:
        return rep.reason, None
    log.warning("목록 밖 운반 건 사유 %r: %s 상태 %s는 반영, 이력 사유는 비움",
                rep.reason, rep.trip_id, rep.status)
    return None, Jsonb({"reported_reason": rep.reason})


def _late(conn: psycopg.Connection, rep: ReportIn, order_id: str | None,
          why: str) -> ReportOut:
    """LATE_REPORT 알림을 연다(같은 보고는 event_id로 하나만). 상태 · 수량 · 번호는 그대로."""
    found = order_id is not None
    detail = {**dataclasses.asdict(rep), "late_reason": why}
    log.warning("늦은 보고 %s: %s (%s, %s) %s", why, rep.trip_id, rep.epoch, rep.n, rep.status)
    conn.execute(
        "INSERT INTO alert (level, code, event_id, source, robot_id, order_id, trip_id, "
        "message, detail) VALUES ('WARN', 'LATE_REPORT', %s, 'fleet', %s, %s, %s, %s, %s) "
        "ON CONFLICT (event_id) DO NOTHING",
        (f"LATE:{rep.trip_id}:{rep.epoch}:{rep.n}", rep.robot_id or None, order_id,
         rep.trip_id if found else None,
         f"반영하지 않은 운반 건 보고 ({why}): {rep.trip_id} {rep.status}", Jsonb(detail)))
    return ReportOut(True, "LATE_REPORT", why)
