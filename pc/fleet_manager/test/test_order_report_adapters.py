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
from fleet_manager import fleet_manager_node as node  # noqa: E402


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


def test_force_ship_args_and_response_need_no_interface_type():
    assert adapters.force_ship_args(SimpleNamespace(order_id="O-003")) == "O-003"
    yes = adapters.force_ship_response(SimpleNamespace(accepted=None, reason=None), True, "x")
    assert (yes.accepted, yes.reason) == (True, "")
    no = adapters.force_ship_response(SimpleNamespace(accepted=None, reason=None), False,
                                      "NOT_IMPLEMENTED")
    assert (no.accepted, no.reason) == (False, "NOT_IMPLEMENTED")


def test_the_force_ship_server_waits_for_the_contract_key():
    assert node.force_ship_type(SimpleNamespace(entries={})) is None


def test_with_the_contract_key_the_server_uses_the_interface_once_it_is_built():
    try:
        from fleet_interfaces.srv import ForceShip as built
    except ImportError:
        built = None
    assert node.force_ship_type(SimpleNamespace(entries={"force_ship": object()})) is built
