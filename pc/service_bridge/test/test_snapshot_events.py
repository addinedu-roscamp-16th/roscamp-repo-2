"""고객 말 · 스냅샷 · 사건 나누기 시험."""
from service_bridge.core.customer import STATUS_TEXT, status_text
from service_bridge.core.events import Outgoing, envelope, read_event, route
from service_bridge.core.intake import place_order
from service_bridge.core.snapshot import admin_snapshot, customer_snapshot, stock
from service_bridge.core.types import OrderLine, OrderRequest, PlaceOk

NINE = ["RECEIVED", "ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING",
        "SHIPPED", "PARTIAL", "HELD", "CANCELLED"]


def _place(conn, seeded, account, key, kind_qty=(("A", 1),)):
    items = tuple(OrderLine(seeded["products"][k], q) for k, q in kind_qty)
    res = place_order(conn, OrderRequest(seeded["accounts"][account], items), key)
    assert isinstance(res, PlaceOk)
    return res


# ---- 고객 말 ----
def test_status_text_covers_all_nine():
    assert set(STATUS_TEXT) == set(NINE)
    assert status_text("FILLING_PAUSE") == "준비 중"
    assert status_text("SHIPPED") == "출고 완료"


# ---- 나누기 ----
def test_route_order_changed():
    out = route("order_changed", 7, {"order_id": "O-1", "status": "FILLING", "account_id": 3}, "T")
    assert out == [
        Outgoing("admin", None, {"type": "order_changed", "seq": 7, "ts": "T",
                                 "data": {"order_id": "O-1", "status": "FILLING"}}),
        Outgoing("customer", 3, {"type": "order_changed", "seq": 7, "ts": "T",
                                 "data": {"order_id": "O-1", "status_text": "준비 중"}})]
    assert "account_id" not in str(out[0].message) + str(out[1].message)


def test_route_trip_changed_admin_only():
    d = {"trip_id": "T-1", "order_id": "O-1", "status": "RECEIVED", "robot_id": None, "items": []}
    assert route("trip_changed", 2, d, "T") == [
        Outgoing("admin", None, {"type": "trip_changed", "seq": 2, "ts": "T", "data": d})]


def test_route_alert_dropped():
    assert route("alert", 1, {"id": "5", "status": "OPEN"}, "T") == []


def test_envelope_omits_seq_when_none():
    assert envelope("snapshot", {"a": 1}, "T") == {"type": "snapshot", "ts": "T", "data": {"a": 1}}
    assert envelope("x", {}, "T", 0)["seq"] == 0


def test_read_event(conn, seeded):
    res = _place(conn, seeded, "tester", "k1")
    seq, = conn.execute("SELECT max(seq) FROM ws_event WHERE type='order_changed'").fetchone()
    t, data = read_event(conn, seq)
    assert t == "order_changed" and data["order_id"] == res.order_id
    assert data["account_id"] == seeded["accounts"]["tester"]
    assert read_event(conn, seq + 1000) is None


# ---- 스냅샷 ----
def test_stock_sorted(conn, seeded):
    s = stock(conn)
    assert [i["product_id"] for i in s["items"]] == sorted(seeded["products"].values())
    assert all(i["stock_qty"] == 20 for i in s["items"])


def test_customer_snapshot_only_own_orders(conn, seeded):
    mine = _place(conn, seeded, "tester", "k1")
    _place(conn, seeded, "tester2", "k2")
    snap = customer_snapshot(conn, seeded["accounts"]["tester"], "NORMAL")
    assert snap["orders"] == [{"order_id": mine.order_id, "status_text": "접수됨"}]
    assert snap["notice"] is None
    assert "items" in snap["stock"]
    t = customer_snapshot(conn, seeded["accounts"]["tester"], "TEST")
    assert t["notice"] == {"kind": "MAINTENANCE", "message": "점검 중"}


def test_admin_snapshot_shape(conn, seeded):
    a = _place(conn, seeded, "tester", "k1", (("A", 3), ("B", 3)))
    _place(conn, seeded, "tester2", "k2")
    robots = [{"robot_id": "pode1"}]
    snap = admin_snapshot(conn, "NORMAL", robots)
    assert set(snap) == {"orders", "trips", "robots", "work_state", "stock"}
    assert [o["status"] for o in snap["orders"]] == ["RECEIVED", "RECEIVED"]
    assert snap["robots"] == robots and snap["work_state"] == {"system_mode": "NORMAL"}
    assert len(snap["trips"]) == 3
    assert all(t["items"] for t in snap["trips"])
    # 사건 data와 같은 모양
    for t in snap["trips"]:
        _, ev = conn.execute(
            "SELECT type, data FROM ws_event WHERE type='trip_changed' AND data->>'trip_id'=%s "
            "ORDER BY seq DESC LIMIT 1", (t["trip_id"],)).fetchone()
        assert ev == t
    assert {t["order_id"] for t in snap["trips"]} >= {a.order_id}


def test_snapshot_excludes_old_finished(conn, seeded):
    old = _place(conn, seeded, "tester", "k1")
    recent = _place(conn, seeded, "tester", "k2")
    for oid, hours in ((old.order_id, 25), (recent.order_id, 1)):
        conn.execute("UPDATE orders SET status='SHIPPED', "
                     "finished_at = now() - make_interval(hours => %s) WHERE order_id=%s",
                     (hours, oid))
    ids = lambda s: [o["order_id"] for o in s["orders"]]  # noqa: E731
    assert ids(customer_snapshot(conn, seeded["accounts"]["tester"], "NORMAL")) == [recent.order_id]
    assert ids(admin_snapshot(conn, "NORMAL", [])) == [recent.order_id]
    assert all(t["order_id"] != old.order_id for t in admin_snapshot(conn, "NORMAL", [])["trips"])
