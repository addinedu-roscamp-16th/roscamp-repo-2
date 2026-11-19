"""Task 2: CHECK · 부분 UNIQUE · 생성 칼럼 · updated_at (설계 문서 4절)."""
import psycopg
import pytest
from psycopg import errors

from helpers import ledger, make_account, make_order, make_product

BAD = (errors.CheckViolation, errors.NotNullViolation)


def _base(conn):
    """여러 시험이 쓰는 행: 계정 · 상품 · 주문 · 운반 건 · 마감."""
    acc = make_account(conn)
    pid = make_product(conn, "cider")
    order_id, (trip_id,) = make_order(conn, acc, [{pid: 2}])
    return acc, pid, order_id, trip_id


ROW_SQL = {
    "account": ("insert into account (login_id, password_hash, role) values ('z', 'x', %(v)s)", "role"),
    "orders": ("insert into orders (order_id, account_id, status) values ('O-z', %(acc)s, %(v)s)", "status"),
    "trip": ("insert into trip (trip_id, order_id, seq, status) values ('T-z', %(order)s, 9, %(v)s)", "status"),
    "status_history.reason": (
        "insert into status_history (order_id, to_status, reason, actor) values (%(order)s, 'HELD', %(v)s, 'SYSTEM')",
        None),
    "status_history.actor": (
        "insert into status_history (order_id, to_status, actor) values (%(order)s, 'HELD', %(v)s)", None),
    "alert.level": ("insert into alert (level, code, source, message) values (%(v)s, 'X', 'web', 'm')", None),
    "alert.status": (
        "insert into alert (level, code, source, message, status) values ('INFO', 'X', 'web', 'm', %(v)s)", None),
    "admin_action.action": (
        "insert into admin_action (account_id, action, result) values (%(acc)s, %(v)s, 'LOCAL')", None),
    "admin_action.result": (
        "insert into admin_action (account_id, action, result) values (%(acc)s, 'ESTOP', %(v)s)", None),
    "stock_close.close_kind": (
        "insert into stock_close (started_by, close_kind) values (%(acc)s, %(v)s)", None),
    "stock_count.reason": (
        "insert into stock_count (product_id, account_id, counted_qty, book_qty, reason) "
        "values (%(pid)s, %(acc)s, 1, 2, %(v)s)", None),
    "app_setting.key": ("insert into app_setting (key, value) values (%(v)s, 3)", None),
}

GOOD = {
    "account": "DEV", "orders": "FILLING_PAUSE", "trip": "HELD", "status_history.reason": "RETURNED_TO_SHELF",
    "status_history.actor": "ADMIN", "alert.level": "WARN", "alert.status": "RESOLVED",
    "admin_action.action": "CHARGE_END", "admin_action.result": "REJECTED",
    "stock_close.close_kind": "TEST_ENTRY", "stock_count.reason": "LOST", "app_setting.key": "max_order_items",
}
UNKNOWN = {
    "account": "ROOT", "orders": "REJECTED", "trip": "DONE", "status_history.reason": "OTHER",
    "status_history.actor": "ROBOT", "alert.level": "FATAL", "alert.status": "ACK",
    "admin_action.action": "DELETE_ALL", "admin_action.result": "OK",
    "stock_close.close_kind": "AUTO", "stock_count.reason": "MISC", "app_setting.key": "max_baskets",
}


@pytest.mark.parametrize("name", sorted(ROW_SQL))
def test_value_lists_reject_unknown(conn, name):
    acc, pid, order_id, _ = _base(conn)
    sql = ROW_SQL[name][0]
    with pytest.raises(errors.CheckViolation):
        conn.execute(sql, {"v": UNKNOWN[name], "acc": acc, "order": order_id, "pid": pid})


@pytest.mark.parametrize("name", sorted(ROW_SQL))
def test_value_lists_accept_known(conn, name):
    acc, pid, order_id, _ = _base(conn)
    conn.execute(ROW_SQL[name][0], {"v": GOOD[name], "acc": acc, "order": order_id, "pid": pid})


def _raw_ledger(conn, **cols):
    """트리거 없이도 CHECK만 보게 qty_after를 직접 채움 (Task 4 뒤에는 트리거가 덮어씀)."""
    cols.setdefault("qty_after", 0)
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    conn.execute(f"insert into stock_ledger ({names}) values ({marks})", list(cols.values()))


def test_ledger_inbound_needs_plus_and_inbound_reason(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="INBOUND", change_qty=-1, reason="REGULAR")
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="INBOUND", change_qty=1, reason="DAMAGED")


def test_ledger_outbound_needs_minus(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="OUTBOUND", change_qty=1, reason="DAMAGED")
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="OUTBOUND", change_qty=-1, reason="ADDITIONAL")


def test_ledger_reason_only_for_in_out(conn):
    _, pid, _, trip_id = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="SHIP", change_qty=-1, trip_id=trip_id, reason="OTHER", note="n")


def test_ledger_ship_needs_trip(conn):
    _, pid, _, trip_id = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="SHIP", change_qty=-1)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="INBOUND", change_qty=1, reason="REGULAR", trip_id=trip_id)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="SHIP", change_qty=1, trip_id=trip_id)


def test_ledger_adjust_needs_count(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="SPOT_ADJUST", change_qty=-1)


