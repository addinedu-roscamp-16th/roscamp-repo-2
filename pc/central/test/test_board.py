"""간이 주문판: 받은 순서, 응답 기억, 끝난 건 빼기."""
import pytest

from central.board import Board

NOW = object()


def test_submit_keeps_arrival_order_and_first_answer():
    b = Board()
    assert b.submit("t1", {"A": 1}, NOW) == (True, "")
    assert b.submit("t2", {"B": 2}, NOW) == (True, "")
    assert [e.trip_id for e in b.entries()] == ["t1", "t2"]
    assert b.entries()[1].items == {"B": 2}
    assert b.entries()[0].created_at is NOW


def test_same_trip_id_other_items_keeps_first():
    b = Board()
    b.submit("t1", {"A": 1}, NOW)
    assert b.submit("t1", {"B": 9}, NOW) == (True, "")
    assert [(e.trip_id, e.items) for e in b.entries()] == [("t1", {"A": 1})]


def test_invalid_request_is_remembered():
    b = Board()
    assert b.submit("", {"A": 1}, NOW) == (False, "INVALID_REQUEST")
    assert b.submit("t1", {}, NOW) == (False, "INVALID_REQUEST")
    assert b.submit("t2", {"A": 0}, NOW) == (False, "INVALID_REQUEST")
    assert b.submit("t1", {"A": 1}, NOW) == (False, "INVALID_REQUEST")  # 기억한 응답
    assert b.entries() == []


@pytest.mark.parametrize("st", ["SHIPPED", "PARTIAL", "CANCELED", "HELD"])
def test_final_report_removes_once(st):
    b = Board()
    b.submit("t1", {"A": 1}, NOW)
    assert b.report("t1", st) is True
    assert b.entries() == []
    assert b.report("t1", st) is True


def test_report_unknown_accepted_non_final_rejected():
    b = Board()
    b.submit("t1", {"A": 1}, NOW)
    assert b.report("zz", "SHIPPED") is True
    assert b.report("t1", "FILLING") is False
    assert [e.trip_id for e in b.entries()] == ["t1"]
