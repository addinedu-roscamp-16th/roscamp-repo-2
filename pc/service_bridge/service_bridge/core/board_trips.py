# ---- central이 켜질 때 주문판을 다시 세우려고 부르는 "끝나지 않은 운반 건" 목록 (설계 6.3, ROS 의존 없음) ----
import logging
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg_pool import ConnectionPool

from service_bridge.core.retry import run_tx_guarded
from service_bridge.core.transitions import TERMINAL

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoardTrip:
    trip_id: str
    order_id: str
    trip_index: int            # trip.seq (1부터)
    trip_count: int            # 같은 주문의 운반 건 수
    items: dict[str, int]      # kind -> 주문 수
    status: str
    reason: str                # 이 상태로 바뀔 때의 이력 사유. 없으면 ""
    robot_id: str              # 맡은 Pode, 없으면 ""
    placed: dict[str, int]     # kind -> 담은 수 (0인 상품은 뺌)
    missing: dict[str, int]    # kind -> 못 담은 수 (0인 상품은 뺌)
    submitted_at: datetime     # trip.created_at
    report_epoch: int          # 마지막 반영 보고 번호. 아직 없으면 0
    report_n: int


@dataclass(frozen=True)
class BoardTripsOut:
    accepted: bool             # false는 DB 오류뿐
    trips: list[BoardTrip]
    reason: str = ""           # DB_ERROR


def list_board_trips(pool: ConnectionPool, deadline: float) -> BoardTripsOut:
    """끝 상태가 아니고 central이 거절하지 않은 운반 건을 접수 순서(created_at, trip_id)로 돌려준다.

    central에 아직 못 넘긴 건(central_acked_at IS NULL)도 넣는다: 앞 central이 받았는지 모르므로
    뒤이어 SubmitTrip이 오면 central이 같은 trip_id를 중복으로 처리한다. DB 오류는 DB_ERROR.
    """
    def on_error(e: Exception) -> BoardTripsOut:
        log.error("목록 읽기 실패 (DB 오류): %s: %s", type(e).__name__, e)
        return BoardTripsOut(False, [], "DB_ERROR")

    return run_tx_guarded(pool, lambda conn: BoardTripsOut(True, _read(conn)), deadline, on_error)


def _read(conn: psycopg.Connection) -> list[BoardTrip]:
    # 운반 건 · 품목이 서로 다른 시점 것이 섞이지 않게 한 스냅샷으로 읽는다(첫 문장이어야 함)
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    rows = conn.execute(
        "SELECT t.trip_id, t.order_id, t.seq, "
        "(SELECT count(*) FROM trip t2 WHERE t2.order_id = t.order_id), "
        "t.status, "
        "coalesce((SELECT h.reason FROM status_history h "
        "          WHERE h.trip_id = t.trip_id AND h.to_status = t.status "
        "          ORDER BY h.history_id DESC LIMIT 1), ''), "
        "coalesce(t.robot_id, ''), t.created_at, "
        "coalesce(t.report_epoch, 0), coalesce(t.report_n, 0) "
        "FROM trip t JOIN trip_dispatch d USING (trip_id) "
        "WHERE t.status <> ALL(%s) "
        "AND NOT (d.central_acked_at IS NOT NULL AND d.last_result <> 'ACCEPTED') "
        "ORDER BY t.created_at, t.trip_id", (sorted(TERMINAL),)).fetchall()
    if not rows:
        return []
    qty: dict[str, list[tuple[str, int, int, int]]] = {}
    for trip_id, kind, ordered, placed, missing in conn.execute(
            "SELECT ti.trip_id, p.kind, ti.qty_ordered, ti.qty_placed, ti.qty_missing "
            "FROM trip_item ti JOIN product p USING (product_id) "
            "WHERE ti.trip_id = ANY(%s) ORDER BY p.kind", ([r[0] for r in rows],)).fetchall():
        qty.setdefault(trip_id, []).append((kind, ordered, placed, missing))
    out = []
    for (trip_id, order_id, seq, count, status, reason, robot, created, epoch, n) in rows:
        lines = qty.get(trip_id, [])
        out.append(BoardTrip(
            trip_id, order_id, seq, count,
            {k: o for k, o, _, _ in lines}, status, reason, robot,
            {k: p for k, _, p, _ in lines if p}, {k: m for k, _, _, m in lines if m},
            created, epoch, n))
    return out
