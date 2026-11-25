"""웹 대신 주문 · 운반 건 하나를 넣는다 (설계 8.2 1). 나누지 않는다."""
import datetime

import psycopg


def insert_trip(conn: psycopg.Connection, account_id: int,
                items: dict[str, int]) -> tuple[str, str]:
    """주문 · 운반 건(seq 1, RECEIVED)과 접수 이력 두 줄을 한 트랜잭션에 넣고 (order_id, trip_id).

    품목이 비었거나 개수가 1보다 작거나 모르는 상품이면, 또는 합이 basket_capacity를 넘으면
    ValueError.
    """
    if not items or any(q < 1 for q in items.values()):
        raise ValueError(f"품목이 비었거나 개수가 1보다 작음: {items}")
    prefix = f"O-{datetime.date.today():%Y%m%d}-"
    with conn.transaction():
        capacity = conn.execute(
            "SELECT value FROM app_setting WHERE key = 'basket_capacity'").fetchone()[0]
        if sum(items.values()) > capacity:
            raise ValueError(f"품목 합 {sum(items.values())}이 바구니 용량 {capacity}을 넘음")
        products = dict(conn.execute(
            "SELECT kind, product_id FROM product WHERE kind = ANY(%s)",
            (list(items),)).fetchall())
        unknown = set(items) - set(products)
        if unknown:
            raise ValueError(f"모르는 상품: {sorted(unknown)}")
        last = conn.execute(
            "SELECT max(order_id) FROM orders WHERE order_id LIKE %s",
            (prefix + "%",)).fetchone()[0]
        order_id = f"{prefix}{int(last[-4:]) + 1 if last else 1:04d}"
        trip_id = f"{order_id}-1"
        conn.execute(
            "INSERT INTO orders (order_id, account_id, status) VALUES (%s, %s, 'RECEIVED')",
            (order_id, account_id))
        conn.execute(
            "INSERT INTO trip (trip_id, order_id, seq, status) VALUES (%s, %s, 1, 'RECEIVED')",
            (trip_id, order_id))
        for kind, q in items.items():
            conn.execute(
                "INSERT INTO order_item (order_id, product_id, qty_ordered) VALUES (%s, %s, %s)",
                (order_id, products[kind], q))
            conn.execute(
                "INSERT INTO trip_item (trip_id, product_id, qty_ordered) VALUES (%s, %s, %s)",
                (trip_id, products[kind], q))
        for tid in (trip_id, None):  # 운반 건 · 주문
            conn.execute(
                "INSERT INTO status_history (order_id, trip_id, from_status, to_status, actor, "
                "account_id) VALUES (%s, %s, NULL, 'RECEIVED', 'USER', %s)",
                (order_id, tid, account_id))
    return order_id, trip_id


def trip_items(conn: psycopg.Connection, trip_id: str) -> tuple[str, dict[str, int]]:
    """이미 넣은 운반 건의 (order_id, {kind: 주문 수}). 없으면 LookupError."""
    row = conn.execute("SELECT order_id FROM trip WHERE trip_id = %s", (trip_id,)).fetchone()
    if row is None:
        raise LookupError(f"운반 건 없음: {trip_id}")
    items = dict(conn.execute(
        "SELECT p.kind, ti.qty_ordered FROM trip_item ti JOIN product p USING (product_id) "
        "WHERE ti.trip_id = %s ORDER BY p.kind", (trip_id,)).fetchall())
    return row[0], items
