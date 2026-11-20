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
    set_trip_status(conn, tid, "FILLING")
    got = _drain(lc)
    assert _no_seq(got) == [{"t": "trip", "id": tid, "status": "FILLING"}]
    row = conn.execute("select type, data from ws_event where seq = %s", (got[0]["seq"],)).fetchone()
    assert row == ("trip", {"id": tid, "status": "FILLING"})


def test_order_insert_notifies(conn, conn_factory):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    lc = _listen(conn_factory)
    order_id, (tid,) = make_order(conn, acc, [{pid: 1}])
    got = _no_seq(_drain(lc))
    assert {"t": "orders", "id": order_id, "status": "RECEIVED"} in got
    assert {"t": "trip", "id": tid, "status": "RECEIVED"} in got


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
    assert conn.execute("select count(*) from ws_event where type = 'stock_close'").fetchone()[0] == 0


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
