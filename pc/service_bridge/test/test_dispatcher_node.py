"""넘기기 노드: 접수 순서 · 하나씩 · 앞 건 막힘 · wake · 시간 초과 · /fleet/robots (설계 6.2, 6.3)."""
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_interfaces.msg import RobotStatus, RobotStatusArray  # noqa: E402
from fleet_interfaces.srv import SubmitTrip  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from service_bridge.core.intake import place_order  # noqa: E402
from service_bridge.core.types import OrderLine, OrderRequest  # noqa: E402
from service_bridge.ros.bridge_node import BridgeNode  # noqa: E402

DOMAIN = 59


@pytest.fixture
def ros():
    rclpy.init(domain_id=DOMAIN)
    try:
        yield
    finally:
        rclpy.shutdown()


@pytest.fixture
def spin(ros):
    ex = MultiThreadedExecutor(num_threads=4)
    nodes = []

    def add(*ns):
        for n in ns:
            nodes.append(n)
            ex.add_node(n)

    thread = threading.Thread(target=ex.spin, daemon=True)
    thread.start()
    try:
        yield add
    finally:
        ex.shutdown(timeout_sec=2.0)
        thread.join(timeout=3)
        for n in nodes:
            n.destroy_node()


class FakeCentral:
    """가짜 SubmitTrip 서버. answers: 받은 순번(0부터) -> (accepted, reason) 만드는 함수."""

    def __init__(self, answer=lambda i, req: (True, ""), delay=0.0):
        self.node = rclpy.create_node("fake_central")
        self.node.create_service(SubmitTrip, "/central/submit_trip", self._on)
        self.received = []
        self.active = 0
        self.max_active = 0
        self._answer = answer
        self._delay = delay
        self._lock = threading.Lock()

    def _on(self, req, res):
        with self._lock:
            i = len(self.received)
            self.received.append(req.trip_id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self._delay)
        res.accepted, res.reason = self._answer(i, req)
        with self._lock:
            self.active -= 1
        return res


def _place(conn, seeded, kind_qty, key):
    items = tuple(OrderLine(seeded["products"][k], q) for k, q in kind_qty)
    res = place_order(conn, OrderRequest(seeded["accounts"]["tester"], items), key)
    assert not hasattr(res, "code"), res
    return res


def _until(cond, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _bridge(pool, **kw):
    return BridgeNode(pool, parameter_overrides=[Parameter("mode", value="TEST")], **kw)


def test_sends_in_order_one_at_a_time(spin, pool, conn, seeded):
    a = _place(conn, seeded, [("A", 5)], "k1")   # 운반 건 2개
    b = _place(conn, seeded, [("B", 1)], "k2")
    central = FakeCentral(delay=0.2)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    want = [*a.trip_ids, *b.trip_ids]
    assert _until(lambda: len(central.received) == len(want), 8)
    time.sleep(0.5)
    assert central.received == want
    assert central.max_active == 1
    assert _until(lambda: conn.execute(
        "select count(*) from trip_dispatch where central_acked_at is null").fetchone()[0] == 0, 3)


def test_head_blocks_followers(spin, pool, conn, seeded):
    a = _place(conn, seeded, [("B", 1)], "k1")
    _place(conn, seeded, [("B", 1)], "k2")
    central = FakeCentral(answer=lambda i, req: (False, "NOT_READY"))
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: len(central.received) >= 5, 12)
    time.sleep(1.5)  # 5번째 뒤에는 30초 쉼
    assert central.received == [a.trip_ids[0]] * 5
    rows = conn.execute(
        "select trip_id, status from alert where code = 'DISPATCH_STALLED'").fetchall()
    assert rows == [(a.trip_ids[0], "OPEN")]


def test_wake_sends_without_waiting_timer(spin, pool, conn, seeded):
    central = FakeCentral()
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: central.node.count_clients("/central/submit_trip") == 1, 5)
    time.sleep(1.2)  # 빈 줄이 없으니 쉬는 시각 없이 타이머만 돌고 있는 상태
    res = _place(conn, seeded, [("B", 1)], "k1")
    t0 = time.monotonic()
    bridge.wake_dispatch()
    assert _until(lambda: central.received == list(res.trip_ids), 0.5)
    assert time.monotonic() - t0 < 0.5


def test_rejection_is_final(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("B", 1)], "k1")
    central = FakeCentral(answer=lambda i, req: (False, "INVALID_REQUEST"))
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: conn.execute(
        "select last_result from trip_dispatch where trip_id = %s and central_acked_at is not null",
        (res.trip_ids[0],)).fetchone() == ("INVALID_REQUEST",), 4)
    time.sleep(1.2)
    assert central.received == list(res.trip_ids)


def test_timeout_records_failure(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("B", 1)], "k1")
    central = FakeCentral(delay=3.0)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    q = ("select attempts, last_result, central_acked_at from trip_dispatch "
         "where trip_id = %s")
    assert _until(lambda: conn.execute(q, (res.trip_ids[0],)).fetchone()[:2] == (1, "TIMEOUT"), 6)
    assert conn.execute(q, (res.trip_ids[0],)).fetchone()[2] is None


def test_no_service_records_failure(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("B", 1)], "k1")
    bridge = _bridge(pool)
    spin(bridge)
    assert _until(lambda: conn.execute(
        "select last_result from trip_dispatch where trip_id = %s",
        (res.trip_ids[0],)).fetchone() == ("NO_SERVICE",), 4)


def test_robots_callback(spin, pool):
    got = []
    bridge = _bridge(pool, on_robots=got.append)
    pub_node = rclpy.create_node("robots_pub")
    qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    pub = pub_node.create_publisher(RobotStatusArray, "/fleet/robots", qos)
    pub.publish(RobotStatusArray(robots=[RobotStatus(robot_id="pode1", order_id="T-1")]))
    spin(bridge, pub_node)
    assert _until(lambda: got, 5)
    assert isinstance(got[0], list) and got[0][0]["robot_id"] == "pode1"
    assert got[0][0]["trip_id"] == "T-1"


def test_reply_while_waking_sends_once(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("B", 1)], "k1")
    central = FakeCentral(delay=0.3)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    end = time.monotonic() + 1.5
    while time.monotonic() < end:  # 응답이 오가는 동안 wake를 계속 울림
        bridge.wake_dispatch()
        time.sleep(0.01)
    assert _until(lambda: conn.execute(
        "select attempts from trip_dispatch where trip_id = %s and central_acked_at is not null",
        (res.trip_ids[0],)).fetchone() == (1,), 3)
    assert central.received == list(res.trip_ids)
