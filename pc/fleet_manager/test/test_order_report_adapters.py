"""주문 보고 변환의 보류 사유, 그리고 강제 출고 인터페이스가 없을 때의 서버."""
from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.commands import (  # noqa: E402
    CANCELED,
    HELD,
    HOLD_ARM_FAULT,
    HOLD_ROBOT_FAULT,
    SHIPPED,
    ReportOrderCmd,
)
from fleet_interfaces.srv import ReportOrder  # noqa: E402
from fleet_manager import adapters  # noqa: E402


def test_report_request_leaves_the_hold_reason_empty_by_default():
    cmd = ReportOrderCmd("O-001", "pode1", SHIPPED, {"A": 1}, {}, "")
    assert adapters.report_request(cmd).hold_reason == ""


@pytest.mark.parametrize("reason", [HOLD_ARM_FAULT, HOLD_ROBOT_FAULT])
def test_report_request_carries_the_hold_reason(reason):
    cmd = ReportOrderCmd("O-001", "pode1", HELD, {"A": 1}, {"A": 1}, "", reason)
    request = adapters.report_request(cmd)
    assert (request.status, request.hold_reason) == (HELD, reason)


def test_the_status_and_hold_words_match_the_service_constants():
    req = ReportOrder.Request
    assert (req.STATUS_CANCELED, req.HOLD_ARM_FAULT, req.HOLD_ROBOT_FAULT) == (
        CANCELED, HOLD_ARM_FAULT, HOLD_ROBOT_FAULT)


# 노드 없이 인터페이스 설정만 돌려 어떤 서비스를 여는지 본다
class _Interfaces:
    def __init__(self, entries):
        self.contract = SimpleNamespace(name=lambda key, **_: f"/fleet/{key}", entries=entries)
        self.site = SimpleNamespace(stations={})
        self.services = []
        self._on_order_board = self._on_confirm_clear = self._on_reinstate = object()
        self._on_force_ship = object()
        self._on_system_mode = object()
        self._core_group = self._inbox_group = None  # IR-30 콜백 그룹 자리

    def _post(self, handler, *args):
        handler(*args)

    def _qos(self, key):
        return None

    def create_publisher(self, *args):
        return None

    def create_subscription(self, *args, **_):
        return None

    def create_client(self, *args, **_):
        return None

    def create_service(self, kind, name, callback, **_):
        self.services.append((kind, name, callback))


