"""한 바퀴: 브리지 + central + 가짜 fleet + submit_trip을 한 시험 도메인에서.

설계 9, 완료 기준 2 · 3.
"""
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")
pytest.importorskip("central")

from central.central_node import CentralNode  # noqa: E402
from fleet_interfaces.msg import OrderBoard  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from service_bridge.ros.bridge_node import BridgeNode  # noqa: E402
from service_bridge.tools.common import load_default_contract  # noqa: E402
from service_bridge.tools.fake_fleet import FakeFleetNode  # noqa: E402
from service_bridge.tools.submit_trip import submit  # noqa: E402
from service_bridge.tools.trips import insert_trip  # noqa: E402
from test_reports import count, trip_state  # noqa: E402

DOMAIN = 59


def test_full_loop(pool, conn, seeded):
    rclpy.init(domain_id=DOMAIN)
    ex = MultiThreadedExecutor(num_threads=4)
    nodes = []
    thread = None
    try:
        nodes.append(BridgeNode(pool, parameter_overrides=[Parameter("mode", value="TEST")]))
        nodes.append(CentralNode())
        fleet = FakeFleetNode(parameter_overrides=[
            Parameter("step_s", value=0.2), Parameter("duplicate_every_report", value=True)])
        nodes.append(fleet)
        peer = rclpy.create_node("loop_test_peer")
        nodes.append(peer)
        boards = []
        contract = load_default_contract()
        peer.create_subscription(OrderBoard, contract.name("order_board"), boards.append, 10)
        for n in nodes:
            ex.add_node(n)
        thread = threading.Thread(target=ex.spin, daemon=True)
        thread.start()

        order_id, trip_id = insert_trip(conn, seeded["account_id"], {"A": 1, "C": 1})
        accepted, reason = submit(peer, contract, order_id, trip_id, {"A": 1, "C": 1})
        assert (accepted, reason) == (True, "")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if trip_state(conn, trip_id)[0] == "SHIPPED" and fleet.order_reports == 1 \
                    and boards and not boards[-1].orders:
                break
            time.sleep(0.1)
        assert trip_state(conn, trip_id)[:3] == ("SHIPPED", 1, 4)
        assert count(conn, "status_history where trip_id=%s", trip_id) == 5
        assert boards[-1].orders == []
        assert fleet.duplicates == 4
    finally:
        ex.shutdown(timeout_sec=2.0)
        if thread is not None:
            thread.join(timeout=3)
        for n in nodes:
            n.destroy_node()
        rclpy.try_shutdown()
