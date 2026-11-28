"""넘기기 노드: 접수 순서 · 하나씩 · 앞 건 막힘 · wake · 시간 초과 · /fleet/robots (설계 6.2, 6.3),
/central/state 지켜보기 (central 재시작 설계 6.4)."""
import threading
import time
import uuid

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_interfaces.msg import CentralState, RobotStatus, RobotStatusArray  # noqa: E402
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
    """가짜 central: /central/state 발행(진짜처럼 바뀔 때 + 주기적으로) + 가짜 SubmitTrip 서버.

    answer: 받은 순번(0부터) -> (accepted, reason). serve=False면 SubmitTrip 서버 없음.
    state=None이면 /central/state를 내지 않는다."""

    def __init__(self, answer=lambda i, req: (True, ""), delay=0.0, serve=True,
                 state="RUNNING"):
        self.node = rclpy.create_node(f"fake_central_{uuid.uuid4().hex[:8]}")
        if serve:
            self.node.create_service(SubmitTrip, "/central/submit_trip", self._on)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._state_pub = self.node.create_publisher(CentralState, "/central/state", qos)
        self.boot_id = uuid.uuid4().hex
        self.state = state
        self.node.create_timer(0.5, self._publish_state)
        self._publish_state()
        self.received = []
        self.active = 0
        self.max_active = 0
        self._answer = answer
        self._delay = delay
        self._lock = threading.Lock()

    def set_state(self, state, boot_id=None):
        if boot_id is not None:
            self.boot_id = boot_id
        self.state = state
        self._publish_state()

    def _publish_state(self):
        if self.state is not None:
            self._state_pub.publish(CentralState(boot_id=self.boot_id, state=self.state))

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
    a = _place(conn, seeded, [("mentos", 5)], "k1")   # 운반 건 2개
    b = _place(conn, seeded, [("choco_bar", 1)], "k2")
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
    # central은 RUNNING인데 SubmitTrip 서버가 없음: NO_SERVICE 5번 뒤 30초 쉼, 뒤 건은 기다림
    a = _place(conn, seeded, [("choco_bar", 1)], "k1")
    b = _place(conn, seeded, [("choco_bar", 1)], "k2")
    central = FakeCentral(serve=False)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    q = "select attempts, last_result from trip_dispatch where trip_id = %s"
    assert _until(lambda: conn.execute(q, (a.trip_ids[0],)).fetchone()[0] >= 5, 12)
    time.sleep(1.5)  # 5번째 뒤에는 30초 쉼
    assert conn.execute(q, (a.trip_ids[0],)).fetchone() == (5, "NO_SERVICE")
    assert conn.execute(q, (b.trip_ids[0],)).fetchone() == (0, None)
    rows = conn.execute(
        "select trip_id, status from alert where code = 'DISPATCH_STALLED'").fetchall()
    assert rows == [(a.trip_ids[0], "OPEN")]


def test_wake_sends_without_waiting_timer(spin, pool, conn, seeded):
    central = FakeCentral()
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: central.node.count_clients("/central/submit_trip") == 1, 5)
    time.sleep(1.2)  # 빈 줄이 없으니 쉬는 시각 없이 타이머만 돌고 있는 상태
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    t0 = time.monotonic()
    bridge.wake_dispatch()
    assert _until(lambda: central.received == list(res.trip_ids), 0.5)
    assert time.monotonic() - t0 < 0.5


def test_rejection_is_final(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(answer=lambda i, req: (False, "INVALID_REQUEST"))
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: conn.execute(
        "select last_result from trip_dispatch where trip_id = %s and central_acked_at is not null",
        (res.trip_ids[0],)).fetchone() == ("INVALID_REQUEST",), 4)
    time.sleep(1.2)
    assert central.received == list(res.trip_ids)


