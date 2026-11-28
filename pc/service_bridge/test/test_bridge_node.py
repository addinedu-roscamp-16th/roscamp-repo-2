"""브리지 노드: mode 매개변수, 서버 한 바퀴, DB 준비 전에는 서버 없음 (설계 6.1)."""
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_interfaces.msg import SystemMode  # noqa: E402
from fleet_interfaces.srv import Hello, ListOpenTrips, ReportCommand, ReportTrip  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from service_bridge.core.db import make_pool  # noqa: E402
from service_bridge.ros.bridge_node import BridgeNode  # noqa: E402
from service_bridge.tools.trips import insert_trip  # noqa: E402

DOMAIN = 58


def _mode(value):
    return [Parameter("mode", value=value)]


@pytest.fixture
def ros():
    rclpy.init(domain_id=DOMAIN)
    try:
        yield
    finally:
        rclpy.shutdown()


@pytest.fixture
def spin(ros):
    """노드들을 MultiThreadedExecutor로 뒤에서 돌린다."""
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


def _call(client, request):
    assert client.wait_for_service(timeout_sec=10)
    future = client.call_async(request)
    deadline = time.monotonic() + 5
    while not future.done() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert future.done()
    return future.result()


@pytest.mark.parametrize("overrides", [[], _mode("test")])
def test_mode_must_be_given_and_valid(ros, pool, overrides):
    with pytest.raises(ValueError):
        BridgeNode(pool, parameter_overrides=overrides)


def test_report_trip_round_trip(spin, pool, conn, seeded):
    _, trip = insert_trip(conn, seeded["account_id"], {"mentos": 1})
    bridge = BridgeNode(pool, parameter_overrides=_mode("TEST"))
    peer = rclpy.create_node("bridge_test_peer")
    spin(bridge, peer)
    hello = peer.create_client(Hello, "/bridge/hello")
    report = peer.create_client(ReportTrip, "/bridge/report_trip")

    res = _call(hello, Hello.Request(node="fleet", boot_id="b1"))
    assert (res.accepted, res.epoch) == (True, 1)

    req = ReportTrip.Request(trip_id=trip, report_epoch=1, report_n=1, status="ASSIGNED",
                             robot_id="pode1")
    res = _call(report, req)
    assert (res.accepted, res.result) == (True, "APPLIED")
    res = _call(report, req)
    assert (res.accepted, res.result) == (True, "DUPLICATE")


def test_report_command_round_trip(spin, pool, conn, seeded):
    """/bridge/report_command가 열려 있고 응답 전에 final_result가 커밋된다 (IR-12)."""
    conn.execute(
        "INSERT INTO admin_action (account_id, action, robot_id, request_id, result) "
        "VALUES (%s, 'LOCK_RELEASE', 'pode1', 'R-1', 'SENT')", (seeded["account_id"],))
    bridge = BridgeNode(pool, parameter_overrides=_mode("TEST"))
    peer = rclpy.create_node("bridge_test_peer")
    spin(bridge, peer)
    client = peer.create_client(ReportCommand, "/bridge/report_command")

    res = _call(client, ReportCommand.Request(
        request_id="R-1", command="set_lock", target="pode1", result="ACCEPTED"))
    assert res.accepted is True
    assert conn.execute("select final_result from admin_action where request_id='R-1'"
                        ).fetchone()[0] == "ACCEPTED"


def test_list_board_trips_server_answers_after_db(spin, pool, conn, seeded):
    """DB가 붙은 뒤 /bridge/list_board_trips가 끝나지 않은 건을 접수 순서로 돌려준다 (설계 6.3)."""
    ids = []
    for kind in ("mentos", "mygummy"):
        _, trip = insert_trip(conn, seeded["account_id"], {kind: 1})
        conn.execute("INSERT INTO trip_dispatch (trip_id) VALUES (%s)", (trip,))
        ids.append(trip)
    bridge = BridgeNode(pool, parameter_overrides=_mode("TEST"))
    peer = rclpy.create_node("bridge_test_peer")
    spin(bridge, peer)
    client = peer.create_client(ListOpenTrips, "/bridge/list_board_trips")

    res = _call(client, ListOpenTrips.Request(epoch=0))
    assert res.accepted is True
    assert [t.trip_id for t in res.trips] == ids
    assert [(i.kind, i.count) for i in res.trips[0].items] == [("mentos", 1)]
    assert res.trips[0].status == "RECEIVED"


def test_no_servers_until_db_ready(spin):
    dead = make_pool("postgresql://postgres:x@127.0.0.1:1/none", open=False)
    dead.open(wait=False)
    try:
        bridge = BridgeNode(dead, parameter_overrides=_mode("TEST"))
        spin(bridge)
        time.sleep(0.5)  # 발행 뒤에 붙는 구독자 (LATCHED라 받아야 함)
        peer = rclpy.create_node("bridge_test_late")
        modes = []
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        peer.create_subscription(SystemMode, "/system/mode", modes.append, qos)
        spin(peer)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
        assert [m.mode for m in modes] == ["TEST"]
        services = {name for name, _ in peer.get_service_names_and_types()}
        assert "/bridge/report_trip" not in services
        assert "/bridge/report_command" not in services
        assert "/bridge/hello" not in services
    finally:
        dead.close(timeout=1.0)
