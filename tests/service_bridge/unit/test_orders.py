"""UT-03: 멱등 주문 접수."""
import sqlite3

import pytest

from sbt_bridge.core.db import Database
from sbt_bridge.core.orders import IdempotencyConflict, OrderDesk
from sbt_bridge.core.outbox import Outbox


def _desk():
    db = Database()
    db.init_schema()
    return db, OrderDesk(db, Outbox(db))


def _count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.plan_id("UT-03")
def test_idempotent_accept(record):
    db, desk = _desk()
    responses = [desk.accept("key-1", {"item": "A", "qty": 1}) for _ in range(3)]
    assert responses[0] == responses[1] == responses[2]
    assert responses[0][0] == 201
    assert responses[0][1]["state"] == "PENDING"
    assert _count(db, "orders") == 1
    assert _count(db, "outbox") == 1
    record.observe("responses", responses)


def test_same_key_different_payload_conflicts():
    db, desk = _desk()
    desk.accept("key-1", {"item": "A"})
    with pytest.raises(IdempotencyConflict):
        desk.accept("key-1", {"item": "B"})
    assert _count(db, "orders") == 1


class FailingDatabase(Database):
    """fail_commits 횟수만큼 commit을 실패시킵니다."""

    def __init__(self, fail_commits=0):
        super().__init__()
        self.fail_commits = fail_commits

    def commit(self):
        if self.fail_commits > 0:
            self.fail_commits -= 1
            raise sqlite3.OperationalError("주입된 커밋 실패")
        super().commit()


def _failing_desk(fail_commits):
    db = FailingDatabase()
    db.init_schema()
    db.fail_commits = fail_commits
    return db, OrderDesk(db, Outbox(db))


def test_commit_failure_leaves_no_order():
    """회귀(검토 사항 3.5 P-01): 커밋이 실패하면 주문과 outbox에 미커밋 행이 남지 않음."""
    db, desk = _failing_desk(fail_commits=1)
    with pytest.raises(sqlite3.OperationalError):
        desk.accept("key-1", {"item": "A"})
    assert _count(db, "orders") == 0
    assert _count(db, "outbox") == 0
    db.commit()  # 다른 경로의 커밋이 실패한 주문을 확정하지 않음
    assert _count(db, "orders") == 0


def test_retry_after_commit_failure_commits_once():
    """회귀(검토 사항 3.5 P-01): 커밋 실패 뒤 같은 키로 재시도하면 주문과 outbox가 각각 1건만 커밋됨."""
    db, desk = _failing_desk(fail_commits=1)
    with pytest.raises(sqlite3.OperationalError):
        desk.accept("key-1", {"item": "A"})
    first = desk.accept("key-1", {"item": "A"})
    again = desk.accept("key-1", {"item": "A"})
    assert first[0] == 201
    assert again == first
    db.rollback()  # 다른 경로의 롤백에도 접수된 주문이 사라지지 않음
    assert _count(db, "orders") == 1
    assert _count(db, "outbox") == 1
