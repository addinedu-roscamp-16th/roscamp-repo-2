# ---- fleet ReportTrip을 한 트랜잭션으로 DB에 반영 (F-09, ROS 의존 없음) ----
import dataclasses
import logging

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from service_bridge.core.order_sync import recompute_order_summary
from service_bridge.core.retry import run_tx_guarded
from service_bridge.core.transitions import (
    STATUSES,
    TERMINAL,
    TRIP_REASONS,
    is_allowed,
)
from service_bridge.core.types import ReportIn, ReportOut

log = logging.getLogger(__name__)

# 원장 잠금 키. 이번 조각은 원장에 쓰지 않지만 출고 차감이 들어와도 순서가 같게 처음부터 잡는다
LOCK_LEDGER = 7001
INT_MIN, INT_MAX = -(2**31), 2**31 - 1


# ---- 서버 콜백이 부르는 입구 ----
def apply_report(pool: ConnectionPool, rep: ReportIn, deadline: float) -> ReportOut:
    """보고 하나를 반영하고 커밋한 뒤 응답 값을 돌려준다. DB 오류는 accepted=false, DB_ERROR.

    반영 · 중복 · 늦은 보고는 모두 accepted=true(fleet이 다시 보내지 않게, IR-24).
    accepted=false는 DB 오류뿐이고, fleet이 같은 번호로 다시 보낸다.
    """
    def on_error(e: Exception) -> ReportOut:
        log.error("보고 반영 실패 (DB 오류): %s (%s, %s) %s: %s",
                  rep.trip_id, rep.epoch, rep.n, rep.status, e)
        return ReportOut(False, reason="DB_ERROR")

    return run_tx_guarded(pool, lambda conn: _apply(conn, rep), deadline, on_error)


# ---- 보고 판정과 반영 (한 트랜잭션) ----
def _apply(conn: psycopg.Connection, rep: ReportIn) -> ReportOut:
    # 1. 잠금 없이 order_id (orders를 trip보다 먼저 잠그려면 order_id가 먼저 있어야 함)
    row = conn.execute("SELECT order_id FROM trip WHERE trip_id = %s", (rep.trip_id,)).fetchone()
    if row is None:
        return _late(conn, rep, None, "NOT_FOUND")
    order_id = row[0]

    # 2. 잠금 순서 (SB-05): 7001 공유 → orders → trip.
    # 원장에 쓸 수 있는 트랜잭션은 모두 이 순서로 잡으므로 서로 교착하지 않는다
    conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (LOCK_LEDGER,))
    order_cur = conn.execute(
        "SELECT status FROM orders WHERE order_id = %s FOR UPDATE", (order_id,)).fetchone()[0]
    cur, last_epoch, last_n, placed_at = conn.execute(
        "SELECT status, report_epoch, report_n, placed_at FROM trip WHERE trip_id = %s FOR UPDATE",
        (rep.trip_id,)).fetchone()

    # 3. 번호 비교 (SB-01): 같거나 작은 (epoch, n)은 아무것도 바꾸지 않고 DUPLICATE.
    # 재전송이나 재시작 전의 늦은 보고다. 번호가 아직 없으면(NULL) 무엇보다 작다
    if last_epoch is not None and (rep.epoch, rep.n) <= (last_epoch, last_n):
        return ReportOut(True, "DUPLICATE")

    # 4. 값 검사: 잘못된 값은 반영하지 않고 LATE_REPORT(INVALID_REPORT)
    items = {kind: (pid, ordered, placed, missing) for kind, pid, ordered, placed, missing in
             conn.execute(
                 "SELECT p.kind, ti.product_id, ti.qty_ordered, ti.qty_placed, ti.qty_missing "
                 "FROM trip_item ti JOIN product p USING (product_id) WHERE ti.trip_id = %s",
                 (rep.trip_id,)).fetchall()}
    new_qty = _check_values(rep, items)
    if new_qty is None:
        return _late(conn, rep, order_id, "INVALID_REPORT")

    # 5. 허용 전이 (SB-03): 번호는 크지만 허용되지 않는 전이는 덮어쓰지 않고 LATE_REPORT
    if not is_allowed(cur, rep.status):
        return _late(conn, rep, order_id, "NOT_ALLOWED")

    # 6. 반영: 수량은 보고에 없는 상품까지 모든 행을 고친다
    for pid, placed, missing in new_qty:
        conn.execute(
            "UPDATE trip_item SET qty_placed = %s, qty_missing = %s "
            "WHERE trip_id = %s AND product_id = %s",
            (placed, missing, rep.trip_id, pid))
    set_placed = bool(rep.placed) and placed_at is None  # 처음 담은 시각만 남긴다

    # 같은 상태: 번호 · 수량만 고치고 이력은 쓰지 않는다
    if rep.status == cur:
        conn.execute(
            "UPDATE trip SET report_epoch = %s, report_n = %s, "
            "placed_at = CASE WHEN %s THEN now() ELSE placed_at END WHERE trip_id = %s",
            (rep.epoch, rep.n, set_placed, rep.trip_id))
        return ReportOut(True, "APPLIED")

    # 상태가 바뀜: trip · 이력 · 주문 요약
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

    recompute_order_summary(conn, order_id, order_cur)
    return ReportOut(True, "APPLIED")


