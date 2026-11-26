"""한 바퀴(웹 포함): 주문 POST → 브리지 넘기기 → central → 가짜 fleet 보고 → 고객 WS.

설계 10 "한 바퀴", 목표 1 · 2 · 3. 웹은 TestClient로(진짜 uvicorn 없음),
브리지 노드와 앱은 main.build로 묶는다(실행 때와 같은 묶음).
"""
import contextlib
import threading
import time
import uuid
import warnings

import pytest

# ---- starlette TestClient가 httpx를 쓸 때 내는 사용 중단 경고는 시험 출력에서 뺀다 ----
warnings.filterwarnings("ignore", message=".*httpx.*starlette.testclient.*")

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")
pytest.importorskip("central")

from central.central_node import CentralNode  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from service_bridge.core.db import make_pool  # noqa: E402
from service_bridge.main import build  # noqa: E402
from service_bridge.tools.fake_fleet import FakeFleetNode  # noqa: E402
from test_reports import count, trip_state  # noqa: E402
from test_web import _wait_listener, recv, recv_until  # noqa: E402

DOMAIN = 58
LOOP_WAIT = 20.0  # 한 바퀴 전체를 기다리는 최대 시간(초)


@pytest.fixture
def web_pool(pool):
    # 웹 풀은 브리지 풀과 따로 (설계 6.1)
    p = make_pool(pool.conninfo)
    yield p
    p.close()


class Loop:
    """도메인 58에서 브리지(+ 웹 앱) · 그 밖 노드를 실행기 스레드로 돌린다."""

    def __init__(self, pool, web_pool, mode, extra_nodes=()):
        self.ex = MultiThreadedExecutor(num_threads=4)
        self.nodes = []
        self.thread = None
        self.node, self.app = build(
            pool, web_pool, pool.conninfo,
            parameter_overrides=[Parameter("mode", value=mode)])
        self.nodes.append(self.node)
        for make in extra_nodes:
            self.nodes.append(make())
        for n in self.nodes:
            self.ex.add_node(n)
        self.thread = threading.Thread(target=self.ex.spin, daemon=True)
        self.thread.start()

    def close(self):
        self.node.close_servers()
        self.ex.shutdown(timeout_sec=2.0)
        if self.thread is not None:
            self.thread.join(timeout=3)
        for n in self.nodes:
            n.destroy_node()


@contextlib.contextmanager
def running(pool, web_pool, mode, extra_nodes=()):
    rclpy.init(domain_id=DOMAIN)
    loop = None
    try:
        loop = Loop(pool, web_pool, mode, extra_nodes)
        yield loop
    finally:
        if loop is not None:
            loop.close()
        rclpy.try_shutdown()


def _fold(texts):
    """같은 말이 이어지면 하나로 접는다."""
    out = []
    for t in texts:
        if not out or out[-1] != t:
            out.append(t)
    return out


def _post(client, body):
    return client.post("/api/orders", json=body,
                       headers={"Idempotency-Key": str(uuid.uuid4())})


def test_order_to_shipped_over_ws(pool, web_pool, conn, seeded):
    fleet_box = []

    def make_fleet():
        fleet_box.append(FakeFleetNode(parameter_overrides=[
            Parameter("step_s", value=0.2), Parameter("duplicate_every_report", value=True)]))
        return fleet_box[0]

    with running(pool, web_pool, "NORMAL", (CentralNode, make_fleet)) as loop:
        fleet = fleet_box[0]
        acc = seeded["accounts"]["tester"]
        p = seeded["products"]
        with TestClient(loop.app) as client:
            _wait_listener(loop.app)
            with client.websocket_connect(f"/ws/customer?account_id={acc}") as ws:
                assert recv(ws)["type"] == "snapshot"
                # 4개 → 운반 건 하나 (설계 5.3)
                r = _post(client, {"account_id": acc, "items": [
                    {"product_id": p["A"], "qty": 2}, {"product_id": p["B"], "qty": 1},
                    {"product_id": p["C"], "qty": 1}]})
                assert r.status_code == 202, r.text
                order_id = r.json()["order_id"]
                (trip_id,) = r.json()["trip_ids"]

                # ---- 목표 1: 고객 WS의 말 순서 ----
                texts = []
                deadline = time.monotonic() + LOOP_WAIT
                while "출고 완료" not in texts:
                    left = deadline - time.monotonic()
                    assert left > 0, f"출고 완료가 오지 않음: {texts}"
                    m = recv_until(ws, "order_changed", left)
                    assert m["data"]["order_id"] == order_id
                    texts.append(m["data"]["status_text"])
                assert _fold(texts) == ["접수됨", "준비 중", "출고 중", "출고 완료"]

        # ---- 목표 2: 보고 반영 (완료 기준 2 · 3) ----
        deadline = time.monotonic() + LOOP_WAIT
        while fleet.order_reports < 1 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert fleet.order_reports == 1
        # fake_fleet의 report_n은 노드 전체에서 1부터 늘므로 운반 건 하나면 마지막이 4
        assert trip_state(conn, trip_id)[:3] == ("SHIPPED", fleet.epoch, 4)
        # 보고마다 두 번 보냄: 두 번째 4개는 DUPLICATE, 이력은 접수 1줄 + 보고 4줄
        assert fleet.duplicates == 4
        assert count(conn, "status_history where trip_id=%s", trip_id) == 1 + 4
        assert count(conn, "trip_dispatch where trip_id=%s and central_acked_at is not null",
                     trip_id) == 1


def test_test_mode_blocks_orders(pool, web_pool, seeded):  # 목표 3
    with running(pool, web_pool, "TEST") as loop:
        assert loop.node.mode == "TEST"
        acc = seeded["accounts"]["tester"]
        with TestClient(loop.app) as client:
            _wait_listener(loop.app)
            r = _post(client, {"account_id": acc,
                               "items": [{"product_id": seeded["products"]["A"], "qty": 1}]})
            assert (r.status_code, r.json()["code"]) == (503, "MAINTENANCE")
            with client.websocket_connect(f"/ws/customer?account_id={acc}") as ws:
                assert recv(ws)["type"] == "snapshot"
                m = recv(ws)
                assert m["type"] == "customer_notice"
                assert m["data"] == {"kind": "MAINTENANCE", "message": "점검 중"}
