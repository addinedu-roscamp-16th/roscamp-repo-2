# ---- 운반 건 넘기기 기록 · 5번 실패 뒤 쉼 · DISPATCH_STALLED 알림 (설계 6.2, 6.5, ROS 의존 없음) ----
import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import psycopg

FAST_TRIES = 5      # 이 횟수째 실패부터 쉼
RETRY_SEC = 1.0     # 1 ~ 4번째 실패 뒤 다시 보내기까지 (IR-40)
JITTER = 0.2        # ±20% (IR-41)
REST_SEC = 30.0     # TODO(SB-12): 결과 모름 처리를 정하기 전의 임시 값

log = logging.getLogger("service_bridge.dispatch")

STALLED_MESSAGE = "운반 건을 central에 넘기지 못하고 있습니다 (뒤의 운반 건도 기다림)"


@dataclass(frozen=True)
class DispatchItem:
    trip_id: str
    order_id: str
    trip_index: int
    trip_count: int
    items: dict[str, int]            # kind -> count (OrderItem)
    submitted_at: datetime           # trip.created_at
    attempts: int
    next_attempt_at: datetime | None


# ---- 맨 앞 빈 줄 (접수 순서) ----
def head_item(conn: psycopg.Connection) -> DispatchItem | None:
    """central 확정 답 전인 줄 중 가장 이른 것 하나. next_attempt_at(쉼)은 호출한 쪽이 본다."""
    with conn.transaction():
        row = conn.execute(
            "SELECT t.trip_id, t.order_id, t.seq, t.created_at, d.attempts, d.next_attempt_at, "
            "(SELECT count(*) FROM trip t2 WHERE t2.order_id = t.order_id) "
            "FROM trip_dispatch d JOIN trip t USING (trip_id) "
            "WHERE d.central_acked_at IS NULL "
            "ORDER BY t.created_at, t.trip_id LIMIT 1").fetchone()
        if row is None:
            return None
        trip_id, order_id, seq, created_at, attempts, next_at, count = row
        items = dict(conn.execute(
            "SELECT p.kind, ti.qty_ordered FROM trip_item ti JOIN product p USING (product_id) "
            "WHERE ti.trip_id = %s ORDER BY p.kind", (trip_id,)).fetchall())
    return DispatchItem(trip_id, order_id, seq, count, items, created_at, attempts, next_at)


# ---- 확정 답 ----
def record_ack(conn: psycopg.Connection, trip_id: str, accepted: bool, reason: str) -> None:
    result = "ACCEPTED" if accepted else reason
    with conn.transaction():
        conn.execute("SELECT 1 FROM trip_dispatch WHERE trip_id = %s FOR UPDATE", (trip_id,))
        # 확정 답을 받으면 결과 모름도 풀린다(주문 DB 설계 4.16)
        conn.execute(
            "UPDATE trip_dispatch SET central_acked_at = now(), last_result = %s, "
            "attempts = attempts + 1, unknown_since = NULL, next_attempt_at = NULL "
            "WHERE trip_id = %s", (result, trip_id))
        conn.execute(
            "UPDATE alert SET status = 'RESOLVED', resolved_at = now() "
            "WHERE code = 'DISPATCH_STALLED' AND trip_id = %s AND status = 'OPEN'", (trip_id,))


# ---- 실패 (NOT_READY · 시간 초과 · 서비스 없음) ----
def record_failure(conn: psycopg.Connection, trip_id: str, result: str) -> int:
    """attempts를 하나 올리고 바뀐 값을 돌려준다. FAST_TRIES 이상이면 30초 쉼 + 알림."""
    with conn.transaction():
        conn.execute("SELECT 1 FROM trip_dispatch WHERE trip_id = %s FOR UPDATE", (trip_id,))
        attempts = conn.execute(
            "UPDATE trip_dispatch SET attempts = attempts + 1, last_result = %s "
            "WHERE trip_id = %s RETURNING attempts", (result, trip_id)).fetchone()[0]
        if attempts >= FAST_TRIES:
            _rest_and_alert(conn, trip_id, attempts, result)
    return attempts


def _rest_and_alert(conn: psycopg.Connection, trip_id: str, attempts: int, result: str) -> None:
    unknown_since = conn.execute(
        "UPDATE trip_dispatch SET unknown_since = coalesce(unknown_since, now()), "
        "next_attempt_at = now() + make_interval(secs => %s) "
        "WHERE trip_id = %s RETURNING unknown_since", (REST_SEC, trip_id)).fetchone()[0]
    order_id = conn.execute("SELECT order_id FROM trip WHERE trip_id = %s",
                            (trip_id,)).fetchone()[0]
    # 맨 앞 건 뒤에서 기다리는 빈 줄 수
    waiting = conn.execute(
        "SELECT count(*) FROM trip_dispatch WHERE central_acked_at IS NULL AND trip_id <> %s",
        (trip_id,)).fetchone()[0]
    detail = {"attempts": attempts, "last_result": result,
              "unknown_since": unknown_since.isoformat(), "waiting_trips": waiting}
    # 이미 열린 알림이 있으면 detail만 합쳐 고친다 (unknown_since는 처음 값 그대로)
    conn.execute(
        "INSERT INTO alert (level, code, source, order_id, trip_id, message, detail) "
        "VALUES ('WARN', 'DISPATCH_STALLED', 'service_bridge', %s, %s, %s, %s) "
        "ON CONFLICT (code, trip_id) WHERE status = 'OPEN' AND code = 'DISPATCH_STALLED' "
        "DO UPDATE SET detail = alert.detail || EXCLUDED.detail",
        (order_id, trip_id, STALLED_MESSAGE, json.dumps(detail)))
    log.warning("운반 건 넘기기 멈춤: trip_id=%s attempts=%d last_result=%s waiting_trips=%d",
                trip_id, attempts, result, waiting)


# ---- 다시 보내기까지 ----
def retry_delay(attempts: int, rand: Callable[[], float] = random.random) -> float | None:
    """1 ~ 4번째 실패 뒤의 대기 초. 쉼(FAST_TRIES 이상)은 None: next_attempt_at으로 한다."""
    if attempts >= FAST_TRIES:
        return None
    return RETRY_SEC * (1 + JITTER * (2 * rand() - 1))
