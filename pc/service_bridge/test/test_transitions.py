"""허용 전이 표(SB-03)와 주문 요약 규칙(주문 DB 설계 4.4) 시험."""
import re
from pathlib import Path

import pytest
from service_bridge.core.transitions import TRIP_REASONS, is_allowed, order_summary

REPO = Path(__file__).resolve().parents[3]

ALLOWED = {
    "RECEIVED": {"ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL",
                 "HELD", "CANCELLED"},
    "ASSIGNED": {"FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD",
                 "CANCELLED", "RECEIVED"},
    "FILLING": {"FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD", "CANCELLED",
                "RECEIVED"},
    "FILLING_PAUSE": {"FILLING", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD", "CANCELLED",
                      "RECEIVED"},
    "TRANSPORTING": {"SHIPPED", "PARTIAL", "HELD"},
    "HELD": {"SHIPPED", "PARTIAL", "RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING"},
    "SHIPPED": set(), "PARTIAL": set(), "CANCELLED": set(),
}


@pytest.mark.parametrize("cur", sorted(ALLOWED))
@pytest.mark.parametrize("new", sorted(ALLOWED))
def test_transition_table(cur, new):
    assert is_allowed(cur, new) == (cur == new or new in ALLOWED[cur])


@pytest.mark.parametrize("trips, expected", [
    (["SHIPPED", "CANCELLED"], "PARTIAL"),          # 규칙 1
    (["RECEIVED", "RECEIVED"], "RECEIVED"),         # 규칙 2
    (["HELD", "TRANSPORTING"], "HELD"),             # 규칙 3
    (["SHIPPED", "RECEIVED"], "ASSIGNED"),          # 규칙 4: 시작한 주문의 남은 접수됨은 배정됨
    (["TRANSPORTING", "FILLING"], "FILLING"),       # 규칙 4: 가장 늦은 단계
    (["TRANSPORTING"], "TRANSPORTING"),             # 운반 건 하나는 그대로
])
def test_order_summary(trips, expected):
    assert order_summary(trips) == expected


def test_trip_reasons_match_db_check():
    sql = (REPO / "design/db/order_db_extra.sql").read_text(encoding="utf-8")
    block = sql.split("ck_status_history_reason")[1].split("ck_status_history_actor")[0]
    assert TRIP_REASONS == set(re.findall(r"'([A-Z_]+)'", block))
