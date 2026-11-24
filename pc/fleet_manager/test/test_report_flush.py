"""보고 재전송(IR-41)과 /fleet/robots 주기 검사. ROS 그래프 없이 노드 메서드만 부른다."""
from functools import partial
from types import SimpleNamespace

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.commands import ReportOrderCmd  # noqa: E402
from fleet_interfaces.srv import ReportOrder  # noqa: E402
from fleet_manager.fleet_manager_node import FleetManagerNode  # noqa: E402
from fleet_manager.goal_tracker import ReportOutbox  # noqa: E402
from rclpy.task import Future  # noqa: E402


def test_a_failed_reply_keeps_the_report_for_sending_again():
    report = SimpleNamespace(order_id="O-001")
    outbox = ReportOutbox(rand=lambda: 0.5)
    outbox.add(report)
    [(report_id, _)] = outbox.due(0.0, True, 2.0, 4)
    future = Future()
    future.set_exception(RuntimeError("central went away"))
    node = SimpleNamespace(_reports=outbox, get_logger=lambda: SimpleNamespace(warning=print))
    FleetManagerNode._report_answered(node, report_id, report, future)
    assert outbox.due(2.0, True, 2.0, 4) == [(report_id, report)]


class _ReportClient:
    """call_async가 답 없는 future를 내고 지운 요청을 적는다."""

    def __init__(self):
        self.sent = []
        self.removed = []

    def service_is_ready(self):
        return True

    def call_async(self, request):
        future = Future()
        self.sent.append(future)
        return future

    def remove_pending_request(self, future):
        self.removed.append(future)


def report_node(limit=4):
    outbox = ReportOutbox(rand=lambda: 0.5)
    events = []
    node = SimpleNamespace(
        _reports=outbox, _report_client=_ReportClient(),
        core=SimpleNamespace(params=SimpleNamespace(report_retry_s=2.0,
                                                    report_max_in_flight=limit)),
        get_logger=lambda: SimpleNamespace(warning=print),
        _now=lambda: 0.0, _dispatch=lambda command, now: events.append(command.code))
    node._report_answered = partial(FleetManagerNode._report_answered, node)
    return node, events


def report_cmd(order_id):
    return ReportOrderCmd(order_id, "pode1", "SHIPPED", {"A": 1}, {}, "")


def answer(future, accepted):
    future.set_result(ReportOrder.Response(accepted=accepted))


def test_a_resent_report_removes_only_requests_past_the_reply_limit():
    """IR-41: 응답 한도(3 s)를 넘긴 요청만 클라이언트에서 지워 대기 표가 늘지 않는다."""
    node, _ = report_node()
    node._reports.add(report_cmd("O-001"))
    client = node._report_client
    FleetManagerNode._flush_reports(node, 0.0)
    FleetManagerNode._flush_reports(node, 2.0)
    assert client.removed == [], "the first request is still inside the reply limit"
    FleetManagerNode._flush_reports(node, 4.0)
    assert client.removed == client.sent[:1]
    answer(client.sent[1], True)  # 한도 안에 남은 앞선 요청의 답이 끝낸다
    assert not node._reports._items
    assert client.removed == [client.sent[0], client.sent[2]], "the other waiting request goes"


def test_a_request_past_the_reply_limit_is_dropped_without_a_resend():
    node, _ = report_node()
    node.core.params.report_retry_s = 10.0
    node._reports.add(report_cmd("O-001"))
    client = node._report_client
    FleetManagerNode._flush_reports(node, 0.0)
    FleetManagerNode._flush_reports(node, 2.9)
    assert client.removed == []
    FleetManagerNode._flush_reports(node, 3.0)
    assert client.removed == client.sent[:1] and len(client.sent) == 1


def test_a_late_answer_to_a_done_report_is_harmless():
    node, events = report_node()
    node._reports.add(report_cmd("O-001"))
    client = node._report_client
    FleetManagerNode._flush_reports(node, 0.0)
    FleetManagerNode._flush_reports(node, 2.0)
    answer(client.sent[1], True)
    answer(client.sent[0], False)  # 지웠어도 늦게 끝난 future
    assert events == [] and not node._reports._items
    assert client.removed == client.sent[:1]
    FleetManagerNode._flush_reports(node, 10.0)
    assert len(client.sent) == 2


def test_the_node_keeps_reports_over_the_limit_for_a_later_flush():
    node, _ = report_node(limit=1)
    for order in ("O-001", "O-002"):
        node._reports.add(report_cmd(order))
    client = node._report_client
    FleetManagerNode._flush_reports(node, 0.0)
    assert len(client.sent) == 1
    answer(client.sent[0], True)
    FleetManagerNode._flush_reports(node, 0.1)
    assert len(client.sent) == 2


@pytest.mark.parametrize("bad", ["0.0", "-5.0", ".inf"])
def test_a_robots_rate_that_is_not_positive_is_refused(bad):
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", f"robots_hz:={bad}"])
    try:
        with pytest.raises(ValueError, match="robots_hz"):
            FleetManagerNode()
    finally:
        rclpy.shutdown()
