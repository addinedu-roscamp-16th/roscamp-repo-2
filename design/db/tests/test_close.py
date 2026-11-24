"""Task 5: 마감 실사 · 확정 검사 · 대기 주문 재확인 (설계 문서 5절 "마감")."""
import datetime as dt

import psycopg
import pytest

from helpers import (confirm_close, ledger, make_account, make_order, make_product, open_counting,
                     set_trip_status, start_close)


def raises_msg(msg):
    return pytest.raises(psycopg.Error, match=msg)


@pytest.fixture
def acc(conn):
    return make_account(conn)


def _count(conn, close_id, pid, acc, counted, book=None, reason=None):
    conn.execute(
        "insert into stock_count (close_id, product_id, account_id, counted_qty, book_qty, reason) "
        "values (%s, %s, %s, %s, %s, %s)", (close_id, pid, acc, counted, book, reason))


def _confirm(conn, close_id, acc):
    conn.execute("update stock_close set status = 'CONFIRMED', finished_at = now(), finished_by = %s "
                 "where close_id = %s", (acc, close_id))


def test_insert_only_in_progress(conn, acc):
    with raises_msg("진행 중으로만 시작"):
        conn.execute("insert into stock_close (started_by, status, finished_at) values (%s, 'CONFIRMED', now())", (acc,))


def test_counting_needs_hold_ack(conn, acc):
    cid = start_close(conn, acc)
    with raises_msg("배정 보류 반영 전"):
        conn.execute("update stock_close set counting_started_at = now() where close_id = %s", (cid,))


def test_counting_needs_no_in_flight(conn, acc):
    pid = make_product(conn, "cider", 5)
    _, (tid,) = make_order(conn, acc, [{pid: 1}])
    set_trip_status(conn, tid, "FILLING")
    cid = start_close(conn, acc)
    conn.execute("update stock_close set hold_acked_at = now() where close_id = %s", (cid,))
    with raises_msg("진행 중 운반 건이 남음"):
        conn.execute("update stock_close set counting_started_at = now() where close_id = %s", (cid,))


def test_counting_waits_for_started_split_order(conn, acc):
    pid = make_product(conn, "cider", 9)
    _, (t1, _t2) = make_order(conn, acc, [{pid: 4}, {pid: 1}])
    set_trip_status(conn, t1, "SHIPPED")
    cid = start_close(conn, acc)
    conn.execute("update stock_close set hold_acked_at = now() where close_id = %s", (cid,))
    with raises_msg("진행 중 운반 건이 남음"):
        conn.execute("update stock_close set counting_started_at = now() where close_id = %s", (cid,))


def test_count_rows_only_after_counting_opens(conn, acc):
    pid = make_product(conn, "cider", 5)
    cid = start_close(conn, acc)
    with raises_msg("실사를 열지 않은 마감"):
        _count(conn, cid, pid, acc, 5)


def test_confirm_requires_all_products_counted(conn, acc):
    p1 = make_product(conn, "cider", 5)
    p2 = make_product(conn, "cola", 3)
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    _count(conn, cid, p1, acc, 5, 5)
    _count(conn, cid, p2, acc, None, 3)
    with raises_msg("실사하지 않은 상품이 있음"):
        _confirm(conn, cid, acc)


def test_confirm_requires_row_for_new_product(conn, acc):
    p1 = make_product(conn, "cider", 5)
    cid = start_close(conn, acc)
    make_product(conn, "cola")
    open_counting(conn, cid)
    _count(conn, cid, p1, acc, 5, 5)
    with raises_msg("실사하지 않은 상품이 있음"):
        _confirm(conn, cid, acc)


def test_confirm_requires_book_qty(conn, acc):
    p1 = make_product(conn, "cider", 5)
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    _count(conn, cid, p1, acc, 5)
    with raises_msg("장부 수량이 없는 행이 있음"):
        _confirm(conn, cid, acc)


def test_confirm_requires_reason_for_diff(conn, acc):
    p1 = make_product(conn, "cider", 10)
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    _count(conn, cid, p1, acc, 9, 10)
    with raises_msg("사유 없는 차이가 있음"):
        _confirm(conn, cid, acc)


def test_confirm_flow_adjusts_stock(conn, acc):
    p1 = make_product(conn, "cider", 10)
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    _count(conn, cid, p1, acc, 9, 10, "COUNT_DIFF")
    count_id = conn.execute("select count_id from stock_count where close_id = %s", (cid,)).fetchone()[0]
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(7001)")
        ledger(conn, p1, "CLOSE_ADJUST", -1, count_id=count_id)
        _confirm(conn, cid, acc)
    assert conn.execute("select stock_qty from product").fetchone()[0] == 9


def test_test_run_reason_only_for_ops_resume(conn, acc):
    p1 = make_product(conn, "cider", 10)
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    with raises_msg("시험 영향은 운영 재개 마감에서만"):
        _count(conn, cid, p1, acc, 9, 10, "TEST_RUN")
    conn.execute("update stock_close set status = 'CANCELLED', finished_at = now() where close_id = %s", (cid,))
    ops = start_close(conn, acc, "OPS_RESUME")
    open_counting(conn, ops)
    _count(conn, ops, p1, acc, 9, 10, "TEST_RUN")


