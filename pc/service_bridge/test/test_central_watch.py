"""central 상태 지켜보기 core 시험: 신선함 · 재시작 알림 · 실패 기록 풀기 (재시작 설계 6.4)."""
import pytest
from service_bridge.core.central_watch import CentralWatch, record_central_restart
from service_bridge.core.dispatch import record_ack, record_failure
from service_bridge.core.intake import place_order
from service_bridge.core.types import OrderLine, OrderRequest

A, B = "a" * 32, "b" * 32


# ---- 메모리 판단 (ROS · DB 없음) ----
def test_first_boot_id_only_remembered():
    w = CentralWatch()
    assert w.on_state(A, "CONNECTING", 10.0) is None
    # 같은 boot_id의 단계 바뀜은 재시작이 아님
    assert w.on_state(A, "RUNNING", 11.0) is None
    assert w.view(11.0) == "RUNNING"


def test_changed_boot_id_returns_old():
    w = CentralWatch()
    w.on_state(A, "RUNNING", 0.0)
    assert w.on_state(B, "CONNECTING", 1.0) == A
    assert w.view(1.0) == "CONNECTING"
    assert w.on_state(B, "SYNCING", 2.0) is None


def test_view_is_lost_before_any_and_after_3s():
    w = CentralWatch()
    assert w.view(0.0) == "LOST"
    w.on_state(A, "RUNNING", 100.0)
    assert w.view(102.9) == "RUNNING"
    assert w.view(103.1) == "LOST"
    w.on_state(A, "RUNNING", 104.0)  # 다시 오면 되돌아옴
    assert w.view(104.0) == "RUNNING"


def test_stale_state_blocks_dispatch_without_counting():
    w = CentralWatch()
    assert not w.can_dispatch(0.0)
    w.on_state(A, "SYNCING", 0.0)
    assert not w.can_dispatch(0.5)
    w.on_state(A, "RUNNING", 1.0)
    assert w.can_dispatch(3.9)
    assert not w.can_dispatch(4.1)


# ---- DB: 재시작 알림 · 실패 기록 풀기 ----
def _place(conn, seeded, kind_qty, key):
    items = tuple(OrderLine(seeded["products"][k], q) for k, q in kind_qty)
    res = place_order(conn, OrderRequest(seeded["accounts"]["tester"], items), key)
    assert not hasattr(res, "code"), res
    return res


def _row(conn, trip_id):
    return conn.execute(
        "SELECT attempts, next_attempt_at, unknown_since, last_result, central_acked_at "
        "FROM trip_dispatch WHERE trip_id = %s", (trip_id,)).fetchone()


def test_restart_records_info_alert_and_resets_unacked(conn, seeded):
    acked = _place(conn, seeded, [("mentos", 1)], "k0").trip_ids[0]
    record_failure(conn, acked, "TIMEOUT")
    record_ack(conn, acked, True, "")
    stalled = _place(conn, seeded, [("mentos", 1)], "k1").trip_ids[0]
    waiting = _place(conn, seeded, [("choco_bar", 1)], "k2").trip_ids[0]
    for _ in range(5):
        record_failure(conn, stalled, "TIMEOUT")
    record_failure(conn, waiting, "NO_SERVICE")
    assert _row(conn, stalled)[1] is not None
    before_acked = _row(conn, acked)

    assert record_central_restart(conn, A, B) == 2

    for t in (stalled, waiting):
        attempts, next_at, unknown, _last, acked_at = _row(conn, t)
        assert (attempts, next_at, unknown, acked_at) == (0, None, None, None)
    assert _row(conn, stalled)[3] == "TIMEOUT"  # last_result는 기록으로 남김
    assert _row(conn, acked) == before_acked
    assert conn.execute(
        "SELECT status FROM alert WHERE code = 'DISPATCH_STALLED'").fetchall() == [("RESOLVED",)]
    (level, source, message, detail, status), = conn.execute(
        "SELECT level, source, message, detail, status FROM alert "
        "WHERE code = 'CENTRAL_RESTART'").fetchall()
    assert (level, source, status) == ("INFO", "service_bridge", "OPEN")
    assert message == "central이 다시 켜져 주문판을 다시 세웁니다"
    assert detail == {"old_boot_id": A, "new_boot_id": B}


def test_restart_with_nothing_pending(conn, seeded):
    assert record_central_restart(conn, A, B) == 0
    assert conn.execute(
        "SELECT count(*) FROM alert WHERE code = 'CENTRAL_RESTART'").fetchone()[0] == 1


@pytest.mark.parametrize("state", ["", "BOOTING"])
def test_unknown_state_is_lost(state):
    w = CentralWatch()
    w.on_state(A, state, 0.0)
    assert w.view(0.0) == "LOST"
