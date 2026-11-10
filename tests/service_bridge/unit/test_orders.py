"""UT-03: 멱등 주문 접수."""
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
