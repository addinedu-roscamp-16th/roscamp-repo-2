# ---- 브리지 DB 연결 풀과 세션 시간 한도 (ROS 의존 없음) ----
import os

from psycopg_pool import ConnectionPool

# 시험 DB 기본 주소(design/db/compose.yaml). 운영에서는 IPONIC_DB_DSN으로 준다
DEFAULT_DSN = "postgresql://postgres:ifp_test@localhost:55432/ifp_test"
# 세션마다 거는 시간 한도: 풀 대기 0.5초 + 실행 1.5초 + 여유가 fleet 호출 한도 3초 안에 들게.
# lock_timeout은 statement_timeout보다 짧게, 트랜잭션을 연 채 멈춘 연결은 10초 뒤 끊는다
SESSION_OPTIONS = (
    "-c statement_timeout=1500 -c lock_timeout=500 "
    "-c idle_in_transaction_session_timeout=10000"
)


# ---- DB 주소 ----
def dsn_from_env() -> str:
    return os.environ.get("IPONIC_DB_DSN", DEFAULT_DSN)


# ---- 연결 풀 만들기 ----
def make_pool(dsn: str, *, open: bool = True) -> ConnectionPool:
    # 크기는 실행기 스레드 4개와 같게 고정. 연결을 0.5초 안에 못 얻거나
    # 기다리는 요청이 8개를 넘으면 DB 오류로 답한다.
    # connection() 블록 하나가 트랜잭션 하나(정상이면 커밋, 예외면 롤백)
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
