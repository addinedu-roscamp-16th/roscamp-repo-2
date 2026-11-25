# ---- 운반 건 상태가 바뀐 뒤 주문 요약을 다시 써 두는 공통 단계 (보고 · Hello) ----
import psycopg

from service_bridge.core.transitions import TERMINAL, order_summary


def recompute_order_summary(conn: psycopg.Connection, order_id: str, order_cur: str) -> None:
    """주문 요약 다시 계산 (DB 설계 4.4). 바뀌면 `orders`를 고치고 trip_id 없는 이력 한 줄을 쓴다.

    호출한 쪽이 그 주문 행을 `FOR UPDATE`로 잡고 있어야 한다(`order_cur`는 잠글 때 읽은 값).
    """
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
