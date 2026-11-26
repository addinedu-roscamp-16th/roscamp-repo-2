"""apply_hello (F-06, 설계 6.3) 시험: 실제 PostgreSQL 시험 DB."""
import pytest
from service_bridge.core.hello import apply_hello
from service_bridge.core.reports import apply_report
from service_bridge.core.types import HelloIn, HelloOut, ReportIn, ReportOut
from test_reports import D, count, order_status, trip_of, trip_state


def rep(trip, epoch, n, status):
    return ReportIn(trip_id=trip, epoch=epoch, n=n, status=status, reason="", robot_id="pode1",
                    placed={}, missing={})


def test_epoch_starts_at_1_and_same_boot_id_keeps_it(pool):
    assert apply_hello(pool, HelloIn("fleet", "b1"), D()) == HelloOut(True, 1)
    assert apply_hello(pool, HelloIn("fleet", "b1"), D()) == HelloOut(True, 1)


def test_restart_holds_active_and_filters_old_epoch(pool, conn, seeded):
    assert apply_hello(pool, HelloIn("fleet", "b1"), D()) == HelloOut(True, 1)
    t1, t2, t3 = (trip_of(seeded, conn, {"mentos": 1}) for _ in range(3))
    for n, st in enumerate(["ASSIGNED", "FILLING", "TRANSPORTING"], 1):
        assert apply_report(pool, rep(t1, 1, n, st), D()).result == "APPLIED"
    assert apply_report(pool, rep(t3, 1, 1, "SHIPPED"), D()).result == "APPLIED"
    assert trip_state(conn, t1)[:3] == ("TRANSPORTING", 1, 3)
    before = {t: trip_state(conn, t) for t in (t2, t3)}

    assert apply_hello(pool, HelloIn("fleet", "b2"), D()) == HelloOut(True, 2)

    assert trip_state(conn, t1)[:3] == ("HELD", 2, 0)
    assert {t: trip_state(conn, t) for t in (t2, t3)} == before  # t2 RECEIVED, t3 SHIPPED 그대로
    assert conn.execute(
        "select from_status, to_status, reason, actor, robot_id from status_history "
        "where trip_id = %s order by history_id desc limit 1", (t1,)
    ).fetchone() == ("TRANSPORTING", "HELD", "FLEET_RESTART", "SYSTEM", "pode1")
    assert order_status(conn, t1) == "HELD"
    assert conn.execute(
        "select to_status from status_history where order_id = (select order_id from trip "
        "where trip_id = %s) and trip_id is null order by history_id desc limit 1", (t1,)
    ).fetchone() == ("HELD",)
    assert order_status(conn, t2) == "RECEIVED" and order_status(conn, t3) == "SHIPPED"

    assert apply_report(pool, rep(t1, 1, 9, "SHIPPED"), D()) == ReportOut(True, "DUPLICATE")
    assert apply_report(pool, rep(t1, 2, 1, "ASSIGNED"), D()) == ReportOut(True, "APPLIED")
    assert trip_state(conn, t1)[:3] == ("ASSIGNED", 2, 1)


def test_restart_without_active_trips_only_bumps_epoch(pool, conn, seeded):
    apply_hello(pool, HelloIn("fleet", "b1"), D())
    t = trip_of(seeded, conn, {"mentos": 1})
    hist = count(conn, "status_history")
    assert apply_hello(pool, HelloIn("fleet", "b2"), D()) == HelloOut(True, 2)
    assert trip_state(conn, t)[:3] == ("RECEIVED", None, None)
    assert count(conn, "status_history") == hist


@pytest.mark.parametrize("hello, reason", [(HelloIn("central", "b1"), "UNKNOWN_NODE"),
                                           (HelloIn("fleet", ""), "INVALID_REQUEST")])
def test_bad_hello_rejected(pool, conn, hello, reason):  # Review Focus 4
    assert apply_hello(pool, hello, D()) == HelloOut(False, reason=reason)
    assert count(conn, "bridge_session") == 0


def test_order_that_turns_active_between_queries_is_locked_and_held(pool, conn, seeded,
                                                                    monkeypatch):
    """후보 주문을 잠그는 사이 다른 트랜잭션이 다른 주문의 건을 ASSIGNED로 커밋해도 보류된다."""
    from service_bridge.core import hello as hello_mod

    apply_hello(pool, HelloIn("fleet", "b1"), D())
    t1, t2 = (trip_of(seeded, conn, {"mentos": 1}) for _ in range(2))
    assert apply_report(pool, rep(t1, 1, 1, "ASSIGNED"), D()).result == "APPLIED"
    real, fired = hello_mod._lock_orders, []

    def racing(c, ids):
        out = real(c, ids)
        if not fired:  # 첫 잠금 직후, 다른 트랜잭션이 t2를 진행 중으로 커밋
            fired.append(1)
            conn.execute("update trip set status = 'ASSIGNED' where trip_id = %s", (t2,))
        return out

    monkeypatch.setattr(hello_mod, "_lock_orders", racing)
    assert apply_hello(pool, HelloIn("fleet", "b2"), D()) == HelloOut(True, 2)
    assert fired
    for t in (t1, t2):
        assert trip_state(conn, t)[:3] == ("HELD", 2, 0)
        assert order_status(conn, t) == "HELD"
