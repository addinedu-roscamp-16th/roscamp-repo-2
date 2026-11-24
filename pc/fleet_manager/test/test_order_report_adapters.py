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


def test_with_the_contract_key_but_no_built_type_the_server_stays_closed(monkeypatch):
    import fleet_interfaces.srv as srv
    monkeypatch.delattr(srv, "ForceShip", raising=False)
    assert node.force_ship_type(SimpleNamespace(entries={"force_ship": object()})) is None


def test_with_the_contract_key_and_the_built_type_the_server_uses_it(monkeypatch):
    import fleet_interfaces.srv as srv
    built = object()
    monkeypatch.setattr(srv, "ForceShip", built, raising=False)
    assert node.force_ship_type(SimpleNamespace(entries={"force_ship": object()})) is built
    assert node.force_ship_type(SimpleNamespace(entries={})) is None


# 노드 없이 인터페이스 설정만 돌려 어떤 서비스를 여는지 본다
class _Interfaces:
    def __init__(self, entries):
        self.contract = SimpleNamespace(name=lambda key, **_: f"/fleet/{key}", entries=entries)
        self.site = SimpleNamespace(stations={})
        self.services = []
        self._on_order_board = self._on_confirm_clear = self._on_reinstate = object()
        self._on_force_ship = object()
        self._on_system_mode = object()

    def _qos(self, key):
        return None

    def create_publisher(self, *args):
        return None

    def create_subscription(self, *args):
        return None

    def create_client(self, *args):
        return None

    def create_service(self, kind, name, callback):
        self.services.append((kind, name, callback))


def test_without_the_interface_only_the_existing_services_open(monkeypatch):
    import fleet_interfaces.srv as srv
    monkeypatch.delattr(srv, "ForceShip", raising=False)
    fake = _Interfaces({"force_ship": object()})
    node.FleetManagerNode._create_interfaces(fake, [])
    assert [name for _, name, _ in fake.services] == ["/fleet/confirm_clear", "/fleet/reinstate"]


def test_with_the_interface_force_ship_opens_on_its_callback(monkeypatch):
    import fleet_interfaces.srv as srv
    built = object()
    monkeypatch.setattr(srv, "ForceShip", built, raising=False)
    fake = _Interfaces({"force_ship": object()})
    node.FleetManagerNode._create_interfaces(fake, [])
    assert fake.services[-1] == (built, "/fleet/force_ship", fake._on_force_ship)


def test_the_force_ship_callback_asks_the_core_and_fills_the_response():
    asked = []
    core = SimpleNamespace(force_ship=lambda order_id: asked.append(order_id)
                           or (False, "NOT_IMPLEMENTED"))
    response = node.FleetManagerNode._on_force_ship(
        SimpleNamespace(core=core), SimpleNamespace(order_id="O-007"),
        SimpleNamespace(accepted=None, reason=None))
    assert asked == ["O-007"]
    assert (response.accepted, response.reason) == (False, "NOT_IMPLEMENTED")
