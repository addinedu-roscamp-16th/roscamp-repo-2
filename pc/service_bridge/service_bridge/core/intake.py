# ---- 주문 접수: 본문 검사 · 운반 건 나누기 · 한 트랜잭션 접수 (설계 5.2 ~ 5.4, ROS 의존 없음) ----
import datetime
import json
from collections.abc import Sequence

import psycopg
from psycopg.errors import UniqueViolation

from service_bridge.core.types import (
    OrderLine, OrderRequest, PlaceError, PlaceOk, TripPlan)

# 처음 한 번 + 다시 매기기 최대 3번 (설계 5.4). 번호 매기기는 7002로 한 줄로 세우므로 보통 겹치지 않고,
# 이 재시도는 그 뒤의 안전망이다(웹 풀 연결이 4개라 겹칠 상대가 있어도 최대 3)
ORDER_ID_TRIES = 4


# ---- 운반 건으로 나누기 (설계 5.3) ----
def split_trips(items: Sequence[OrderLine], capacity: int) -> list[TripPlan]:
    """요청 품목 순서대로 바구니를 capacity까지 채운다. 한 상품이 두 건에 걸쳐 나뉠 수 있다."""
    plans: list[TripPlan] = []
    current: list[OrderLine] = []
    room = capacity
    for line in items:
        left = line.qty
        while left > 0:
            take = min(left, room)
            current.append(OrderLine(line.product_id, take))
            left -= take
            room -= take
            if room == 0:
                plans.append(TripPlan(len(plans) + 1, tuple(current)))
                current, room = [], capacity
    if current:
        plans.append(TripPlan(len(plans) + 1, tuple(current)))
    return plans


# ---- 본문 모양 검사 ----
def check_shape(req: OrderRequest) -> PlaceError | None:
    if not req.items:
        return PlaceError(400, "BAD_REQUEST", "품목이 비어 있음")
    if any(line.qty < 1 for line in req.items):
        return PlaceError(400, "BAD_REQUEST", "수량은 1 이상이어야 함")
    ids = [line.product_id for line in req.items]
    if len(set(ids)) != len(ids):
        return PlaceError(400, "BAD_REQUEST", "같은 상품이 두 번 들어 있음")
    return None


def account_exists(conn: psycopg.Connection, account_id: int) -> bool:
    return conn.execute("SELECT 1 FROM account WHERE account_id = %s",
                        (account_id,)).fetchone() is not None


# ---- 접수 ----
class _Rollback(Exception):
    """큰 트랜잭션을 되돌리고 거절 답을 돌려주기 위한 내부 신호."""

    def __init__(self, error: PlaceError):
        self.error = error


class _KeyRace(Exception):
    """다른 접수가 같은 키를 먼저 커밋했다 (idem_key UNIQUE 충돌)."""


def place_order(conn: psycopg.Connection, req: OrderRequest, idem_key: str) -> PlaceOk | PlaceError:
    """주문 하나를 접수한다. 트랜잭션은 여기서 연다.

    잠금 대기 초과(psycopg.errors.LockNotAvailable)는 그대로 올린다(웹이 503 BUSY로 바꿈).
    """
    error = check_shape(req)
    if error:
        return error
    try:
        try:
            with conn.transaction():
                return _place(conn, req, idem_key)
        except _KeyRace:
            # 같은 키가 동시에 들어옴: 먼저 커밋된 쪽을 새 트랜잭션에서 읽어 답한다
            with conn.transaction():
                return _replay_or_conflict(conn, req, idem_key)
    except _Rollback as rb:
        error = rb.error
    if error.code == "OUT_OF_STOCK":
        _count_low_stock(conn, error.detail["items"])
    return error


def _place(conn: psycopg.Connection, req: OrderRequest, idem_key: str) -> PlaceOk:
    # 1. 마감 시작 · 확정(배타 잠금)과 겹치지 않게 공유 잠금
    conn.execute("SELECT pg_advisory_xact_lock_shared(7001)")
    # 2. 같은 키가 이미 있으면 처음 답 또는 409
    found = _replay_or_conflict(conn, req, idem_key)
    if found is not None:
        return found
    # 3. 계정 · 상품 · 품목 합 · 재고
    if not account_exists(conn, req.account_id):
        raise _Rollback(PlaceError(404, "UNKNOWN_ACCOUNT", "모르는 계정"))
    stock = dict(conn.execute(
        "SELECT product_id, stock_qty FROM product WHERE product_id = ANY(%s)",
        ([line.product_id for line in req.items],)).fetchall())
    unknown = [line.product_id for line in req.items if line.product_id not in stock]
    if unknown:
        raise _Rollback(PlaceError(422, "UNKNOWN_PRODUCT", "모르는 상품",
                                   {"product_ids": unknown}))
    settings = dict(conn.execute("SELECT key, value FROM app_setting").fetchall())
    total = sum(line.qty for line in req.items)
    if total > settings["max_order_items"]:
        raise _Rollback(PlaceError(
            422, "OVER_MAX_ITEMS", "품목 합이 한도를 넘음",
            {"total": total, "max_order_items": settings["max_order_items"]}))
    short = [{"product_id": line.product_id, "qty": line.qty, "stock_qty": stock[line.product_id]}
             for line in req.items if line.qty > stock[line.product_id]]
    if short:
        raise _Rollback(PlaceError(422, "OUT_OF_STOCK", "재고가 모자람", {"items": short}))
    # 4. 주문 번호 · orders · order_item
    order_id = _insert_order(conn, req, idem_key)
    for line in req.items:
        conn.execute(
            "INSERT INTO order_item (order_id, product_id, qty_ordered) VALUES (%s, %s, %s)",
            (order_id, line.product_id, line.qty))
    # 5. 운반 건 · trip_item · trip_dispatch
    trip_ids = []
    for plan in split_trips(req.items, settings["basket_capacity"]):
        trip_id = f"{order_id}-{plan.seq}"
        trip_ids.append(trip_id)
        conn.execute(
            "INSERT INTO trip (trip_id, order_id, seq, status) VALUES (%s, %s, %s, 'RECEIVED')",
            (trip_id, order_id, plan.seq))
        for line in plan.items:
            conn.execute(
                "INSERT INTO trip_item (trip_id, product_id, qty_ordered) VALUES (%s, %s, %s)",
                (trip_id, line.product_id, line.qty))
        conn.execute("INSERT INTO trip_dispatch (trip_id) VALUES (%s)", (trip_id,))
    # 6. 접수 이력: 운반 건마다 한 줄 + 주문 한 줄(trip_id 없음)
    for tid in (*trip_ids, None):
        conn.execute(
            "INSERT INTO status_history (order_id, trip_id, from_status, to_status, actor, "
            "account_id) VALUES (%s, %s, NULL, 'RECEIVED', 'USER', %s)",
            (order_id, tid, req.account_id))
    return PlaceOk(order_id, tuple(trip_ids))


