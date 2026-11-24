"""이름, 타입, QoS의 기준은 계약 파일 하나다."""
from pathlib import Path

import pytest
from fleet_core.common.contract import ContractError, load_contract

CONTRACT = Path(__file__).parents[3] / "interfaces" / "fleet_interfaces" / "contract.yaml"


@pytest.fixture(scope="module")
def contract():
    return load_contract(CONTRACT)


def test_topic_name_is_built_from_key_and_placeholder(contract):
    assert contract.name("robot_state", pode="pode2") == "/pode2/state"
    assert contract.name("order_board") == "/central/orders"
    assert contract.name("swap_basket", arm="arm3") == "/arm3/swap_basket"


def test_qos_lookup_returns_the_profile_of_the_entry(contract):
    qos = contract.qos("robot_state")
    assert (qos.reliability, qos.durability, qos.depth) == ("best_effort", "volatile", 5)
    assert contract.qos("zones").durability == "transient_local"


def test_the_heartbeat_is_reliable_with_depth_one(contract):
    """빠짐 자체가 정지 조건이라 재전송이 필요하다. 최신 하나만 다시 보낸다.

    상태는 best_effort 그대로다. 주기적으로 계속 와서 하나를 잃어도 다음 값이 대신한다.
    """
    qos = contract.qos("heartbeat")
    assert (qos.reliability, qos.durability, qos.depth) == ("reliable", "volatile", 1)
    assert contract.qos("robot_state").reliability == "best_effort"


def test_unknown_key_is_an_error_not_a_guess(contract):
    with pytest.raises(ContractError):
        contract.name("no_such_key")


def test_missing_placeholder_is_an_error(contract):
    with pytest.raises(ContractError):
        contract.name("robot_state")


def test_expand_lists_every_instance_with_type_and_kind(contract):
    entries = {e.name: e for e in contract.expanded()}
    assert entries["/pode3/move_to"].kind == "action"
    assert entries["/pode3/move_to"].type == "fleet_interfaces/action/MoveTo"
    assert entries["/pode3/state"].qos.reliability == "best_effort"
    assert entries["/workcell/fill_basket"].type == "fleet_interfaces/action/FillBasket"
    assert "/arm3/swap_basket" in entries
    assert "/arm1/swap_basket" not in entries


def test_expand_skips_real_only_entries_when_asked(contract):
    with_real = {e.name for e in contract.expanded(include_real_only=True)}
    without = {e.name for e in contract.expanded(include_real_only=False)}
    assert "/pode1/navigate_to_pose" in with_real
    assert "/pode1/navigate_to_pose" not in without


def test_entry_with_undefined_qos_profile_is_rejected(tmp_path):
    bad = tmp_path / "contract.yaml"
    bad.write_text(
        'placeholders: {}\nqos_profiles: {}\n'
        'topics:\n  - {key: a, name: /a, type: x/msg/A, qos: NOPE}\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError):
        load_contract(bad)


# ---- 계약 0.6: ReportOrder 보류 사유와 CANCELED ----
REPORT_ORDER_0_6 = """string STATUS_SHIPPED="SHIPPED"
string STATUS_PARTIAL="PARTIAL"
string STATUS_HELD="HELD"
string STATUS_CANCELED="CANCELED"
string HOLD_ROBOT_FAULT="ROBOT_FAULT"
string HOLD_ARM_FAULT="ARM_FAULT"
string order_id
string robot_id
string status
string hold_reason
OrderItem[] placed
OrderItem[] missing
string detail
---
bool accepted
"""


def test_report_order_matches_the_contract_0_6_text():
    srv = CONTRACT.parent / "srv" / "ReportOrder.srv"
    assert srv.read_text(encoding="utf-8") == REPORT_ORDER_0_6


def test_the_contract_is_version_0_7():
    import yaml
    assert yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["version"] == 0.7


# ---- 계약 0.7: 주문 흐름 1차 (SubmitTrip · Hello · ReportTrip · SystemMode) ----
def test_order_flow_names_are_in_the_contract():
    contract = load_contract(CONTRACT)
    assert contract.name("submit_trip") == "/central/submit_trip"
    assert contract.name("hello") == "/bridge/hello"
    assert contract.name("report_trip") == "/bridge/report_trip"
    assert contract.name("system_mode") == "/system/mode"
    assert contract.qos("system_mode").durability == "transient_local"
