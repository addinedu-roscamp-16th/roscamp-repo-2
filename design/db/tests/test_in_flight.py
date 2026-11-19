"""Task 3: v_in_flight (설계 문서 4.12)."""
from helpers import make_account, make_order, make_product, set_trip_status


def _in_flight(conn):
    return {r[0] for r in conn.execute("select trip_id from v_in_flight")}


def _setup(conn):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    return acc, pid


def test_active_statuses_are_in_flight(conn):
    acc, pid = _setup(conn)
    want = set()
    for status in ["RECEIVED", "ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING", "HELD"]:
        _, (tid,) = make_order(conn, acc, [{pid: 1}])
        set_trip_status(conn, tid, status)
        if status != "RECEIVED":
            want.add(tid)
    assert _in_flight(conn) == want


def test_finished_not_in_flight(conn):
    acc, pid = _setup(conn)
    for status in ["SHIPPED", "PARTIAL", "CANCELLED"]:
        _, (tid,) = make_order(conn, acc, [{pid: 1}])
        set_trip_status(conn, tid, status)
    assert _in_flight(conn) == set()


def test_remaining_trip_of_started_split_order(conn):
    acc, pid = _setup(conn)
    _, (t1, t2) = make_order(conn, acc, [{pid: 4}, {pid: 1}])
    set_trip_status(conn, t1, "SHIPPED")
    assert _in_flight(conn) == {t2}


def test_unstarted_split_order_not_in_flight(conn):
    acc, pid = _setup(conn)
    make_order(conn, acc, [{pid: 4}, {pid: 1}])
    assert _in_flight(conn) == set()


def test_cancelled_sibling_does_not_start_order(conn):
    acc, pid = _setup(conn)
    _, (t1, _t2) = make_order(conn, acc, [{pid: 4}, {pid: 1}])
    set_trip_status(conn, t1, "CANCELLED")
    assert _in_flight(conn) == set()
