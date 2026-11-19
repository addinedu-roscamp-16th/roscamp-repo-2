"""시험 데이터 만들기."""
import itertools

_seq = itertools.count(1)


def make_account(conn, role="ADMIN", login=None) -> int:
    login = login or f"user{next(_seq)}"
    return conn.execute(
        "insert into account (login_id, password_hash, role, name) values (%s, 'x', %s, %s) returning account_id",
        (login, role, login),
    ).fetchone()[0]


def ledger(conn, product_id, entry_type, qty, reason=None, **cols) -> int:
    cols = {"product_id": product_id, "entry_type": entry_type, "change_qty": qty, "reason": reason, **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"insert into stock_ledger ({names}) values ({marks}) returning ledger_id", list(cols.values())
    ).fetchone()[0]


def make_product(conn, kind, qty=0, threshold=0) -> int:
    pid = conn.execute(
        "insert into product (kind, name, low_stock_threshold) values (%s, %s, %s) returning product_id",
        (kind, kind, threshold),
    ).fetchone()[0]
    if qty > 0:
        ledger(conn, pid, "INBOUND", qty, "REGULAR")
    return pid


def make_order(conn, account_id, trips, order_id=None, created_at=None):
    """trips: [{product_id: qty, ...}, ...] 운반 건마다 상품 수량. 모두 RECEIVED."""
    order_id = order_id or f"O-T-{next(_seq):04d}"
    conn.execute(
        "insert into orders (order_id, account_id, status, created_at) values (%s, %s, 'RECEIVED', coalesce(%s, now()))",
        (order_id, account_id, created_at),
    )
    totals = {}
    for t in trips:
        for pid, q in t.items():
            totals[pid] = totals.get(pid, 0) + q
    for pid, q in totals.items():
        conn.execute("insert into order_item (order_id, product_id, qty_ordered) values (%s, %s, %s)", (order_id, pid, q))
    trip_ids = []
    for seq, t in enumerate(trips, start=1):
        tid = f"{order_id}-{seq}"
        conn.execute(
            "insert into trip (trip_id, order_id, seq, status) values (%s, %s, %s, 'RECEIVED')", (tid, order_id, seq)
        )
        for pid, q in t.items():
            conn.execute("insert into trip_item (trip_id, product_id, qty_ordered) values (%s, %s, %s)", (tid, pid, q))
        trip_ids.append(tid)
    return order_id, trip_ids


def set_trip_status(conn, trip_id, status, placed=None):
    conn.execute("update trip set status = %s where trip_id = %s", (status, trip_id))
    if placed is not None:
        for pid, q in placed.items():
            conn.execute(
                "update trip_item set qty_placed = %s where trip_id = %s and product_id = %s", (q, trip_id, pid)
            )
        conn.execute("update trip set placed_at = now() where trip_id = %s", (trip_id,))
