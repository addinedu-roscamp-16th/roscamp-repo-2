"""Task 4: 재고 원장 트리거 (설계 문서 5절 "재고 기록")."""
import psycopg
import pytest
from psycopg import errors

from helpers import (confirm_close, ledger, make_account, make_order, make_product, open_counting,
                     set_trip_status, start_close)


def stock(conn, pid):
    return conn.execute("select stock_qty from product where product_id = %s", (pid,)).fetchone()[0]


def raises_msg(msg):
    return pytest.raises(psycopg.Error, match=msg)


@pytest.fixture
def acc(conn):
    return make_account(conn)


def test_inbound_raises_stock_and_fills_qty_after(conn):
    pid = make_product(conn, "cider")
    lid = ledger(conn, pid, "INBOUND", 10, "REGULAR")
    assert stock(conn, pid) == 10
    assert conn.execute("select qty_after from stock_ledger where ledger_id = %s", (lid,)).fetchone()[0] == 10


def test_outbound_below_zero_rejected(conn):
    pid = make_product(conn, "cider", 3)
    with pytest.raises(errors.CheckViolation):
        ledger(conn, pid, "OUTBOUND", -5, "DAMAGED")
    assert stock(conn, pid) == 3


def test_ship_lowers_stock(conn, acc):
    pid = make_product(conn, "cider", 10)
    _, (tid,) = make_order(conn, acc, [{pid: 2}])
    ledger(conn, pid, "SHIP", -2, trip_id=tid)
    assert stock(conn, pid) == 8


def test_ledger_rows_immutable(conn):
    pid = make_product(conn, "cider", 10)
    with raises_msg("원장"):
        conn.execute("update stock_ledger set note = 'x'")
    with raises_msg("원장"):
        conn.execute("delete from stock_ledger")


def test_direct_stock_update_rejected(conn):
    pid = make_product(conn, "cider", 10)
    with raises_msg("재고는 원장으로만 바꿈"):
        conn.execute("update product set stock_qty = 99 where product_id = %s", (pid,))
    conn.execute("update product set name = 'Cider' where product_id = %s", (pid,))
    assert stock(conn, pid) == 10


def test_product_insert_must_start_at_zero(conn):
    with raises_msg("재고는 원장으로만 바꿈"):
        conn.execute("insert into product (kind, name, stock_qty) values ('cola', 'cola', 5)")


def test_close_blocks_outbound_and_spot_from_start(conn, acc):
    pid = make_product(conn, "cider", 10)
    start_close(conn, acc)
    with raises_msg("마감 중에는 반출할 수 없음"):
        ledger(conn, pid, "OUTBOUND", -1, "DAMAGED")
    cid = conn.execute("insert into stock_count (product_id, account_id, counted_qty, book_qty) "
                       "values (%s, %s, 9, 10) returning count_id", (pid, acc)).fetchone()[0]
    with raises_msg("마감 중에는 수시 조정할 수 없음"):
        ledger(conn, pid, "SPOT_ADJUST", -1, count_id=cid)


def test_close_allows_inbound_until_counting(conn, acc):
    pid = make_product(conn, "cider", 10)
    close_id = start_close(conn, acc)
    ledger(conn, pid, "INBOUND", 5, "ADDITIONAL")
    assert stock(conn, pid) == 15
    open_counting(conn, close_id)
    with raises_msg("실사를 연 뒤에는 입고할 수 없음"):
        ledger(conn, pid, "INBOUND", 5, "ADDITIONAL")


def test_close_allows_ship_and_close_adjust(conn, acc):
    pid = make_product(conn, "cider", 10)
    _, (tid,) = make_order(conn, acc, [{pid: 2}])
    close_id = start_close(conn, acc)
    ledger(conn, pid, "SHIP", -2, trip_id=tid)
    open_counting(conn, close_id)
    cid = conn.execute("insert into stock_count (close_id, product_id, account_id, counted_qty, book_qty, reason) "
                       "values (%s, %s, %s, 7, 8, 'LOST') returning count_id", (close_id, pid, acc)).fetchone()[0]
    ledger(conn, pid, "CLOSE_ADJUST", -1, count_id=cid)
    assert stock(conn, pid) == 7


def test_correction_reverses_original(conn):
    pid = make_product(conn, "cider")
    n = ledger(conn, pid, "INBOUND", 100, "REGULAR")
    ledger(conn, pid, "OUTBOUND", -100, "INPUT_ERROR", corrects_ledger_id=n)
    assert stock(conn, pid) == 0


def test_correction_twice_rejected(conn):
    pid = make_product(conn, "cider")
    n = ledger(conn, pid, "INBOUND", 100, "REGULAR")
    ledger(conn, pid, "INBOUND", 100, "REGULAR")
    ledger(conn, pid, "OUTBOUND", -100, "INPUT_ERROR", corrects_ledger_id=n)
    with pytest.raises(errors.UniqueViolation):
        ledger(conn, pid, "OUTBOUND", -100, "INPUT_ERROR", corrects_ledger_id=n)


