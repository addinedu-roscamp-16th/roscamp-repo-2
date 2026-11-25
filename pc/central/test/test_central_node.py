"""central 노드: 주문판 발행자 · 서비스가 contract대로 열리고 접수 · 보고가 주문판에 반영된다."""
import os
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

os.environ.setdefault("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST")

from central.central_node import CentralNode  # noqa: E402
from fleet_interfaces.msg import OrderBoard, OrderItem  # noqa: E402
from fleet_interfaces.srv import ReportOrder, SubmitTrip  # noqa: E402
from rclpy.executors import SingleThreadedExecutor  # noqa: E402
from rclpy.qos import DurabilityPolicy  # noqa: E402


def test_the_node_opens_latched_board_and_both_services():
    rclpy.init(domain_id=57)
    try:
        node = CentralNode()
        pubs = {p.topic_name: p for p in node.publishers}
        assert pubs["/central/orders"].qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL
        services = {name for name, _ in node.get_service_names_and_types()}
        assert {"/central/submit_trip", "/central/report_order"} <= services
        node.destroy_node()
    finally:
        rclpy.shutdown()


def _call(ex, client, request):
    future = client.call_async(request)
    deadline = time.monotonic() + 5
    while not future.done() and time.monotonic() < deadline:
        ex.spin_once(timeout_sec=0.1)
    assert future.done()
    return future.result()


def test_submit_sums_same_kind_and_report_removes():
    rclpy.init(domain_id=57)
    try:
        node = CentralNode()
        peer = rclpy.create_node("central_test_peer")
        ex = SingleThreadedExecutor()
        ex.add_node(node)
        ex.add_node(peer)
        boards = []
        peer.create_subscription(OrderBoard, "/central/orders", boards.append, 10)
        submit = peer.create_client(SubmitTrip, "/central/submit_trip")
        report = peer.create_client(ReportOrder, "/central/report_order")
        assert submit.wait_for_service(timeout_sec=5)
        request = SubmitTrip.Request(trip_id="t1", order_id="o1", trip_index=0, trip_count=1)
        request.items = [OrderItem(kind="A", count=1), OrderItem(kind="A", count=2)]
        request.submitted_at.sec = 77
        assert _call(ex, submit, request).accepted is True
        last = boards[-1]
        assert [o.order_id for o in last.orders] == ["t1"]
        assert [(i.kind, i.count) for i in last.orders[0].items] == [("A", 3)]
        assert last.orders[0].created_at.sec == 77
        done = ReportOrder.Request(order_id="t1", status=ReportOrder.Request.STATUS_SHIPPED)
        assert _call(ex, report, done).accepted is True
        deadline = time.monotonic() + 3
        while boards[-1].orders and time.monotonic() < deadline:
            ex.spin_once(timeout_sec=0.1)
        assert boards[-1].orders == []
        node.destroy_node()
        peer.destroy_node()
    finally:
        rclpy.shutdown()


def test_report_with_non_final_status_is_rejected_and_warned():
    rclpy.init(domain_id=57)
    try:
        node = CentralNode()
        warns = []

        class _Log:
            def warn(self, msg):
                warns.append(msg)

            def info(self, msg):
                pass

        node.get_logger = lambda: _Log()
        peer = rclpy.create_node("central_test_peer")
        ex = SingleThreadedExecutor()
        ex.add_node(node)
        ex.add_node(peer)
        report = peer.create_client(ReportOrder, "/central/report_order")
        assert report.wait_for_service(timeout_sec=5)
        bad = ReportOrder.Request(order_id="t9", status="CANCELLED")  # 철자 오류(CANCELED가 맞음)
        assert _call(ex, report, bad).accepted is False
        assert len(warns) == 1
        assert "t9" in warns[0] and "CANCELLED" in warns[0] and "CANCELED" in warns[0]
        assert not any("모르는 order_id" in w for w in warns)
        node.destroy_node()
        peer.destroy_node()
    finally:
        rclpy.shutdown()
