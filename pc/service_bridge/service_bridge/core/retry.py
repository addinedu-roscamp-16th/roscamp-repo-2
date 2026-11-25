# ---- 트랜잭션 한 번 실행과 교착 · 잠금 대기 초과의 한 번 다시 하기 ----
import time
from typing import Callable, TypeVar

import psycopg
from psycopg import errors
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

T = TypeVar("T")

# 다시 해서 풀릴 수 있는 것만: 40P01(교착) · 55P03(lock_timeout 초과)
_RETRYABLE = (errors.DeadlockDetected, errors.LockNotAvailable)


# ---- 다시 하기 한 번 ----
def run_tx(pool: ConnectionPool, fn: Callable[[psycopg.Connection], T], deadline: float) -> T:
    """`pool.connection()` 한 번을 트랜잭션 한 번으로 `fn`을 실행한다(정상 종료면 커밋).

    40P01 · 55P03이면 `deadline`(time.monotonic 기준) 전일 때 한 번만 다시 한다.
    두 번째 예외와 그 밖의 예외는 그대로 올린다.
    """
    # 첫 시도는 롤백되므로 트랜잭션 전체를 처음부터 다시 한다.
    # 마감(받은 시각 + 2.5초)은 fleet 호출 한도 3초보다 짧게 둔다(한도 뒤 답은 버려짐)
    try:
        with pool.connection() as conn:
            return fn(conn)
    except _RETRYABLE:
        if time.monotonic() >= deadline:
            raise
    with pool.connection() as conn:
        return fn(conn)


# ---- DB 오류를 응답 값으로 바꾸기 ----
DB_ERRORS = (psycopg.Error, PoolTimeout, PoolClosed)


def run_tx_guarded(pool: ConnectionPool, fn: Callable[[psycopg.Connection], T], deadline: float,
                   on_db_error: Callable[[Exception], T]) -> T:
    """`run_tx`에 DB 오류(psycopg · 풀 시간 초과 · 풀 닫힘) 감싸기를 더한다.

    오류면 `on_db_error(e)`. 호출한 쪽은 accepted=false로 답하고 fleet이 같은 번호로 다시 보낸다.
    """
    try:
        return run_tx(pool, fn, deadline)
    except DB_ERRORS as e:
        return on_db_error(e)