def test_correction_must_match(conn, acc):
    pid = make_product(conn, "cider")
    other = make_product(conn, "cola", 100)
    n = ledger(conn, pid, "INBOUND", 100, "REGULAR")
    with raises_msg("바로잡을 기록과 맞지 않음"):
        ledger(conn, other, "OUTBOUND", -100, "INPUT_ERROR", corrects_ledger_id=n)
    with raises_msg("바로잡을 기록과 맞지 않음"):
        ledger(conn, pid, "OUTBOUND", -90, "INPUT_ERROR", corrects_ledger_id=n)
    with raises_msg("바로잡을 기록과 맞지 않음"):
        ledger(conn, pid, "INBOUND", 100, "INPUT_ERROR", corrects_ledger_id=n)
    _, (tid,) = make_order(conn, acc, [{pid: 1}])
    ship = ledger(conn, pid, "SHIP", -1, trip_id=tid)
    with raises_msg("바로잡을 기록과 맞지 않음"):
        ledger(conn, pid, "INBOUND", 1, "INPUT_ERROR", corrects_ledger_id=ship)


def test_correction_before_last_confirmed_close_rejected(conn, acc):
    pid = make_product(conn, "cider")
    n = ledger(conn, pid, "INBOUND", 100, "REGULAR")
    confirm_close(conn, start_close(conn, acc), acc)
    with raises_msg("마감 이전 기록은 고칠 수 없음"):
        ledger(conn, pid, "OUTBOUND", -100, "INPUT_ERROR", corrects_ledger_id=n)


def _spot_count(conn, pid, acc, counted, book):
    return conn.execute("insert into stock_count (product_id, account_id, counted_qty, book_qty) "
                        "values (%s, %s, %s, %s) returning count_id", (pid, acc, counted, book)).fetchone()[0]


def test_spot_adjust_blocked_when_in_flight_trip_has_unknown_placed(conn, acc):
    pid = make_product(conn, "cider", 10)
    _, (tid,) = make_order(conn, acc, [{pid: 2}])
    set_trip_status(conn, tid, "FILLING")
    cid = _spot_count(conn, pid, acc, 9, 10)
    with raises_msg("담은 수를 모르는 운반 건이 있어 수시 조정할 수 없음"):
        ledger(conn, pid, "SPOT_ADJUST", -1, count_id=cid)
    set_trip_status(conn, tid, "FILLING", placed={pid: 0})
    ledger(conn, pid, "SPOT_ADJUST", -1, count_id=cid)
    assert stock(conn, pid) == 9


def test_adjust_qty_must_equal_count_diff(conn, acc):
    pid = make_product(conn, "cider", 10)
    cid = _spot_count(conn, pid, acc, 9, 10)
    with raises_msg("실사 차이와 맞지 않음"):
        ledger(conn, pid, "SPOT_ADJUST", -2, count_id=cid)


def test_adjust_from_cancelled_close_rejected(conn, acc):
    pid = make_product(conn, "cider", 10)
    close_id = start_close(conn, acc)
    open_counting(conn, close_id)
    cid = conn.execute("insert into stock_count (close_id, product_id, account_id, counted_qty, book_qty, reason) "
                       "values (%s, %s, %s, 9, 10, 'LOST') returning count_id", (close_id, pid, acc)).fetchone()[0]
    conn.execute("update stock_close set status = 'CANCELLED', finished_at = now() where close_id = %s", (close_id,))
    with raises_msg("취소된 마감의 실사로 조정할 수 없음"):
        ledger(conn, pid, "CLOSE_ADJUST", -1, count_id=cid)


def test_close_start_waits_for_ledger_txn(conn, conn_factory):
    pid = make_product(conn, "cider")
    a = conn_factory(autocommit=False)
    b = conn_factory(autocommit=False)
    ledger(a, pid, "INBOUND", 1, "REGULAR")          # 공유 잠금을 쥔 채 커밋하지 않음
    b.execute("set lock_timeout = '500ms'")
    with pytest.raises(errors.LockNotAvailable):
        b.execute("select pg_advisory_xact_lock(7001)")
    b.rollback()
    a.commit()
    b.execute("select pg_advisory_xact_lock(7001)")
    b.rollback()


def test_adjust_once_per_count(conn, acc):
    pid = make_product(conn, "cider", 10)
    cid = _spot_count(conn, pid, acc, 9, 10)
    ledger(conn, pid, "SPOT_ADJUST", -1, count_id=cid)
    with pytest.raises(errors.UniqueViolation):
        ledger(conn, pid, "SPOT_ADJUST", -1, count_id=cid)
    assert stock(conn, pid) == 9
