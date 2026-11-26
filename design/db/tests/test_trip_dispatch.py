"""trip_dispatch 제약 · DISPATCH_STALLED 알림 부분 UNIQUE (설계 8.1, 6.5)."""
import psycopg
import pytest

from helpers import make_account, make_order, make_product


def _trip(conn):
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    _, (tid,) = make_order(conn, acc, [{pid: 1}])
    return tid


def test_attempts_must_be_non_negative(conn):
    tid = _trip(conn)
    conn.execute("insert into trip_dispatch (trip_id) values (%s)", (tid,))
    assert conn.execute("select attempts from trip_dispatch").fetchone()[0] == 0
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("update trip_dispatch set attempts = -1 where trip_id = %s", (tid,))


def test_dispatch_row_needs_trip(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("insert into trip_dispatch (trip_id) values ('no-such-trip')")


def test_one_open_dispatch_stalled_per_trip(conn):
    tid = _trip(conn)
    sql = ("insert into alert (level, code, source, message, trip_id) "
           "values ('WARN', 'DISPATCH_STALLED', 'web', 'm', %s) returning alert_id")
    first = conn.execute(sql, (tid,)).fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(sql, (tid,))
    conn.execute("update alert set status = 'RESOLVED' where alert_id = %s", (first,))
    conn.execute(sql, (tid,))


def test_pending_dispatch_index_is_partial(conn):
    tid = _trip(conn)
    conn.execute("insert into trip_dispatch (trip_id, central_acked_at) values (%s, now())", (tid,))
    idx = conn.execute("select indexdef from pg_indexes where indexname = 'ux_trip_dispatch_pending'").fetchone()[0]
    assert "central_acked_at IS NULL" in idx
