# ---- WS 첫 메시지(snapshot) 내용 만들기 (설계 7.2 · 7.3, ROS 의존 없음) ----
import psycopg

from service_bridge.core.customer import status_text

# 끝난 주문 상태. 이 상태 주문은 finished_at이 24시간 안일 때만 넣는다
TERMINAL = ("SHIPPED", "PARTIAL", "CANCELLED")
# ---- 보여 줄 주문 조건 (DB now() 기준) ----
_VISIBLE = ("(o.status <> ALL(%(term)s) "
            "OR o.finished_at >= now() - interval '24 hours')")

# ---- trip_changed 사건 data와 같은 모양 (design/db/order_db_extra.sql fn_notify_change와 같은 식) ----
TRIP_JSON_SQL = """jsonb_build_object('trip_id', t.trip_id, 'order_id', t.order_id,
  'status', t.status, 'robot_id', t.robot_id,
  'items', COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'product_id', ti.product_id, 'qty_ordered', ti.qty_ordered,
      'qty_placed', ti.qty_placed, 'qty_missing', ti.qty_missing) ORDER BY ti.product_id)
    FROM trip_item ti WHERE ti.trip_id = t.trip_id), '[]'::jsonb))"""


# ---- 주문 품목 [{product_id, qty}] (order_item의 주문 수량, 상품 번호 순, N-06) ----
_ITEMS_SQL = """COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'product_id', oi.product_id, 'qty', oi.qty_ordered) ORDER BY oi.product_id)
    FROM order_item oi WHERE oi.order_id = o.order_id), '[]'::jsonb)"""


def stock(conn: psycopg.Connection) -> dict:
    """{"items": [{product_id, kind, name, stock_qty}]} (N-05). 스냅샷 · WS stock이 같이 쓴다."""
    rows = conn.execute(
        "SELECT product_id, kind, name, stock_qty FROM product ORDER BY product_id").fetchall()
    return {"items": [{"product_id": p, "kind": k, "name": n, "stock_qty": q}
                      for p, k, n, q in rows]}


def limits(conn: psycopg.Connection) -> dict:
    """화면이 쓰는 운영 설정 (N-08). 값은 app_setting이 원본."""
    value, = conn.execute(
        "SELECT value FROM app_setting WHERE key = 'max_order_items'").fetchone()
    return {"max_order_items": value}


def _orders(conn: psycopg.Connection, where: str, params: dict) -> list[tuple]:
    """(order_id, status, created_at ISO 글, items) 줄. created_at은 WS JSON에 그대로 싣게 글로 바꾼다."""
    rows = conn.execute(
        f"SELECT o.order_id, o.status, o.created_at, {_ITEMS_SQL} FROM orders o "
        f"WHERE {where} ORDER BY o.created_at, o.order_id", params).fetchall()
    return [(oid, st, created.isoformat(), items) for oid, st, created, items in rows]


def customer_snapshot(conn: psycopg.Connection, account_id: int, mode: str) -> dict:
    rows = _orders(conn, f"o.account_id = %(a)s AND {_VISIBLE}",
                   {"a": account_id, "term": list(TERMINAL)})
    return {
        "orders": [{"order_id": oid, "status_text": status_text(st), "created_at": created,
                    "items": items} for oid, st, created, items in rows],
        "stock": stock(conn),
        "limits": limits(conn),
        "notice": {"kind": "MAINTENANCE", "message": "점검 중"} if mode == "TEST" else None,
    }


def admin_snapshot(conn: psycopg.Connection, mode: str, robots: list[dict]) -> dict:
    params = {"term": list(TERMINAL)}
    orders = _orders(conn, _VISIBLE, params)
    trips = conn.execute(
        f"SELECT {TRIP_JSON_SQL} FROM trip t JOIN orders o ON o.order_id = t.order_id "
        f"WHERE {_VISIBLE} ORDER BY o.created_at, o.order_id, t.seq", params).fetchall()
    return {
        "orders": [{"order_id": oid, "status": st, "created_at": created, "items": items}
                   for oid, st, created, items in orders],
        "trips": [r[0] for r in trips],
        "robots": robots,
        "work_state": {"system_mode": mode},
        "stock": stock(conn),
    }