def test_ledger_input_error_needs_corrects(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="OUTBOUND", change_qty=-1, reason="INPUT_ERROR")
    _raw_ledger(conn, product_id=pid, entry_type="INBOUND", change_qty=5, reason="REGULAR")
    first = conn.execute("select min(ledger_id) from stock_ledger").fetchone()[0]
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="OUTBOUND", change_qty=-1, reason="DAMAGED",
                    corrects_ledger_id=first)


def test_ledger_other_needs_note(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="INBOUND", change_qty=1, reason="OTHER")


def test_ledger_change_qty_nonzero(conn):
    _, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        _raw_ledger(conn, product_id=pid, entry_type="SPOT_ADJUST", change_qty=0, count_id=None)


def test_trip_item_placed_plus_missing_le_ordered(conn):
    acc, pid, _, _ = _base(conn)
    _, (tid,) = make_order(conn, acc, [{pid: 4}])
    with pytest.raises(errors.CheckViolation):
        conn.execute("update trip_item set qty_placed = 3, qty_missing = 2 where trip_id = %s", (tid,))
    conn.execute("update trip_item set qty_placed = 3, qty_missing = 1 where trip_id = %s", (tid,))


def test_stock_close_finished_iff_not_in_progress(conn):
    acc, *_ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute("insert into stock_close (started_by, finished_at) values (%s, now())", (acc,))


def test_ops_resume_has_no_starter_and_cannot_cancel(conn):
    acc, *_ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute("insert into stock_close (started_by, close_kind) values (%s, 'OPS_RESUME')", (acc,))
    with pytest.raises(errors.CheckViolation):
        conn.execute("insert into stock_close (started_by, close_kind) values (null, 'MANUAL')")
    cid = conn.execute("insert into stock_close (close_kind) values ('OPS_RESUME') returning close_id").fetchone()[0]
    with pytest.raises(errors.CheckViolation):
        conn.execute("update stock_close set status = 'CANCELLED', finished_at = now() where close_id = %s", (cid,))


def test_only_one_open_close(conn):
    acc, *_ = _base(conn)
    conn.execute("insert into stock_close (started_by) values (%s)", (acc,))
    with pytest.raises(errors.UniqueViolation):
        conn.execute("insert into stock_close (started_by) values (%s)", (acc,))


def test_one_open_low_stock_alert_per_product(conn):
    _, pid, _, _ = _base(conn)
    sql = "insert into alert (level, code, source, message, product_id) values ('WARN', %s, 'web', 'm', %s) returning alert_id"
    aid = conn.execute(sql, ("LOW_STOCK", pid)).fetchone()[0]
    with pytest.raises(errors.UniqueViolation):
        conn.execute(sql, ("LOW_STOCK", pid))
    conn.execute(sql, ("OUT_OF_STOCK", pid))
    conn.execute(sql, ("OUT_OF_STOCK", pid))
    conn.execute("update alert set status = 'RESOLVED', resolved_at = now() where alert_id = %s", (aid,))
    conn.execute(sql, ("LOW_STOCK", pid))


def test_diff_qty_generated(conn):
    acc, pid, _, _ = _base(conn)
    diff = conn.execute(
        "insert into stock_count (product_id, account_id, counted_qty, book_qty, in_transit_qty) "
        "values (%s, %s, 7, 10, 2) returning diff_qty", (pid, acc)).fetchone()[0]
    assert diff == -1


def test_counted_qty_nullable_while_counting(conn):
    acc, pid, _, _ = _base(conn)
    conn.execute("insert into stock_count (product_id, account_id) values (%s, %s)", (pid, acc))


def test_app_setting_value_positive(conn):
    with pytest.raises(errors.CheckViolation):
        conn.execute("insert into app_setting (key, value) values ('basket_capacity', 0)")


@pytest.mark.parametrize("sql", [
    "update product set low_stock_threshold = -1",
    "update trip_item set qty_ordered = 0",
    "update trip set seq = 0",
    "update order_item set qty_ordered = 0",
    "update trip_item set qty_placed = -1",
    "insert into stock_count (product_id, account_id, counted_qty) values (%(pid)s, %(acc)s, -1)",
    "insert into stock_count (product_id, account_id, book_qty) values (%(pid)s, %(acc)s, -1)",
    "insert into stock_count (product_id, account_id, in_transit_qty) values (%(pid)s, %(acc)s, -1)",
])
def test_nonnegative_columns(conn, sql):
    acc, pid, _, _ = _base(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute(sql, {"pid": pid, "acc": acc})


def test_stock_qty_and_qty_after_nonnegative(conn):
    _, pid, _, _ = _base(conn)
    names = {r[0] for r in conn.execute(
        "select conname from pg_constraint where conname in ('ck_product_stock_qty', 'ck_stock_ledger_qty_after')")}
    assert names == {"ck_product_stock_qty", "ck_stock_ledger_qty_after"}


def test_updated_at_moves_on_update(conn):
    _, _, order_id, _ = _base(conn)
    before = conn.execute("select updated_at from orders where order_id = %s", (order_id,)).fetchone()[0]
    conn.execute("select pg_sleep(0.01)")
    conn.execute("update orders set status = 'ASSIGNED' where order_id = %s", (order_id,))
    after = conn.execute("select updated_at from orders where order_id = %s", (order_id,)).fetchone()[0]
    assert after > before