# ---- 값 검사 ----
def _is_int(v) -> bool:
    # bool은 int의 하위형이라 따로 뺀다
    return isinstance(v, int) and not isinstance(v, bool)


def _check_values(rep: ReportIn, items: dict) -> list[tuple[int, int, int]] | None:
    """값 검사(설계 6.4 4). 바르면 고칠 (product_id, 담은 수, 못 담은 수) 목록, 어기면 None.

    보고는 지금까지의 전체 수량을 싣는다(IR-23). 보고에 없는 상품은 0으로 보고 모든 행을 고친다.
    """
    if rep.status not in STATUSES:
        return None
    # DB 칸이 integer라 그 범위를 넘으면 쓰기 전에 거른다
    if not all(_is_int(v) and INT_MIN <= v <= INT_MAX for v in (rep.epoch, rep.n)):
        return None
    # 그 운반 건에 없는 상품 · 음수 · 정수 아님
    for qty in (rep.placed, rep.missing):
        for kind, v in qty.items():
            if kind not in items or not _is_int(v) or v < 0:
                return None
    # 담은 수 + 못 담은 수는 주문 수를 넘지 못한다
    out = []
    for kind, (pid, ordered, _, _) in items.items():
        placed = rep.placed.get(kind, 0)
        missing = rep.missing.get(kind, 0)
        if placed + missing > ordered:
            return None
        out.append((pid, placed, missing))
    return out


# ---- 이력 사유 ----
def _history_reason(rep: ReportIn) -> tuple[str | None, Jsonb | None]:
    """(이력 reason, detail). 목록 밖 사유는 이력 CHECK에 걸리므로 reason은 비우고 detail에 남긴다.

    사유 하나 때문에 상태 반영을 막지 않는다. 대신 WARN 로그를 낸다.
    """
    if not rep.reason:
        return None, None
    if rep.reason in TRIP_REASONS:
        return rep.reason, None
    log.warning("목록 밖 운반 건 사유 %r: %s 상태 %s는 반영, 이력 사유는 비움",
                rep.reason, rep.trip_id, rep.status)
    return None, Jsonb({"reported_reason": rep.reason})


# ---- 늦은 보고 알림 ----
def _late(conn: psycopg.Connection, rep: ReportIn, order_id: str | None,
          why: str) -> ReportOut:
    """LATE_REPORT 알림을 연다. 상태 · 수량 · 번호는 그대로.

    why는 NOT_FOUND(모르는 건) · INVALID_REPORT(잘못된 값) · NOT_ALLOWED(허용 안 되는 전이).
    번호를 올리지 않으므로 같은 보고가 다시 올 수 있어, event_id "LATE:{trip}:{e}:{n}"로
    알림을 하나만 연다. alert.trip_id는 trip을 참조하므로 모르는 건이면 비운다.
    """
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
