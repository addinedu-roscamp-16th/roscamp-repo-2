"""끝나지 않은 운반 건 목록 core 시험 (시험 DB, 설계 6.3)."""
import time

import pytest

from service_bridge.core.board_trips import list_board_trips
from service_bridge.core.db import make_pool
from service_bridge.core.dispatch import record_ack
from service_bridge.tools.trips import insert_trip


def _deadline():
    return time.monotonic() + 2.5


def _trip(conn, seeded, items, *, dispatch=True):
    """운반 건 하나 + (기본) trip_dispatch 줄. 줄이 없으면 접수 API 밖에서 넣은 건."""
    _, trip_id = insert_trip(conn, seeded["account_id"], items)
    if dispatch:
        conn.execute("INSERT INTO trip_dispatch (trip_id) VALUES (%s)", (trip_id,))
    return trip_id


def test_lists_open_and_unacked_trips_in_created_order(pool, conn, seeded):
    a = _trip(conn, seeded, {"mentos": 2, "choco_bar": 1})
    b = _trip(conn, seeded, {"mygummy": 1})
    record_ack(conn, a, True, "")                      # a: 넘김, b: 아직 안 넘김
    created = dict(conn.execute("SELECT trip_id, created_at FROM trip").fetchall())

    out = list_board_trips(pool, _deadline())

    assert out.accepted is True and out.reason == ""
    assert [t.trip_id for t in out.trips] == [a, b]
    first = out.trips[0]
    assert first.items == {"mentos": 2, "choco_bar": 1}
    assert (first.trip_index, first.trip_count) == (1, 1)
    assert first.submitted_at == created[a]
    assert first.status == "RECEIVED" and first.robot_id == ""
    assert first.placed == {} and first.missing == {}
    assert (first.report_epoch, first.report_n) == (0, 0)
    assert out.trips[1].submitted_at == created[b]


@pytest.mark.parametrize("status", ["SHIPPED", "PARTIAL", "CANCELLED"])
def test_final_trips_are_left_out(pool, conn, seeded, status):
    t = _trip(conn, seeded, {"mentos": 1})
    conn.execute("UPDATE trip SET status = %s WHERE trip_id = %s", (status, t))
    assert list_board_trips(pool, _deadline()).trips == []


def test_rejected_trip_is_left_out_but_accepted_stays(pool, conn, seeded):
    bad = _trip(conn, seeded, {"mentos": 1})
    ok = _trip(conn, seeded, {"mygummy": 1})
    record_ack(conn, bad, False, "INVALID_REQUEST")
    record_ack(conn, ok, True, "")
    assert [t.trip_id for t in list_board_trips(pool, _deadline()).trips] == [ok]


def test_held_trip_is_listed_with_status_held(pool, conn, seeded):
    t = _trip(conn, seeded, {"mentos": 1})
    order_id = conn.execute("SELECT order_id FROM trip WHERE trip_id = %s", (t,)).fetchone()[0]
    conn.execute("UPDATE trip SET status = 'HELD', robot_id = 'pode1', "
                 "report_epoch = 2, report_n = 5 WHERE trip_id = %s", (t,))
    conn.execute(
        "UPDATE trip_item SET qty_placed = 1 WHERE trip_id = %s", (t,))
    conn.execute(
        "INSERT INTO status_history (order_id, trip_id, from_status, to_status, reason, actor) "
        "VALUES (%s, %s, 'RECEIVED', 'HELD', 'FLEET_RESTART', 'SYSTEM')", (order_id, t))
    got = list_board_trips(pool, _deadline()).trips
    assert len(got) == 1
    assert got[0].status == "HELD" and got[0].reason == "FLEET_RESTART"
    assert got[0].robot_id == "pode1"
    assert got[0].placed == {"mentos": 1} and got[0].missing == {}
    assert (got[0].report_epoch, got[0].report_n) == (2, 5)


def test_trip_without_dispatch_row_is_left_out(pool, conn, seeded):
    _trip(conn, seeded, {"mentos": 1}, dispatch=False)
    assert list_board_trips(pool, _deadline()).trips == []


def test_db_error_gives_not_accepted(test_db):
    from conftest import _dsn
    p = make_pool(_dsn(test_db))
    p.close()
    out = list_board_trips(p, _deadline())
    assert out.accepted is False and out.reason == "DB_ERROR" and out.trips == []
