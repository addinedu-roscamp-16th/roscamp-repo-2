"""DB 연결 풀 (ROS 의존 없음)."""
import os

from psycopg_pool import ConnectionPool

DEFAULT_DSN = "postgresql://postgres:ifp_test@localhost:55432/ifp_test"
SESSION_OPTIONS = (
    "-c statement_timeout=1500 -c lock_timeout=500 "
    "-c idle_in_transaction_session_timeout=10000"
)


def dsn_from_env() -> str:
    return os.environ.get("IPONIC_DB_DSN", DEFAULT_DSN)


def make_pool(dsn: str, *, open: bool = True) -> ConnectionPool:
    return ConnectionPool(
        dsn,
        min_size=4,
        max_size=4,
        timeout=0.5,
        max_waiting=8,
        check=ConnectionPool.check_connection,
        kwargs={"options": SESSION_OPTIONS, "autocommit": False},
        open=open,
    )
