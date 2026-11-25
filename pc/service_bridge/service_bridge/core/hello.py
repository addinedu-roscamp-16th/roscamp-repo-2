"""fleet 시작 Hello: epoch 발급과 진행 중 운반 건 보류 (F-06, 설계 6.3). 한 트랜잭션, ROS 의존 없음."""
import logging

import psycopg
from psycopg_pool import ConnectionPool

from service_bridge.core.order_sync import recompute_order_summary
from service_bridge.core.retry import run_tx_guarded
from service_bridge.core.transitions import ACTIVE
from service_bridge.core.types import HelloIn, HelloOut

log = logging.getLogger(__name__)

NODE = "fleet"


def apply_hello(pool: ConnectionPool, hello: HelloIn, deadline: float) -> HelloOut:
    """Hello를 반영하고 커밋한 뒤 응답 값을 돌려준다. DB 오류는 accepted=false, DB_ERROR."""
    if hello.node != NODE:
        return HelloOut(False, reason="UNKNOWN_NODE")
    if not hello.boot_id:
        return HelloOut(False, reason="INVALID_REQUEST")

    def on_error(e: Exception) -> HelloOut:
        log.error("Hello 반영 실패 (DB 오류): %s %s: %s", hello.node, hello.boot_id, e)
        return HelloOut(False, reason="DB_ERROR")

    return run_tx_guarded(pool, lambda conn: _apply(conn, hello), deadline, on_error)


def _apply(conn: psycopg.Connection, hello: HelloIn) -> HelloOut:
    # 2. 세션 줄 잠금 (없으면 epoch 1로 만든다. 동시에 만든 쪽이 있으면 그 줄을 잠근다)
    row = _lock_session(conn, hello.node)
    if row is None:
        inserted = conn.execute(
            "INSERT INTO bridge_session (node, epoch, boot_id) VALUES (%s, 1, %s) "
            "ON CONFLICT (node) DO NOTHING RETURNING epoch", (hello.node, hello.boot_id)
        ).fetchone()
        if inserted is not None:
            _hold_active(conn, inserted[0])
            return HelloOut(True, inserted[0])
        row = _lock_session(conn, hello.node)
    epoch, boot_id = row

    # 3. 같은 boot_id: 다시 보낸 Hello, epoch 그대로 (TODO(Q-09))
    if boot_id == hello.boot_id:
        return HelloOut(True, epoch)

    # 4. 새 epoch
    new_epoch = epoch + 1
    conn.execute(
        "UPDATE bridge_session SET epoch = %s, boot_id = %s, hello_at = now() WHERE node = %s",
        (new_epoch, hello.boot_id, hello.node))
    # 5. 진행 중 운반 건 보류
    _hold_active(conn, new_epoch)
    return HelloOut(True, new_epoch)


def _lock_session(conn: psycopg.Connection, node: str) -> tuple[int, str] | None:
    return conn.execute(
        "SELECT epoch, boot_id FROM bridge_session WHERE node = %s FOR UPDATE", (node,)
    ).fetchone()


def _lock_orders(conn: psycopg.Connection, order_ids: list[str]) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT order_id, status FROM orders WHERE order_id = ANY(%s) ORDER BY order_id "
        "FOR UPDATE", (order_ids,)).fetchall())


def _hold_active(conn: psycopg.Connection, epoch: int) -> None:
    """진행 중인 운반 건 모두를 HELD로, 번호는 (epoch, 0). 잠금은 orders → trip 순 (SB-05).

    진행 중인 건의 주문을 모두 잠글 때까지 후보를 다시 읽는다. 잠근 주문의 건은 다른 트랜잭션이
    바꿀 수 없으므로(보고도 orders를 먼저 잠금), 그 뒤에는 trip 잠금 대상이 더 늘지 않는다.
    """
    active = sorted(ACTIVE)
    orders: dict[str, str] = {}  # 잠근 주문 -> 잠글 때의 상태
    while True:
        new_ids = [oid for (oid,) in conn.execute(
            "SELECT DISTINCT order_id FROM trip WHERE status = ANY(%s) ORDER BY order_id",
            (active,)).fetchall() if oid not in orders]
        if not new_ids:
            break
        orders.update(_lock_orders(conn, new_ids))
    trips = conn.execute(
        "SELECT trip_id, order_id, status, robot_id FROM trip "
        "WHERE status = ANY(%s) AND order_id = ANY(%s) ORDER BY trip_id FOR UPDATE",
        (active, list(orders))).fetchall()
    for trip_id, order_id, status, robot_id in trips:
        conn.execute(
            "UPDATE trip SET status = 'HELD', report_epoch = %s, report_n = 0 "
            "WHERE trip_id = %s", (epoch, trip_id))
        conn.execute(
            "INSERT INTO status_history (order_id, trip_id, from_status, to_status, reason, "
            "robot_id, actor) VALUES (%s, %s, %s, 'HELD', 'FLEET_RESTART', %s, 'SYSTEM')",
            (order_id, trip_id, status, robot_id))
    for order_id, order_cur in orders.items():
        recompute_order_summary(conn, order_id, order_cur)
    if trips:
        log.warning("fleet 재시작: 진행 중 운반 건 %d개를 보류 (epoch %d)", len(trips), epoch)
