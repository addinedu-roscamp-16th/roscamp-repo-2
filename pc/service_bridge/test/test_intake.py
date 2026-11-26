"""주문 접수 core 시험: 나누기 · 본문 검사 (DB 없음) + place_order (시험 DB)."""
import threading
from concurrent.futures import ThreadPoolExecutor
import time

import psycopg
import pytest
from psycopg.errors import LockNotAvailable

from service_bridge.core.intake import check_shape, place_order, split_trips
from service_bridge.core.types import (
    OrderLine, OrderRequest, PlaceError, PlaceOk, TripPlan)


def _req(seeded, kind_qty, account="tester"):
    items = tuple(OrderLine(seeded["products"][k], q) for k, q in kind_qty)
    return OrderRequest(seeded["accounts"][account], items)


# ---- 나누기 · 본문 검사 ----
def test_split_in_request_order():
    plans = split_trips([OrderLine(1, 3), OrderLine(2, 3)], 4)
    assert plans == [TripPlan(1, (OrderLine(1, 3), OrderLine(2, 1))),
                     TripPlan(2, (OrderLine(2, 2),))]


def test_split_single_line_over_capacity():
    assert [p.items for p in split_trips([OrderLine(1, 7)], 4)] == [
        (OrderLine(1, 4),), (OrderLine(1, 3),)]


@pytest.mark.parametrize("total,count", [(1, 1), (4, 1), (5, 2), (8, 2), (9, 3)])
def test_split_boundaries(total, count):
    plans = split_trips([OrderLine(1, total)], 4)
    assert len(plans) == count
    assert sum(l.qty for p in plans for l in p.items) == total
    assert [p.seq for p in plans] == list(range(1, count + 1))


@pytest.mark.parametrize("items", [
    (), (OrderLine(1, 0),), (OrderLine(1, -2),), (OrderLine(1, 1), OrderLine(1, 2))])
def test_check_shape_rejects(items):
    err = check_shape(OrderRequest(1, items))
    assert (err.status, err.code) == (400, "BAD_REQUEST")


def test_check_shape_accepts():
    assert check_shape(OrderRequest(1, (OrderLine(1, 1), OrderLine(2, 1)))) is None


# ---- 접수 ----
def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_place_order_writes_everything(conn, seeded):
    req = _req(seeded, [("mentos", 3), ("choco_bar", 3)])
    res = place_order(conn, req, "key-1")
    assert isinstance(res, PlaceOk) and not res.replayed
    oid = res.order_id
    assert res.trip_ids == (f"{oid}-1", f"{oid}-2")
    row = conn.execute("SELECT idem_key, account_id, status FROM orders").fetchall()
    assert row == [("key-1", seeded["account_id"], "RECEIVED")]
    assert _count(conn, "order_item") == 2
    assert conn.execute("SELECT trip_id, seq, status FROM trip ORDER BY seq").fetchall() == [
        (f"{oid}-1", 1, "RECEIVED"), (f"{oid}-2", 2, "RECEIVED")]
    assert conn.execute("SELECT trip_id, product_id, qty_ordered FROM trip_item "
                        "ORDER BY trip_id, product_id").fetchall() == [
        (f"{oid}-1", seeded["products"]["mentos"], 3), (f"{oid}-1", seeded["products"]["choco_bar"], 1),
        (f"{oid}-2", seeded["products"]["choco_bar"], 2)]
    assert conn.execute("SELECT trip_id, attempts, central_acked_at FROM trip_dispatch "
                        "ORDER BY trip_id").fetchall() == [
        (f"{oid}-1", 0, None), (f"{oid}-2", 0, None)]
    hist = conn.execute("SELECT trip_id, from_status, to_status, actor FROM status_history "
                        "ORDER BY history_id").fetchall()
    assert sorted(hist, key=lambda r: str(r[0])) == sorted(
        [(f"{oid}-1", None, "RECEIVED", "USER"), (f"{oid}-2", None, "RECEIVED", "USER"),
         (None, None, "RECEIVED", "USER")], key=lambda r: str(r[0]))
    events = conn.execute("SELECT type, data FROM ws_event ORDER BY seq").fetchall()
    kinds = [t for t, _ in events]
    assert kinds.count("order_changed") == 1 and kinds.count("trip_changed") == 2
    for t, data in events:
        if t == "trip_changed":
            assert data["items"]
        if t == "order_changed":
            assert data["order_id"] == oid and data["account_id"] == seeded["account_id"]


def test_same_key_same_body_replays(conn, seeded):
    first = place_order(conn, _req(seeded, [("mentos", 3), ("choco_bar", 3)]), "k")
    second = place_order(conn, _req(seeded, [("choco_bar", 3), ("mentos", 3)]), "k")  # 순서가 달라도 같음
    assert second.replayed is True
    assert (second.order_id, second.trip_ids) == (first.order_id, first.trip_ids)
    assert (_count(conn, "orders"), _count(conn, "trip"), _count(conn, "status_history")) == (1, 2, 3)


def test_same_key_other_body_conflicts(conn, seeded):
    place_order(conn, _req(seeded, [("mentos", 2)]), "k")
    for other in (_req(seeded, [("mentos", 3)]), _req(seeded, [("mentos", 2)], "tester2")):
        res = place_order(conn, other, "k")
        assert isinstance(res, PlaceError) and (res.status, res.code) == (409, "IDEMPOTENCY_CONFLICT")
    assert _count(conn, "orders") == 1


def _threads(n, fn):
    out, errs = [None] * n, []

    def run(i):
        try:
            out[i] = fn(i)
        except Exception as e:  # noqa: BLE001 - 시험이 모아서 확인
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert not errs, errs
    return out