# ---- 같은 키 ----
def _replay_or_conflict(conn: psycopg.Connection, req: OrderRequest,
                        idem_key: str) -> PlaceOk | PlaceError | None:
    row = conn.execute("SELECT order_id, account_id FROM orders WHERE idem_key = %s",
                       (idem_key,)).fetchone()
    if row is None:
        return None
    order_id, account_id = row
    stored = dict(conn.execute(
        "SELECT product_id, qty_ordered FROM order_item WHERE order_id = %s",
        (order_id,)).fetchall())
    # 순서는 보지 않는다
    if account_id != req.account_id or stored != {line.product_id: line.qty for line in req.items}:
        return PlaceError(409, "IDEMPOTENCY_CONFLICT", "같은 키로 다른 본문이 왔음")
    trip_ids = tuple(r[0] for r in conn.execute(
        "SELECT trip_id FROM trip WHERE order_id = %s ORDER BY seq", (order_id,)).fetchall())
    return PlaceOk(order_id, trip_ids, replayed=True)


# ---- 주문 번호 (설계 5.4) ----
def _insert_order(conn: psycopg.Connection, req: OrderRequest, idem_key: str) -> str:
    # 날짜는 DB 기준(웹 프로세스 시계 · 시간대와 무관하게 created_at과 같은 날)
    prefix = "O-" + conn.execute("SELECT to_char(current_date, 'YYYYMMDD')").fetchone()[0] + "-"
    # 번호 매기기는 커밋까지 한 줄로 세운다(7002, 순서 7001 공유 → 7002 → orders라 마감과 교착 없음).
    # 안 그러면 최댓값 + 1이 계속 겹쳐
    # 늦게 온 접수가 다시 매기기를 다 써 버릴 수 있다. 아래 orders_pkey 충돌 재시도는 안전망으로 남긴다
    conn.execute("SELECT pg_advisory_xact_lock(7002)")
    for _ in range(ORDER_ID_TRIES):
        last = conn.execute("SELECT max(order_id) FROM orders WHERE order_id LIKE %s",
                            (prefix + "%",)).fetchone()[0]
        order_id = f"{prefix}{int(last[-4:]) + 1 if last else 1:04d}"
        try:
            with conn.transaction():  # SAVEPOINT: 충돌해도 바깥 트랜잭션은 산다
                conn.execute(
                    "INSERT INTO orders (order_id, account_id, status, idem_key) "
                    "VALUES (%s, %s, 'RECEIVED', %s)", (order_id, req.account_id, idem_key))
            return order_id
        except UniqueViolation as e:
            if e.diag.constraint_name == "orders_pkey":
                continue  # 다른 접수가 같은 번호를 먼저 커밋함: 다시 매긴다
            raise _KeyRace() from e
    raise RuntimeError(f"주문 번호가 {ORDER_ID_TRIES}번 겹침")


# ---- 재고 부족 알림 (큰 트랜잭션을 되돌린 뒤 새 트랜잭션) ----
def _count_low_stock(conn: psycopg.Connection, short: list[dict]) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn.transaction():
        for item in short:
            first = {"reject_count": 1, "last_rejected_at": now}
            conn.execute(
                "INSERT INTO alert (level, code, source, product_id, message, detail) "
                "VALUES ('WARN', 'LOW_STOCK', 'order_service', %s, '재고 부족으로 주문 거절', %s) "
                "ON CONFLICT (code, product_id) WHERE status = 'OPEN' AND code = 'LOW_STOCK' "
                # 다른 칸은 남기고 두 칸만 바꾼다
                "DO UPDATE SET detail = COALESCE(alert.detail, '{}'::jsonb) || jsonb_build_object("
                "'reject_count', COALESCE((alert.detail->>'reject_count')::int, 0) + 1, "
                "'last_rejected_at', %s::text)",
                (item["product_id"], json.dumps(first), now))
