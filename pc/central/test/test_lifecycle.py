"""central 단계: 연결 설정 → 주문판 갱신 → 실행 중 전환과 목록 반영 (ROS 없음)."""
from central.board import Board
from central.lifecycle import CONNECTING, RUNNING, SYNCING, Lifecycle

T1, T2 = object(), object()


def _new(rand=lambda: 0.5):
    board = Board()
    return board, Lifecycle(board, "boot0", rand)


def test_starts_connecting_and_needs_both_conditions():
    _, life = _new()
    assert life.state == CONNECTING and life.boot_id == "boot0"
    assert life.ready_to_sync(True, False) is False
    assert life.ready_to_sync(False, True) is False
    assert life.state == CONNECTING
    assert life.ready_to_sync(True, True) is True
    assert life.state == SYNCING


def test_list_loads_in_order_and_runs():
    board, life = _new()
    life.ready_to_sync(True, True)
    assert life.on_list(True, [("t2", {"A": 1}, T1, "RECEIVED"),
                               ("t1", {"B": 2}, T2, "RECEIVED")]) is True
    assert [(e.trip_id, e.items, e.created_at) for e in board.entries()] == [
        ("t2", {"A": 1}, T1), ("t1", {"B": 2}, T2)]
    assert life.state == RUNNING


def test_held_status_is_marked():
    board, life = _new()
    life.ready_to_sync(True, True)
    life.on_list(True, [("t1", {"A": 1}, T1, "HELD"), ("t2", {"A": 1}, T2, "RECEIVED")])
    assert board.held() == {"t1"}


def test_empty_list_runs_with_empty_board():
    board, life = _new()
    life.ready_to_sync(True, True)
    assert life.on_list(True, []) is True
    assert life.state == RUNNING and board.entries() == []


def test_rejected_list_keeps_syncing_and_backs_off():
    board, life = _new(rand=lambda: 0.5)
    life.ready_to_sync(True, True)
    assert life.on_list(False, []) is False
    assert life.state == SYNCING and board.entries() == []
    assert [life.next_retry_sec() for _ in range(6)] == [1, 2, 4, 8, 10, 10]


def test_retry_jitter_stays_within_20_percent():
    _, low = _new(rand=lambda: 0.0)
    _, high = _new(rand=lambda: 1.0)
    assert abs(low.next_retry_sec() - 0.8) < 1e-9
    assert abs(high.next_retry_sec() - 1.2) < 1e-9


def test_resubmit_of_listed_trip_is_accepted_once():
    board, life = _new()
    life.ready_to_sync(True, True)
    life.on_list(True, [("t1", {"A": 1}, T1, "RECEIVED")])
    assert board.submit("t1", {"A": 1}, T2) == (True, "")
    assert len(board.entries()) == 1 and board.entries()[0].created_at is T1


def test_load_skips_empty_items_and_known_trips():
    board = Board()
    board.submit("t1", {"A": 1}, T1)
    board.load([("t1", {"B": 5}, T2, False), ("t2", {}, T2, False), ("t3", {"C": 1}, T2, True)])
    assert [e.trip_id for e in board.entries()] == ["t1", "t3"]
    assert board.entries()[0].items == {"A": 1}
    assert board.held() == {"t3"}


def test_not_running_does_not_accept_work():
    _, life = _new()
    assert life.accepts_work() is False
    life.ready_to_sync(True, True)
    assert life.accepts_work() is False
    life.on_list(True, [])
    assert life.accepts_work() is True
