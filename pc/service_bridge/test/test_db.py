def test_pool_session_has_timeouts(pool):
    with pool.connection() as c:
        assert c.execute("show lock_timeout").fetchone()[0] == "500ms"
        assert c.execute("show statement_timeout").fetchone()[0] == "1500ms"
        assert c.execute("show idle_in_transaction_session_timeout").fetchone()[0] == "10s"