def test_timeout_records_failure(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(delay=3.0)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    q = ("select attempts, last_result, central_acked_at from trip_dispatch "
         "where trip_id = %s")
    assert _until(lambda: conn.execute(q, (res.trip_ids[0],)).fetchone()[:2] == (1, "TIMEOUT"), 6)
    assert conn.execute(q, (res.trip_ids[0],)).fetchone()[2] is None


def test_no_service_records_failure(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(serve=False)
    bridge = _bridge(pool)
    spin(central.node, bridge)
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
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
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


def test_no_service_not_counted_right_after_start(spin, pool, conn, seeded):
    # 켜진 직후는 디스커버리 전이라 central이 없어 보여도 시도로 세지 않는다(재시작마다 +1 막기)
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(serve=False)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    q = "select attempts, last_result from trip_dispatch where trip_id = %s"
    time.sleep(0.8)
    assert conn.execute(q, (res.trip_ids[0],)).fetchone() == (0, None)
    # 첫 1초가 지나면 없는 서비스를 실패로 센다
    assert _until(lambda: conn.execute(q, (res.trip_ids[0],)).fetchone() == (1, "NO_SERVICE"), 3)


# ---- /central/state 지켜보기 (central 재시작 설계 6.4) ----
Q_ROW = "select attempts, last_result from trip_dispatch where trip_id = %s"


def test_no_submit_until_central_running(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(state="SYNCING")
    views = []
    bridge = _bridge(pool, on_central=views.append)
    spin(central.node, bridge)
    assert _until(lambda: views == ["SYNCING"], 5)
    time.sleep(2.0)  # 넘기기 확인이 여러 번 도는 동안
    assert central.received == []
    assert conn.execute(Q_ROW, (res.trip_ids[0],)).fetchone() == (0, None)
    central.set_state("RUNNING")
    t0 = time.monotonic()
    assert _until(lambda: central.received == list(res.trip_ids), 1.0)
    assert time.monotonic() - t0 < 1.0
    assert views == ["SYNCING", "RUNNING"]


def test_no_submit_without_central_state(spin, pool, conn, seeded):
    # /central/state를 한 번도 못 받음 = LOST: SubmitTrip 서버가 보여도 보내지 않고 세지 않는다
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(state=None)
    bridge = _bridge(pool)
    spin(central.node, bridge)
    time.sleep(2.5)
    assert central.received == []
    assert conn.execute(Q_ROW, (res.trip_ids[0],)).fetchone() == (0, None)


def test_state_goes_lost_when_messages_stop(spin, pool, conn, seeded):
    central = FakeCentral()
    views = []
    bridge = _bridge(pool, on_central=views.append)
    spin(central.node, bridge)
    assert _until(lambda: views == ["RUNNING"], 5)
    central.state = None  # 발행을 멈춤
    assert _until(lambda: views == ["RUNNING", "LOST"], 6)


def test_not_ready_reply_does_not_stall(spin, pool, conn, seeded):
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    central = FakeCentral(answer=lambda i, req: (False, "NOT_READY"))
    bridge = _bridge(pool)
    spin(central.node, bridge)
    assert _until(lambda: len(central.received) >= 6, 12)
    assert conn.execute(Q_ROW, (res.trip_ids[0],)).fetchone() == (0, "NOT_READY")
    assert conn.execute(
        "select count(*) from alert where code = 'DISPATCH_STALLED'").fetchone()[0] == 0


def _restart_alerts(conn):
    return conn.execute(
        "select level, detail from alert where code = 'CENTRAL_RESTART'").fetchall()


def test_boot_id_change_writes_one_alert(spin, pool, conn, seeded):
    central = FakeCentral()
    old = central.boot_id
    views = []
    bridge = _bridge(pool, on_central=views.append)
    spin(central.node, bridge)
    assert _until(lambda: views == ["RUNNING"], 5)
    time.sleep(1.0)  # 같은 boot_id가 여러 번 와도 알림 없음(처음 본 값은 기억만)
    assert _restart_alerts(conn) == []
    new = uuid.uuid4().hex
    central.set_state("CONNECTING", boot_id=new)
    assert _until(lambda: _restart_alerts(conn), 3)
    # LATCHED는 깊이 1이라 너무 빨리 바꾸면 가운데 값을 건너뛴다
    # (진짜 central은 단계마다 시간이 걸림)
    time.sleep(0.3)
    central.set_state("SYNCING")
    time.sleep(0.3)
    central.set_state("RUNNING")
    time.sleep(1.5)
    assert _restart_alerts(conn) == [("INFO", {"old_boot_id": old, "new_boot_id": new})]
    assert _until(lambda: views == ["RUNNING", "CONNECTING", "SYNCING", "RUNNING"], 3), views


def test_restart_ends_rest_right_away(spin, pool, conn, seeded):
    # 옛 central 때 5번 실패해 30초 쉬던 건도, 새 central이 RUNNING이 되면 곧 보낸다 (규칙 2)
    res = _place(conn, seeded, [("choco_bar", 1)], "k1")
    t = res.trip_ids[0]
    dead = FakeCentral(serve=False)
    bridge = _bridge(pool)
    spin(dead.node, bridge)
    assert _until(lambda: conn.execute(Q_ROW, (t,)).fetchone()[0] >= 5, 12)
    dead.state = None
    central = FakeCentral(state="RUNNING")  # 새 boot_id
    spin(central.node)
    assert _until(lambda: central.received == [t], 4)
    assert _until(lambda: conn.execute(
        "select attempts from trip_dispatch where trip_id = %s and central_acked_at is not null",
        (t,)).fetchone() == (1,), 3)
    assert conn.execute(
        "select status from alert where code = 'DISPATCH_STALLED'").fetchall() == [("RESOLVED",)]
