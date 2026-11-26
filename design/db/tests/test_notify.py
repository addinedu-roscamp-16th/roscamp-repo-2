"""Task 6: 상태 변경 NOTIFY · ws_event · 운영 설정 처음 값 (설계 문서 5절 "화면 전달", 4.13)."""
import json

from helpers import make_account, make_order, make_product, set_trip_status, start_close


def _listen(conn_factory):
    lc = conn_factory()
    lc.execute("listen ifp_events")
    return lc


def _drain(lc):
    return [json.loads(n.payload) for n in lc.notifies(timeout=0.5, stop_after=50)]


def _no_seq(msgs):
    return [{k: v for k, v in m.items() if k != "seq"} for m in msgs]


def test_trip_status_change_notifies(conn, conn_factory):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    _, (tid,) = make_order(conn, acc, [{pid: 1}])
    lc = _listen(conn_factory)
    with conn.transaction():  # autocommit 연결이라 한 트랜잭션으로 묶어야 커밋 때 마지막 수량이 실린다
        set_trip_status(conn, tid, "FILLING", placed={pid: 1})
    got = _drain(lc)
    assert _no_seq(got) == [{"t": "trip_changed", "id": tid, "status": "FILLING"}]
    typ, data = conn.execute("select type, data from ws_event where seq = %s", (got[0]["seq"],)).fetchone()
    assert typ == "trip_changed" and data["items"] == [
        {"product_id": pid, "qty_ordered": 1, "qty_placed": 1, "qty_missing": 0}]
    assert (data["trip_id"], data["order_id"], data["status"], data["robot_id"]) == (tid, tid.rsplit("-", 1)[0], "FILLING", None)


def test_order_insert_notifies(conn, conn_factory):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    lc = _listen(conn_factory)
    order_id, (tid,) = make_order(conn, acc, [{pid: 1}])
    got = _no_seq(_drain(lc))
    assert {"t": "order_changed", "id": order_id, "status": "RECEIVED"} in got
    assert {"t": "trip_changed", "id": tid, "status": "RECEIVED"} in got


def test_no_notify_when_status_unchanged(conn, conn_factory):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    order_id, (tid,) = make_order(conn, acc, [{pid: 1}])
    lc = _listen(conn_factory)
    conn.execute("update trip set robot_id = 'pode1' where trip_id = %s", (tid,))
    conn.execute("update orders set finished_at = null where order_id = %s", (order_id,))
    assert _drain(lc) == []


def test_alert_and_close_notify(conn, conn_factory):
    acc = make_account(conn)
    lc = _listen(conn_factory)
    aid = conn.execute("insert into alert (level, code, source, message) values ('WARN', 'X', 'web', 'm') "
                       "returning alert_id").fetchone()[0]
    cid = start_close(conn, acc)
    got = _drain(lc)
    assert _no_seq(got) == [{"t": "alert", "id": str(aid), "status": "OPEN"},
                            {"t": "stock_close", "id": str(cid), "status": "IN_PROGRESS"}]
    # 알림은 사건(순번 있음), 마감은 상태(순번 없음, ws_event에 안 남음)
    assert "seq" in got[0] and "seq" not in got[1]
    assert conn.execute("select type, data from ws_event where type = 'alert'").fetchone() == (
        "alert", {"id": str(aid), "status": "OPEN"})
    assert conn.execute("select count(*) from ws_event where type = 'stock_close'").fetchone()[0] == 0


def test_insert_in_one_tx_carries_items(conn_factory):
    # 한 트랜잭션: orders -> trip(RECEIVED) -> trip_item 순서. 커밋 때 돌므로 items가 채워진다.
    c = conn_factory(autocommit=False)
    acc = make_account(c)
    pid = make_product(c, "cider")
    c.commit()
    lc = _listen(conn_factory)
    order_id, (tid,) = make_order(c, acc, [{pid: 2}])
    c.commit()
    got = _drain(lc)
    assert [m["t"] for m in got].count("trip_changed") == 1
    rows = dict(c.execute("select type, data from ws_event").fetchall())
    assert rows["trip_changed"]["items"] == [{"product_id": pid, "qty_ordered": 2, "qty_placed": 0, "qty_missing": 0}]
    assert rows["order_changed"] == {"order_id": order_id, "status": "RECEIVED", "account_id": acc}


def test_rollback_writes_no_event(conn_factory):
    c = conn_factory(autocommit=False)
    acc = make_account(c)
    pid = make_product(c, "cider")
    c.commit()
    before = c.execute("select count(*) from ws_event").fetchone()[0]
    lc = _listen(conn_factory)
    make_order(c, acc, [{pid: 1}])
    c.rollback()
    assert _drain(lc) == []
    assert c.execute("select count(*) from ws_event").fetchone()[0] == before


def test_alert_event_shape_unchanged(conn):
    aid = conn.execute("insert into alert (level, code, source, message) values ('WARN', 'X', 'web', 'm') "
                       "returning alert_id").fetchone()[0]
    assert conn.execute("select type, data from ws_event where type = 'alert'").fetchall() == [
        ("alert", {"id": str(aid), "status": "OPEN"})]


def test_ws_event_seq_increases_and_web_insert_notifies(conn, conn_factory):
    lc = _listen(conn_factory)
    s1 = conn.execute("insert into ws_event (type, data) values ('command_result', '{\"request_id\": \"r1\"}') "
                      "returning seq").fetchone()[0]
    s2 = conn.execute("insert into ws_event (type, data) values ('command_result', '{}') returning seq").fetchone()[0]
    assert s2 > s1
    assert _drain(lc) == [{"seq": s1, "t": "command_result"}, {"seq": s2, "t": "command_result"}]


def test_ws_event_rolled_back_with_change(conn_factory):
    c = conn_factory(autocommit=False)
    acc = make_account(c)
    pid = make_product(c, "cider")
    c.commit()
    lc = _listen(conn_factory)
    make_order(c, acc, [{pid: 1}])
    c.rollback()
    assert _drain(lc) == []
    assert c.execute("select count(*) from ws_event").fetchone()[0] == 0


def test_initial_settings(conn):
    rows = dict(conn.execute("select key, value from app_setting").fetchall())
    assert rows == {"basket_capacity": 4, "max_order_items": 8}
