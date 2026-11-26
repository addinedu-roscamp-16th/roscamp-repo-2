"""Task 1: 테이블 · 키 · FK · 기본값 (설계 문서 4절)."""

TABLES = {"account", "product", "stock_ledger", "orders", "order_item", "trip", "trip_item",
          "status_history", "alert", "admin_action", "stock_close", "stock_count", "app_setting",
          "ws_event", "bridge_session", "trip_dispatch"}


def _constraint_cols(conn, kind):
    rows = conn.execute(
        """
        select tc.table_name, tc.constraint_name, kcu.column_name, kcu.ordinal_position
        from information_schema.table_constraints tc
        join information_schema.key_column_usage kcu
          on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema
        where tc.table_schema = 'public' and tc.constraint_type = %s
        order by tc.table_name, tc.constraint_name, kcu.ordinal_position
        """,
        (kind,),
    ).fetchall()
    out = {}
    for table, name, col, _ in rows:
        out.setdefault((table, name), []).append(col)
    return {(t, tuple(sorted(cols))) for (t, _), cols in out.items()}


def _unique_indexes(conn):
    """UNIQUE 제약과 UNIQUE 인덱스(부분 인덱스 제외) 모두."""
    rows = conn.execute(
        """
        select t.relname, array_agg(a.attname::text order by a.attname)
        from pg_index i
        join pg_class t on t.oid = i.indrelid
        join pg_namespace n on n.oid = t.relnamespace and n.nspname = 'public'
        join pg_attribute a on a.attrelid = t.oid and a.attnum = any(i.indkey)
        where i.indisunique and not i.indisprimary and i.indpred is null
        group by i.indexrelid, t.relname
        """
    ).fetchall()
    return {(t, tuple(cols)) for t, cols in rows}


def test_all_tables_exist(conn):
    rows = conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'public' and table_type = 'BASE TABLE'"
    ).fetchall()
    assert {r[0] for r in rows} == TABLES


def test_composite_keys(conn):
    pks = _constraint_cols(conn, "PRIMARY KEY")
    assert ("order_item", ("order_id", "product_id")) in pks
    assert ("trip_item", ("product_id", "trip_id")) in pks
    uniques = _unique_indexes(conn)
    assert ("trip", ("order_id", "seq")) in uniques
    assert ("stock_ledger", ("corrects_ledger_id",)) in uniques
    assert ("stock_count", ("close_id", "product_id")) in uniques
    assert ("account", ("login_id",)) in uniques
    assert ("product", ("kind",)) in uniques
    assert ("orders", ("idem_key",)) in uniques
    assert ("alert", ("event_id",)) in uniques
    assert ("admin_action", ("request_id",)) in uniques


FKS = {
    ("orders", "account_id"): "account",
    ("order_item", "order_id"): "orders",
    ("order_item", "product_id"): "product",
    ("trip", "order_id"): "orders",
    ("trip_item", "trip_id"): "trip",
    ("trip_item", "product_id"): "product",
    ("stock_ledger", "product_id"): "product",
    ("stock_ledger", "corrects_ledger_id"): "stock_ledger",
    ("stock_ledger", "trip_id"): "trip",
    ("stock_ledger", "count_id"): "stock_count",
    ("stock_ledger", "account_id"): "account",
    ("status_history", "order_id"): "orders",
    ("status_history", "trip_id"): "trip",
    ("status_history", "account_id"): "account",
    ("alert", "order_id"): "orders",
    ("alert", "trip_id"): "trip",
    ("alert", "product_id"): "product",
    ("alert", "resolved_by"): "account",
    ("admin_action", "account_id"): "account",
    ("admin_action", "order_id"): "orders",
    ("admin_action", "trip_id"): "trip",
    ("admin_action", "alert_id"): "alert",
    ("admin_action", "close_id"): "stock_close",
    ("stock_close", "started_by"): "account",
    ("stock_close", "finished_by"): "account",
    ("stock_count", "close_id"): "stock_close",
    ("stock_count", "product_id"): "product",
    ("stock_count", "account_id"): "account",
    ("app_setting", "updated_by"): "account",
}


def test_foreign_keys(conn):
    rows = conn.execute(
        """
        select cl.relname, a.attname, rcl.relname
        from pg_constraint c
        join pg_class cl on cl.oid = c.conrelid
        join pg_class rcl on rcl.oid = c.confrelid
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
        where c.contype = 'f'
        """
    ).fetchall()
    found = {(t, col): ref for t, col, ref in rows}
    missing = {k: v for k, v in FKS.items() if found.get(k) != v}
    assert missing == {}


def test_defaults(conn):
    rows = conn.execute(
        "select table_name, column_name, column_default from information_schema.columns where table_schema = 'public'"
    ).fetchall()
    d = {(t, c): (v or "") for t, c, v in rows}
    assert "'OPEN'" in d[("alert", "status")]
    assert "'IN_PROGRESS'" in d[("stock_close", "status")]
    assert "'MANUAL'" in d[("stock_close", "close_kind")]
    for key in [("trip_item", "qty_placed"), ("trip_item", "qty_missing"), ("stock_count", "in_transit_qty"),
                ("product", "stock_qty"), ("product", "low_stock_threshold")]:
        assert d[key] == "0", key
    for t in TABLES - {"order_item", "trip_item", "app_setting", "product", "trip_dispatch"}:
        col = {"stock_close": "started_at", "bridge_session": "hello_at"}.get(t, "created_at")
        assert "now()" in d[(t, col)], t