def _rejects(conn):
    return [r[0] for r in conn.execute("select order_id from fn_recheck_rejects()")]


def _t(minutes):
    return dt.datetime(2026, 11, 19, 9, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)


def test_recheck_cumulative_skip(conn, acc):
    cider = make_product(conn, "cider", 5)
    make_order(conn, acc, [{cider: 3}], order_id="A", created_at=_t(1))
    make_order(conn, acc, [{cider: 4}], order_id="B", created_at=_t(2))
    make_order(conn, acc, [{cider: 2}], order_id="C", created_at=_t(3))
    assert _rejects(conn) == ["B"]


def test_recheck_whole_order_rejected(conn, acc):
    cider = make_product(conn, "cider", 5)
    cola = make_product(conn, "cola", 1)
    make_order(conn, acc, [{cider: 2, cola: 2}], order_id="A", created_at=_t(1))
    make_order(conn, acc, [{cider: 3}], order_id="B", created_at=_t(2))
    assert _rejects(conn) == ["A"]


def test_recheck_ignores_started_orders(conn, acc):
    cider = make_product(conn, "cider", 1)
    _, (t1, _t2) = make_order(conn, acc, [{cider: 4}, {cider: 1}], order_id="S", created_at=_t(1))
    set_trip_status(conn, t1, "SHIPPED")
    assert _rejects(conn) == []


# --- 최종 검토 I-1 · I-2 · I-3 ---

def _counting_close(conn, acc):
    cid = start_close(conn, acc)
    open_counting(conn, cid)
    return cid


def test_confirm_rejects_unapplied_diff(conn, acc):
    p1 = make_product(conn, "cider", 10)
    cid = _counting_close(conn, acc)
    _count(conn, cid, p1, acc, 7, 10, "LOST")
    with raises_msg("재고가 실사 수량과 맞지 않음"):
        _confirm(conn, cid, acc)


def test_confirm_rejects_stock_changed_after_count(conn, acc):
    p1 = make_product(conn, "cider", 10)
    _, (tid,) = make_order(conn, acc, [{p1: 2}])
    cid = _counting_close(conn, acc)
    _count(conn, cid, p1, acc, 9, 10, "LOST")
    ledger(conn, p1, "SHIP", -2, trip_id=tid)            # 늦게 온 출고 보고
    count_id = conn.execute("select count_id from stock_count where close_id = %s", (cid,)).fetchone()[0]
    with raises_msg("재고가 실사 수량과 맞지 않음"):
        with conn.transaction():
            conn.execute("select pg_advisory_xact_lock(7001)")
            ledger(conn, p1, "CLOSE_ADJUST", -1, count_id=count_id)
            _confirm(conn, cid, acc)


def test_close_adjust_requires_exclusive_lock(conn, acc):
    p1 = make_product(conn, "cider", 10)
    cid = _counting_close(conn, acc)
    _count(conn, cid, p1, acc, 9, 10, "LOST")
    count_id = conn.execute("select count_id from stock_count where close_id = %s", (cid,)).fetchone()[0]
    with raises_msg("마감 확정은 배타 잠금을 먼저 잡아야 함"):
        ledger(conn, p1, "CLOSE_ADJUST", -1, count_id=count_id)


def test_confirm_txn_blocks_concurrent_ship(conn, conn_factory, acc):
    p1 = make_product(conn, "cider", 10)
    _, (tid,) = make_order(conn, acc, [{p1: 2}])
    a = conn_factory(autocommit=False)
    b = conn_factory(autocommit=False)
    a.execute("select pg_advisory_xact_lock(7001)")       # 확정 트랜잭션 시작
    b.execute("set lock_timeout = '500ms'")
    with pytest.raises(psycopg.errors.LockNotAvailable):
        ledger(b, p1, "SHIP", -2, trip_id=tid)
    b.rollback()
    a.rollback()


def test_finished_close_frozen(conn, acc):
    other = make_account(conn)
    make_product(conn, "cider", 5)
    cid = start_close(conn, acc)
    confirm_close(conn, cid, acc)
    with raises_msg("끝난 마감은 바꿀 수 없음"):
        conn.execute("update stock_close set finished_by = %s, finished_at = now() where close_id = %s", (other, cid))
    with raises_msg("끝난 마감은 바꿀 수 없음"):
        conn.execute("update stock_close set status = 'IN_PROGRESS', finished_at = null where close_id = %s", (cid,))
    conn.execute("update stock_close set release_acked_at = now() where close_id = %s", (cid,))


def test_cancelled_close_cannot_be_confirmed(conn, acc):
    make_product(conn, "cider", 5)
    cid = _counting_close(conn, acc)
    conn.execute("insert into stock_count (close_id, product_id, account_id, counted_qty, book_qty) "
                 "select %s, product_id, %s, stock_qty, stock_qty from product", (cid, acc))
    conn.execute("update stock_close set status = 'CANCELLED', finished_at = now() where close_id = %s", (cid,))
    with raises_msg("끝난 마감은 바꿀 수 없음"):
        _confirm(conn, cid, acc)
