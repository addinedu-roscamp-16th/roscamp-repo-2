"""contract 1.1: central 운영 단계 토픽과 주문판 복구용 목록 서비스 이름이 contract에 있다."""
import pytest

pytest.importorskip("fleet_interfaces")

from ament_index_python.packages import get_package_share_directory  # noqa: E402
from fleet_core.common.contract import load_contract  # noqa: E402


def test_contract_has_central_state_and_board_trips():
    c = load_contract(get_package_share_directory("fleet_interfaces") + "/contract.yaml")
    assert c.name("central_state") == "/central/state"
    assert c.name("list_board_trips") == "/bridge/list_board_trips"
    from fleet_interfaces.msg import CentralState
    assert CentralState.STATE_RUNNING == "RUNNING"
