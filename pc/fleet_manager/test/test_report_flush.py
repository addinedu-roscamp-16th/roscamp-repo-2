"""보고 보내기(Hello · ReportTrip · ReportOrder, IR-41)와 /fleet/robots 주기 검사.

ROS 그래프 없이 노드 메서드만 부른다.
"""
from functools import partial
from types import SimpleNamespace

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.commands import ReportOrderCmd, ReportTripCmd  # noqa: E402
from fleet_interfaces.srv import Hello, ReportOrder, ReportTrip  # noqa: E402
from fleet_manager.fleet_manager_node import FleetManagerNode  # noqa: E402
from fleet_manager.report_queue import HELLO, ORDER, TRIP, ReportQueue  # noqa: E402
from rclpy.task import Future  # noqa: E402


class _ReportClient:
    """call_async가 답 없는 future를 내고 보낸 요청과 지운 요청을 적는다."""

    def __init__(self):
        self.sent = []
        self.requests = []
        self.removed = []

    def service_is_ready(self):
        return True

    def call_async(self, request):
        future = Future()
        self.sent.append(future)
        self.requests.append(request)
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


def report_node(limit=4):
    events = []
    logs = SimpleNamespace(warning=print, info=print)
    node = SimpleNamespace(
        _reports=ReportQueue("boot-1", 1.0, 10.0, 3.0, limit, rand=lambda: 0.5),
        _report_clients={HELLO: _ReportClient(), TRIP: _ReportClient(), ORDER: _ReportClient()},
        _pending={}, _boot_id="boot-1",
        get_logger=lambda: logs,
        _now=lambda: node.now, now=0.0,
        _dispatch=lambda command, now: events.append(command.code), events=events,
        _post=lambda handler, *args: handler(*args))  # 받은 편지함을 바로 비운 것과 같다
    node._report_answered = partial(FleetManagerNode._report_answered, node)
    return node


def answer(future, response):
    future.set_result(response)


def hello(node, now=0.0):
    FleetManagerNode._flush_reports(node, now)
    answer(node._report_clients[HELLO].sent[-1], Hello.Response(accepted=True, epoch=7))


def shipped(order_id="O-001"):
    return ReportOrderCmd(order_id, "pode1", "SHIPPED", {"A": 1}, {}, "")


def test_assigned_goes_to_the_bridge_after_hello():
    node = report_node()
    FleetManagerNode._dispatch(node, ReportTripCmd("T-1", "pode1", "ASSIGNED", {}, {}), 0.0)
    FleetManagerNode._flush_reports(node, 0.0)
    assert [len(c.sent) for c in node._report_clients.values()] == [1, 0, 0]   # hello만
    answer(node._report_clients[HELLO].sent[0], Hello.Response(accepted=True, epoch=7))
    FleetManagerNode._flush_reports(node, 0.1)
    [req] = node._report_clients[TRIP].requests
    assert (req.trip_id, req.status, req.report_epoch, req.report_n) == ("T-1", "ASSIGNED", 7, 1)


def test_shipped_reaches_central_after_the_bridge_accepts():
    node = report_node()
    hello(node)
    FleetManagerNode._dispatch(node, shipped(), 0.1)
    FleetManagerNode._flush_reports(node, 0.1)
    assert node._report_clients[ORDER].sent == [], "central waits for the bridge"
    [req] = node._report_clients[TRIP].requests
    assert (req.trip_id, req.status) == ("O-001", "SHIPPED")
    answer(node._report_clients[TRIP].sent[0], ReportTrip.Response(accepted=True))
    FleetManagerNode._flush_reports(node, 0.2)
    [req] = node._report_clients[ORDER].requests
    assert (req.order_id, req.status) == ("O-001", "SHIPPED")
    answer(node._report_clients[ORDER].sent[0], ReportOrder.Response(accepted=True))
    FleetManagerNode._flush_reports(node, 5.0)
    assert [len(c.sent) for c in node._report_clients.values()] == [1, 1, 1]
    assert node.events == [] and node._pending == {}


def test_a_refused_order_report_raises_the_event():
    node = report_node()
    hello(node)
    FleetManagerNode._dispatch(node, shipped(), 0.1)
    FleetManagerNode._flush_reports(node, 0.1)
    answer(node._report_clients[TRIP].sent[0], ReportTrip.Response(accepted=True))
    FleetManagerNode._flush_reports(node, 0.2)
    answer(node._report_clients[ORDER].sent[0], ReportOrder.Response(accepted=False))
    assert node.events == ["ORDER_REPORT_REFUSED"]
    FleetManagerNode._flush_reports(node, 20.0)
    assert len(node._report_clients[ORDER].sent) == 1, "a refused report is not sent again"


def test_a_request_past_the_reply_limit_is_removed_from_its_client():
    node = report_node()
    client = node._report_clients[HELLO]
    FleetManagerNode._flush_reports(node, 0.0)
    FleetManagerNode._flush_reports(node, 2.9)
    assert client.removed == []
    FleetManagerNode._flush_reports(node, 3.0)
    assert client.removed == client.sent[:1]
    assert node._pending == {}


def test_a_failed_reply_keeps_the_report_for_sending_again():
    node = report_node()
    hello(node)
    FleetManagerNode._dispatch(node, shipped(), 0.0)
    FleetManagerNode._flush_reports(node, 0.0)
    client = node._report_clients[TRIP]
    client.sent[0].set_exception(RuntimeError("bridge went away"))
    FleetManagerNode._flush_reports(node, 0.9)
    assert len(client.sent) == 1, "the first interval is 1 s"
    FleetManagerNode._flush_reports(node, 1.0)
    assert len(client.sent) == 2
    assert client.requests[1].report_n == client.requests[0].report_n, "the same report again"


@pytest.mark.parametrize("bad", ["0.0", "-5.0", ".inf"])
def test_a_robots_rate_that_is_not_positive_is_refused(bad):
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", f"robots_hz:={bad}"])
    try:
        with pytest.raises(ValueError, match="robots_hz"):
            FleetManagerNode()
    finally:
        rclpy.shutdown()
