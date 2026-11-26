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


def stock(conn: psycopg.Connection) -> dict:
    rows = conn.execute(
        "SELECT product_id, stock_qty FROM product ORDER BY product_id").fetchall()
    return {"items": [{"product_id": p, "stock_qty": q} for p, q in rows]}


def customer_snapshot(conn: psycopg.Connection, account_id: int, mode: str) -> dict:
    rows = conn.execute(
        f"SELECT o.order_id, o.status FROM orders o WHERE o.account_id = %(a)s "
        f"AND {_VISIBLE} ORDER BY o.created_at, o.order_id",
        {"a": account_id, "term": list(TERMINAL)}).fetchall()
    return {
        "orders": [{"order_id": oid, "status_text": status_text(st)} for oid, st in rows],
        "stock": stock(conn),
        "notice": {"kind": "MAINTENANCE", "message": "점검 중"} if mode == "TEST" else None,
    }


def admin_snapshot(conn: psycopg.Connection, mode: str, robots: list[dict]) -> dict:
    params = {"term": list(TERMINAL)}
    orders = conn.execute(
        f"SELECT o.order_id, o.status FROM orders o WHERE {_VISIBLE} "
        f"ORDER BY o.created_at, o.order_id", params).fetchall()
    trips = conn.execute(
        f"SELECT {TRIP_JSON_SQL} FROM trip t JOIN orders o ON o.order_id = t.order_id "
        f"WHERE {_VISIBLE} ORDER BY o.created_at, o.order_id, t.seq", params).fetchall()
    return {
        "orders": [{"order_id": oid, "status": st} for oid, st in orders],
        "trips": [r[0] for r in trips],
        "robots": robots,
        "work_state": {"system_mode": mode},
        "stock": stock(conn),
    }
