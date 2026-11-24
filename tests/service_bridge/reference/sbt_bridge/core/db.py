"""SQLite 래퍼. 스레드 간 공유(내부 락).

브리지는 ROS executor 스레드들과 uvicorn 이벤트 루프가 연결 하나를 함께 씁니다.
- lock은 RLock입니다. 여러 문장을 한 트랜잭션으로 묶을 때 호출자가 `with db.lock:`으로 감싸고,
  그 안에서 execute/commit을 다시 불러도 됩니다.
- 시험용 브리지는 기본으로 메모리 DB를 씁니다(프로세스 재시작 복구는 계획서 범위 밖).
"""
import sqlite3
import threading

SCHEMA = """
-- outbox: 주문별 FMS 제출 상태 (core/outbox.py)
-- orders: Idempotency-Key별 첫 응답 (core/orders.py)
-- alerts: 수신 알림. seq가 WS 재생 순번, src_seq는 발신 측 순번 (core/alert_inbox.py)
CREATE TABLE IF NOT EXISTS outbox (
    order_id TEXT PRIMARY KEY,
    state    TEXT NOT NULL,
    payload  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    idem_key     TEXT PRIMARY KEY,
    order_id     TEXT NOT NULL UNIQUE,
    payload_norm TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    body_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL UNIQUE,
    robot_id TEXT NOT NULL,
    boot_id  TEXT NOT NULL,
    src_seq  INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    text     TEXT NOT NULL,
    stamp_ns INTEGER NOT NULL
);
"""


class Result:
    """락 안에서 미리 읽어 둔 결과.

    커서를 락 밖에서 읽지 않도록 합니다(연결을 스레드 간 공유하므로).
    """

    def __init__(self, cur):
        self.rows = cur.fetchall() if cur.description else []
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Database:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level="DEFERRED")
        self.lock = threading.RLock()

    def init_schema(self) -> None:
        with self.lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params=()) -> Result:
        with self.lock:
            return Result(self._conn.execute(sql, params))

    def commit(self) -> None:
        with self.lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self.lock:
            self._conn.rollback()
