"""웹: 주문 접수 API · 스냅샷 · WS · LISTEN 시험 (설계 5 · 7)."""
import asyncio
import json
import threading
import time
import uuid
import warnings

import anyio
import pytest

# ---- starlette TestClient가 httpx를 쓸 때 내는 사용 중단 경고는 시험 출력에서 뺀다 ----
warnings.filterwarnings("ignore", message=".*httpx.*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from service_bridge.core.events import Outgoing  # noqa: E402
from service_bridge.web.app import create_app  # noqa: E402
from service_bridge.web.hub import Hub  # noqa: E402

WAIT = 3.0  # 메시지 하나를 기다리는 최대 시간(초)


class Wake:
    """wake_dispatch 가짜: 부른 횟수를 센다."""

    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


def _make_client(pool, mode):
    wake = Wake()
    app = create_app(pool, pool.conninfo, mode, wake)
    with TestClient(app) as c:
        c.wake = wake
        _wait_listener(app)
        yield c


def _wait_listener(app, old_pid=None):
    """LISTEN 연결이 붙을 때까지(다시 붙는 경우는 pid가 바뀔 때까지) 기다린다."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pid = app.state.listener_pid
        if pid is not None and pid != old_pid:
            return pid
        time.sleep(0.02)
    raise AssertionError("LISTEN 연결이 붙지 않음")


@pytest.fixture
def client(pool):
    yield from _make_client(pool, "NORMAL")


@pytest.fixture
def client_test_mode(pool):
    yield from _make_client(pool, "TEST")


# ---- WS 받기 (시간 한도 있음) ----
async def _recv(rx, timeout):
    with anyio.fail_after(timeout):
        return await rx.receive()


def recv_raw(ws, timeout=WAIT):
    return ws.portal.call(_recv, ws._send_rx, timeout)


def recv(ws, timeout=WAIT):
    msg = recv_raw(ws, timeout)
    assert msg["type"] == "websocket.send", msg
    return json.loads(msg["text"])


def recv_until(ws, type_, timeout=WAIT):
    """type_ 메시지가 올 때까지 받는다. 그 사이 받은 다른 메시지는 버린다."""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise AssertionError(f"{type_} 메시지가 오지 않음")
        m = recv(ws, left)
        if m["type"] == type_:
            return m


def drain(ws, window):
    """window초 동안 오는 메시지를 모두 받는다."""
    got = []
    deadline = time.monotonic() + window
    while (left := deadline - time.monotonic()) > 0:
        try:
            got.append(recv(ws, left))
        except TimeoutError:
            break
    return got


def _body(seeded, account="tester", items=(("mentos", 1),)):
    return {"account_id": seeded["accounts"][account],
            "items": [{"product_id": seeded["products"][k], "qty": q} for k, q in items]}


def _post(client, body, key=None):
    return client.post("/api/orders", json=body,
                       headers={"Idempotency-Key": key or str(uuid.uuid4())})


# ---- POST /api/orders ----
def test_post_order_202(client, seeded):
    r = _post(client, _body(seeded, items=(("mentos", 3), ("choco_bar", 3))))
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "RECEIVED"
    assert body["order_id"].startswith("O-")
    assert body["trip_ids"] == [f"{body['order_id']}-1", f"{body['order_id']}-2"]
    assert client.wake.count == 1


def test_post_order_replay_same_answer(client, seeded):
    key = str(uuid.uuid4())
    first = _post(client, _body(seeded), key)
    again = _post(client, _body(seeded), key)
    assert again.status_code == 202
    assert again.json() == first.json()
    assert client.wake.count == 1  # 다시 온 요청은 아무것도 쓰지 않으므로 깨우지 않음


def test_post_order_needs_key(client, seeded):
    r = client.post("/api/orders", json=_body(seeded))
    assert r.status_code == 400
    assert r.json()["code"] == "BAD_REQUEST"
    assert client.wake.count == 0


def test_post_order_bad_shape(client, seeded):
    for body in ({"account_id": 1}, {"account_id": "x", "items": []},
                 _body(seeded, items=(("mentos", 0),)), _body(seeded, items=())):
        r = _post(client, body)
        assert r.status_code == 400, body
        assert set(r.json()) == {"code", "message", "detail"}
        assert r.json()["code"] == "BAD_REQUEST"


def test_post_order_maps_errors(client, conn, seeded):
    key = str(uuid.uuid4())
    assert _post(client, _body(seeded), key).status_code == 202
    r = _post(client, _body(seeded, items=(("choco_bar", 1),)), key)
    assert (r.status_code, r.json()["code"]) == (409, "IDEMPOTENCY_CONFLICT")

    r = _post(client, {"account_id": 999999, "items": [{"product_id": 1, "qty": 1}]})
    assert (r.status_code, r.json()["code"]) == (404, "UNKNOWN_ACCOUNT")

    r = _post(client, {"account_id": seeded["accounts"]["tester"],
                       "items": [{"product_id": 999999, "qty": 1}]})
    assert (r.status_code, r.json()["code"]) == (422, "UNKNOWN_PRODUCT")

    r = _post(client, _body(seeded, items=(("mentos", 5), ("choco_bar", 4))))
    assert (r.status_code, r.json()["code"]) == (422, "OVER_MAX_ITEMS")

    conn.execute("INSERT INTO stock_ledger (product_id, entry_type, change_qty, reason) "
                 "VALUES (%s, 'OUTBOUND', -16, 'DAMAGED')", (seeded["products"]["mygummy"],))
    r = _post(client, _body(seeded, items=(("mygummy", 5),)))
    assert (r.status_code, r.json()["code"]) == (422, "OUT_OF_STOCK")
    assert r.json()["detail"]["items"][0]["stock_qty"] == 4
    assert client.wake.count == 1


def test_test_mode_rejects(client_test_mode, seeded):
    r = _post(client_test_mode, _body(seeded))
    assert r.status_code == 503
    assert r.json()["code"] == "MAINTENANCE"
    assert client_test_mode.wake.count == 0


def test_busy_is_503(client, conn, seeded):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(7001)")
        r = _post(client, _body(seeded))
    assert r.status_code == 503
    assert r.json()["code"] == "BUSY"
    assert r.headers["Retry-After"] == "1"
    assert client.wake.count == 0


# ---- GET /api/snapshot ----
def test_snapshot_rest(client, seeded):
    oid = _post(client, _body(seeded)).json()["order_id"]
    r = client.get("/api/snapshot", params={"view": "customer",
                                            "account_id": seeded["accounts"]["tester"]})
    assert r.status_code == 200
    o, = r.json()["orders"]
    assert (o["order_id"], o["status_text"]) == (oid, "접수됨")
    assert o["items"] and o["created_at"]
    assert r.json()["limits"] == {"max_order_items": 8}
    assert r.json()["notice"] is None
    r = client.get("/api/snapshot", params={"view": "admin"})
    assert r.status_code == 200
    # 브리지가 /central/state를 아직 못 받음 = LOST (central 재시작 설계 6.4)
    assert r.json()["work_state"] == {"system_mode": "NORMAL", "central": "LOST"}
    assert [o["order_id"] for o in r.json()["orders"]] == [oid]


def test_snapshot_rest_unknown_account(client, seeded):
    r = client.get("/api/snapshot", params={"view": "customer", "account_id": 999999})
    assert (r.status_code, r.json()["code"]) == (404, "UNKNOWN_ACCOUNT")
    r = client.get("/api/snapshot", params={"view": "customer"})
    assert (r.status_code, r.json()["code"]) == (404, "UNKNOWN_ACCOUNT")
    r = client.get("/api/snapshot", params={"view": "nope"})
    assert (r.status_code, r.json()["code"]) == (400, "BAD_REQUEST")


# ---- WS ----
def test_ws_first_message_is_snapshot(client, seeded):
    with client.websocket_connect("/ws/admin") as ws:
        m = recv(ws)
        assert m["type"] == "snapshot" and "seq" not in m and m["ts"]
        assert set(m["data"]) == {"orders", "trips", "robots", "work_state", "stock"}
    acc = seeded["accounts"]["tester"]
    with client.websocket_connect(f"/ws/customer?account_id={acc}") as ws:
        m = recv(ws)
        assert m["type"] == "snapshot"
        assert set(m["data"]) == {"orders", "stock", "limits", "notice"}
        assert drain(ws, 0.2) == []  # NORMAL이면 customer_notice 없음


def test_ws_customer_unknown_account_closed(client, seeded):
    with client.websocket_connect("/ws/customer?account_id=999999") as ws:
        msg = recv_raw(ws)
        assert msg["type"] == "websocket.close" and msg["code"] == 4404


def test_customer_sees_only_own_orders(client, seeded):
    a1, a2 = seeded["accounts"]["tester"], seeded["accounts"]["tester2"]
    with client.websocket_connect(f"/ws/customer?account_id={a1}") as w1, \
            client.websocket_connect(f"/ws/customer?account_id={a2}") as w2:
        recv(w1), recv(w2)
        oid = _post(client, _body(seeded, "tester")).json()["order_id"]
        m = recv_until(w1, "order_changed")
        assert m["data"] == {"order_id": oid, "status_text": "접수됨"}
        assert isinstance(m["seq"], int)
        others = drain(w2, 0.5)
        assert [x["type"] for x in others] == ["stock"]


def test_admin_gets_trip_changed(client, seeded):
    with client.websocket_connect("/ws/admin") as ws:
        recv(ws)
        oid = _post(client, _body(seeded)).json()["order_id"]
        got = {m["type"]: m for m in drain(ws, 0.7)}
        trip = got["trip_changed"]["data"]
        assert (trip["trip_id"], trip["order_id"], trip["status"]) == (f"{oid}-1", oid, "RECEIVED")
        assert trip["items"] == [{"product_id": seeded["products"]["mentos"], "qty_ordered": 1,
                                  "qty_placed": 0, "qty_missing": 0}]
        assert got["order_changed"]["data"] == {"order_id": oid, "status": "RECEIVED"}
        assert got["order_changed"]["seq"] < got["trip_changed"]["seq"]


def test_customer_test_mode_notice(client_test_mode, seeded):
    acc = seeded["accounts"]["tester"]
    with client_test_mode.websocket_connect(f"/ws/customer?account_id={acc}") as ws:
        assert recv(ws)["type"] == "snapshot"
        m = recv(ws)
        assert m["type"] == "customer_notice"
        assert m["data"] == {"kind": "MAINTENANCE", "message": "점검 중"}
    with client_test_mode.websocket_connect("/ws/admin") as ws:
        m = recv(ws)
        assert m["data"]["work_state"] == {"system_mode": "TEST", "central": "LOST"}
        assert drain(ws, 0.2) == []  # 관리자에게는 customer_notice 없음


def test_stock_sent_after_order(client, conn, seeded):
    acc = seeded["accounts"]["tester2"]
    with client.websocket_connect("/ws/admin") as wa, \
            client.websocket_connect(f"/ws/customer?account_id={acc}") as wc:
        recv(wa), recv(wc)
        # 접수는 재고를 줄이지 않으므로, 보낸 값이 지금 DB 값인지 보려고 재고를 먼저 바꿔 둔다
        conn.execute("INSERT INTO stock_ledger (product_id, entry_type, change_qty, reason) "
                     "VALUES (%s, 'OUTBOUND', -2, 'DAMAGED')", (seeded["products"]["mentos"],))
        _post(client, _body(seeded, items=(("mentos", 2),)))
        for ws in (wa, wc):
            m = recv_until(ws, "stock")
            assert "seq" not in m
            a = [i for i in m["data"]["items"] if i["product_id"] == seeded["products"]["mentos"]]
            # N-05: WS stock도 스냅샷과 같은 모양 {product_id, kind, name, stock_qty}
            assert [(i["product_id"], i["kind"], i["stock_qty"]) for i in a] == [
                (seeded["products"]["mentos"], "mentos", 18)]
            assert set(a[0]) == {"product_id", "kind", "name", "stock_qty"}


def test_robots_throttled(client, seeded):
    box = client.app.state.robots
    acc = seeded["accounts"]["tester"]
    with client.websocket_connect("/ws/admin") as wa, \
            client.websocket_connect(f"/ws/customer?account_id={acc}") as wc:
        recv(wa), recv(wc)

        # ---- ROS 실행기 스레드처럼 다른 스레드에서 넣는다 ----
        def put_many():
            for i in range(20):
                box.put([{"robot_id": "R1", "fsm_state": "IDLE", "fault": "", "battery": 90.0,
                          "x": float(i), "y": 0.0, "trip_id": ""}])
                time.sleep(0.004)

        t = threading.Thread(target=put_many)
        t.start()
        t.join(timeout=2)
        got = [m for m in drain(wa, 0.7) if m["type"] == "robots"]
        assert 1 <= len(got) <= 2
        assert got[-1]["data"]["robots"][0]["x"] == 19.0
        assert box.latest()[0]["x"] == 19.0
        assert drain(wa, 0.3) == []  # 값이 안 바뀌면 다시 안 보냄
        assert drain(wc, 0.1) == []  # 고객은 robots를 받지 않음
    # 관리자 스냅샷에 마지막 값
    with client.websocket_connect("/ws/admin") as wa:
        assert recv(wa)["data"]["robots"][0]["x"] == 19.0


def test_central_change_sends_work_state(client, seeded):
    box = client.app.state.central
    acc = seeded["accounts"]["tester"]
    with client.websocket_connect("/ws/admin") as wa, \
            client.websocket_connect(f"/ws/customer?account_id={acc}") as wc:
        recv(wa), recv(wc)
        # ROS 실행기 스레드처럼 다른 스레드에서 넣는다
        t = threading.Thread(target=box.put, args=("SYNCING",))
        t.start()
        t.join(timeout=2)
        m = recv_until(wa, "work_state")
        assert "seq" not in m
        assert m["data"] == {"system_mode": "NORMAL", "central": "SYNCING"}
        assert drain(wa, 0.4) == []  # 값이 안 바뀌면 다시 안 보냄
        assert drain(wc, 0.1) == []  # 고객 화면에는 central을 보이지 않음
    with client.websocket_connect("/ws/admin") as wa:
        assert recv(wa)["data"]["work_state"]["central"] == "SYNCING"


def test_listener_reconnect_resends_snapshot(client, conn, seeded):
    app = client.app
    acc = seeded["accounts"]["tester"]
    with client.websocket_connect("/ws/admin") as wa, \
            client.websocket_connect(f"/ws/customer?account_id={acc}") as wc:
        recv(wa), recv(wc)
        old = app.state.listener_pid
        conn.execute("SELECT pg_terminate_backend(%s)", (old,))
        assert recv_until(wa, "snapshot", timeout=5)["data"]["work_state"]
        assert set(recv_until(wc, "snapshot", timeout=5)["data"]) == {
            "orders", "stock", "limits", "notice"}
        _wait_listener(app, old)
        # 다시 붙은 LISTEN으로 사건이 계속 온다
        _post(client, _body(seeded))
        assert recv_until(wa, "order_changed")["data"]["status"] == "RECEIVED"


def test_bad_event_does_not_stop_delivery(client, conn, seeded):
    with client.websocket_connect("/ws/admin") as wa:
        recv(wa)
        # data에 order_id가 없는 order_changed(route에서 KeyError) · 없는 seq 알림
        conn.execute("INSERT INTO ws_event (type, data) VALUES ('order_changed', '{}')")
        conn.execute("SELECT pg_notify('ifp_events', '{\"seq\": 99999999}')")
        conn.execute("SELECT pg_notify('ifp_events', 'not json')")
        oid = _post(client, _body(seeded)).json()["order_id"]
        assert recv_until(wa, "order_changed")["data"]["order_id"] == oid


def test_dead_client_does_not_block_others(client, seeded):
    with client.websocket_connect("/ws/admin") as alive:
        recv(alive)
        with client.websocket_connect("/ws/admin") as dead:
            recv(dead)
        oid = _post(client, _body(seeded)).json()["order_id"]
        assert recv_until(alive, "order_changed")["data"]["order_id"] == oid


# ---- Hub 단위: 느린 연결 ----
class FakeWS:
    def __init__(self, slow=False):
        self.slow = slow
        self.sent = []
        self.closed_with = None
        self._gone = asyncio.Event()

    async def send_json(self, msg):
        if self.slow:
            await asyncio.Event().wait()  # 영영 못 보냄
        self.sent.append(msg)

    async def receive(self):
        await self._gone.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def close(self, code=1000):
        self.closed_with = code
        self._gone.set()


def test_slow_client_closed():
    async def main():
        async def nothing(_client):
            return []

        hub = Hub(nothing)
        slow_ws, fast_ws = FakeWS(slow=True), FakeWS()
        slow = hub.register(slow_ws, "admin", None)
        fast = hub.register(fast_ws, "admin", None)
        tasks = [asyncio.create_task(hub.serve(c, [])) for c in (slow, fast)]
        for i in range(150):
            await hub.deliver([Outgoing("admin", None, {"type": "x", "n": i})])
            await asyncio.sleep(0)
        await asyncio.wait_for(tasks[0], 2)
        assert slow_ws.closed_with == 1013
        assert slow not in hub.clients and fast in hub.clients
        await asyncio.sleep(0.05)
        assert [m["n"] for m in fast_ws.sent] == list(range(150))
        assert fast_ws.closed_with is None
        await fast_ws.close()
        await asyncio.wait_for(tasks[1], 2)
        assert hub.clients == set()

    asyncio.run(main())


# ---- 일시 DB 오류 (설계 7.2) ----
def test_event_read_db_error_resends_snapshot(client, seeded, monkeypatch):
    from psycopg_pool import PoolTimeout

    from service_bridge.web import app as app_mod
    real = app_mod.read_event
    failed = []

    def flaky(conn, seq):
        if not failed:
            failed.append(seq)
            raise PoolTimeout("풀 대기 초과 흉내")
        return real(conn, seq)
    monkeypatch.setattr(app_mod, "read_event", flaky)
    acc = seeded["accounts"]["tester"]
    with client.websocket_connect("/ws/admin") as wa, \
            client.websocket_connect(f"/ws/customer?account_id={acc}") as wc:
        recv(wa), recv(wc)
        oid = _post(client, _body(seeded)).json()["order_id"]
        # 첫 사건(order_changed)은 읽기 실패 -> 모든 연결에 스냅샷을 다시 보내 맞춘다
        snap = recv_until(wa, "snapshot")["data"]
        assert [o["order_id"] for o in snap["orders"]] == [oid]
        assert [o["order_id"] for o in recv_until(wc, "snapshot")["data"]["orders"]] == [oid]
    assert failed


@pytest.mark.parametrize("make", [
    lambda: __import__("psycopg_pool").PoolTimeout("풀 대기 초과"),
    lambda: __import__("psycopg_pool").TooManyRequests("기다리는 요청이 너무 많음"),
    lambda: __import__("psycopg").errors.QueryCanceled("statement_timeout"),
])
def test_db_busy_is_503(client, seeded, monkeypatch, make):
    from service_bridge.web import app as app_mod

    def busy(*_a, **_k):
        raise make()
    monkeypatch.setattr(app_mod, "place_order", busy)
    r = _post(client, _body(seeded))
    assert r.status_code == 503
    assert r.json()["code"] == "BUSY"
    assert r.headers["Retry-After"] == "1"
    assert client.wake.count == 0


# ---- 화면 파일 (N-07) ----
def test_static_screens_served(client, seeded):
    r = client.get("/customer/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    r = client.get("/admin/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/common/live.js").status_code == 200
    # / 는 고객 화면으로 넘기는 index.html
    r = client.get("/")
    assert r.status_code == 200 and 'location.replace("customer/"' in r.text
    # 끝 / 없이 오면 폴더로 넘긴다
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code in (301, 307) and r.headers["location"].endswith("/admin/")
    assert client.get("/nope.html").status_code == 404


def test_static_mount_does_not_shadow_api(client, seeded):
    """마운트가 API 경로를 가로채지 않는다: 없는 API는 정적 404가 아니라 API 답."""
    r = client.get("/api/snapshot", params={"view": "nope"})
    assert (r.status_code, r.json()["code"]) == (400, "BAD_REQUEST")
    r = _post(client, _body(seeded))
    assert r.status_code == 202