def test_same_key_concurrent(pool, conn, seeded):
    req = _req(seeded, [("mentos", 5)])

    def go(_):
        with pool.connection() as c:
            return place_order(c, req, "same")
    res = _threads(2, go)
    assert all(isinstance(r, PlaceOk) for r in res)
    assert res[0].order_id == res[1].order_id
    assert sorted(r.replayed for r in res) == [False, True]
    assert _count(conn, "orders") == 1 and _count(conn, "trip") == 2


def test_same_key_race_replays_committed(pool, seeded, test_db):
    # 옆 연결이 7002를 쥔 채 키 'same' 주문을 써 둔다(커밋 전). place_order는 재생 확인(아직 안 보임)을
    # 지나 7002에서 막히고, 옆 연결이 커밋하면 orders 삽입이 idem_key 충돌 -> _KeyRace -> 재생 답
    from conftest import _dsn
    req = _req(seeded, [("mentos", 2)])
    pid = seeded["products"]["mentos"]
    side = psycopg.connect(_dsn(test_db), autocommit=False)
    try:
        side.execute("SELECT pg_advisory_xact_lock(7002)")
        oid = side.execute("SELECT 'O-' || to_char(current_date, 'YYYYMMDD') || '-0001'").fetchone()[0]
        side.execute("INSERT INTO orders (order_id, account_id, status, idem_key) "
                     "VALUES (%s, %s, 'RECEIVED', 'same')", (oid, req.account_id))
        side.execute("INSERT INTO order_item (order_id, product_id, qty_ordered) VALUES (%s, %s, 2)",
                     (oid, pid))
        side.execute("INSERT INTO trip (trip_id, order_id, seq, status) VALUES (%s, %s, 1, 'RECEIVED')",
                     (f"{oid}-1", oid))
        out = []

        def go():
            with pool.connection() as c:
                out.append(place_order(c, req, "same"))
        t = threading.Thread(target=go)
        t.start()
        time.sleep(0.3)  # lock_timeout(0.5초) 안에 커밋
        assert t.is_alive()  # 7002에서 기다리는 중
        side.commit()
        t.join(5)
    finally:
        side.close()
    assert len(out) == 1
    res = out[0]
    assert isinstance(res, PlaceOk) and res.replayed is True
    assert (res.order_id, res.trip_ids) == (oid, (f"{oid}-1",))


def test_out_of_stock_rejects_and_counts_low_stock(conn, seeded):
    pid = seeded["products"]["mentos"]
    # 재고를 5로 줄인다(원장 반출 15). 6개는 max_order_items(8) 안이라 재고 검사까지 간다
    conn.execute("INSERT INTO stock_ledger (product_id, entry_type, change_qty, reason) "
                 "VALUES (%s, 'OUTBOUND', -15, 'DAMAGED')", (pid,))
    req = OrderRequest(seeded["account_id"], (OrderLine(pid, 6),))
    res = place_order(conn, req, "k1")
    assert (res.status, res.code) == (422, "OUT_OF_STOCK")
    assert res.detail == {"items": [{"product_id": pid, "qty": 6, "stock_qty": 5}]}
    assert _count(conn, "orders") == 0
    place_order(conn, req, "k2")
    rows = conn.execute("SELECT alert_id, status, detail FROM alert WHERE code = 'LOW_STOCK' "
                        "AND product_id = %s", (pid,)).fetchall()
    assert len(rows) == 1 and rows[0][1] == "OPEN"
    assert rows[0][2]["reject_count"] == 2 and rows[0][2]["last_rejected_at"]


def test_over_max_items(conn, seeded):
    res = place_order(conn, _req(seeded, [("mentos", 5), ("choco_bar", 4)]), "k")
    assert (res.status, res.code) == (422, "OVER_MAX_ITEMS")
    assert _count(conn, "orders") == 0 and _count(conn, "alert") == 0


def test_unknown_product(conn, seeded):
    res = place_order(conn, OrderRequest(seeded["account_id"], (OrderLine(99999, 1),)), "k")
    assert (res.status, res.code) == (422, "UNKNOWN_PRODUCT")
    assert _count(conn, "orders") == 0


def test_unknown_account(conn, seeded):
    res = place_order(conn, OrderRequest(99999, (OrderLine(seeded["products"]["mentos"], 1),)), "k")
    assert (res.status, res.code) == (404, "UNKNOWN_ACCOUNT")
    assert _count(conn, "orders") == 0


def test_waits_for_close_lock(pool, conn, seeded, test_db):
    from conftest import _dsn
    holder = psycopg.connect(_dsn(test_db), autocommit=False)
    try:
        holder.execute("SELECT pg_advisory_xact_lock(7001)")
        started = time.monotonic()
        with pool.connection() as c, pytest.raises(LockNotAvailable):
            place_order(c, _req(seeded, [("mentos", 1)]), "k")
        assert 0.4 < time.monotonic() - started < 1.5
    finally:
        holder.rollback()
        holder.close()
    assert _count(conn, "orders") == 0


def test_order_ids_unique_under_concurrency(pool, conn, seeded):
    # 풀 연결이 4개라 동시에 겹치는 접수는 최대 4개. 열 건을 네 스레드로 밀어 넣는다
    def go(i):
        with pool.connection() as c:
            return place_order(c, _req(seeded, [("mentos", 1)]), f"key-{i}")
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(go, range(10)))
    assert all(isinstance(r, PlaceOk) for r in res)
    assert len({r.order_id for r in res}) == 10
    assert _count(conn, "orders") == 10
