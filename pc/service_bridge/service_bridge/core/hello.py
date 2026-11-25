# ---- fleet 시작 Hello: epoch 발급과 진행 중 운반 건 보류 (F-06, 한 트랜잭션, ROS 의존 없음) ----
import logging

import psycopg
from psycopg_pool import ConnectionPool

from service_bridge.core.order_sync import recompute_order_summary
from service_bridge.core.retry import run_tx_guarded
from service_bridge.core.transitions import ACTIVE
from service_bridge.core.types import HelloIn, HelloOut

log = logging.getLogger(__name__)

NODE = "fleet"  # 이번 조각에서 Hello를 받는 유일한 node 값


# ---- 서버 콜백이 부르는 입구 ----
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


# ---- epoch 정하기 ----
def _apply(conn: psycopg.Connection, hello: HelloIn) -> HelloOut:
    # 세션 줄을 가장 먼저 잠가 Hello끼리 줄을 세운다.
    # 줄이 없으면 epoch 1로 만들고, 동시에 만든 쪽이 있으면 그 줄을 잠근다
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

    # 같은 boot_id: 응답을 못 받아 다시 보낸 Hello(재연결). epoch를 올리면 그 뒤 보고가
    # 옛 epoch로 버려지므로 그대로 돌려준다 (F-06, TODO(Q-09))
    if boot_id == hello.boot_id:
        return HelloOut(True, epoch)

    # 다른 boot_id: fleet이 새로 켜짐. 새 epoch
    new_epoch = epoch + 1
    conn.execute(
        "UPDATE bridge_session SET epoch = %s, boot_id = %s, hello_at = now() WHERE node = %s",
        (new_epoch, hello.boot_id, hello.node))
    # 죽은 fleet이 몰던 진행 중 건 보류 (F-06)
    _hold_active(conn, new_epoch)
    return HelloOut(True, new_epoch)


# ---- 잠금 ----
def _lock_session(conn: psycopg.Connection, node: str) -> tuple[int, str] | None:
    return conn.execute(
        "SELECT epoch, boot_id FROM bridge_session WHERE node = %s FOR UPDATE", (node,)
    ).fetchone()


def _lock_orders(conn: psycopg.Connection, order_ids: list[str]) -> dict[str, str]:
    # order_id 순으로 잠가 다른 트랜잭션과 순서를 맞춘다. 잠근 주문 -> 그때 상태
    return dict(conn.execute(
        "SELECT order_id, status FROM orders WHERE order_id = ANY(%s) ORDER BY order_id "
        "FOR UPDATE", (order_ids,)).fetchall())


# ---- 진행 중 운반 건 보류 ----
def _hold_active(conn: psycopg.Connection, epoch: int) -> None:
    """진행 중(ACTIVE)인 운반 건 모두를 HELD(FLEET_RESTART, actor SYSTEM)로, 번호는 (epoch, 0).

    번호를 (새 epoch, 0)으로 두면 죽은 fleet의 옛 epoch 보고는 DUPLICATE로 걸러지고,
    새 fleet의 첫 보고 (새 epoch, 1)부터 반영된다.
    잠금은 bridge_session → 주문(order_id 순) → trip(trip_id 순). 원장에 쓰지 않아 7001은 없다.
    """
    # 주문을 잠그는 사이 다른 트랜잭션이 새로 ACTIVE로 만든 건이 있을 수 있다.
    # 그 건의 주문 잠금을 빠뜨리지 않으려고, 새 주문이 안 나올 때까지 후보를 다시 읽는다.
    # 잠근 주문의 건은 남이 못 바꾸므로(보고도 orders를 먼저 잠금) 그 뒤 대상은 늘지 않는다
    active = sorted(ACTIVE)
    orders: dict[str, str] = {}  # 잠근 주문 -> 잠글 때의 상태
    while True:
        new_ids = [oid for (oid,) in conn.execute(
            "SELECT DISTINCT order_id FROM trip WHERE status = ANY(%s) ORDER BY order_id",
            (active,)).fetchall() if oid not in orders]
        if not new_ids:
            break
        orders.update(_lock_orders(conn, new_ids))
    # 잠근 주문 안의 진행 중 건만 trip_id 순으로 잠근다
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
