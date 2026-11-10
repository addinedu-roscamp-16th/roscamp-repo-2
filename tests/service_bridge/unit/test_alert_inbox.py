"""UT-04, UT-05, UT-05-대조: 알림 수신과 ACK."""
import sqlite3

import pytest

from sbt_bridge.core.alert_inbox import AlertInbox, AlertRecord, ReceiveResult as R
from sbt_bridge.core.db import Database


class EventDatabase(Database):
    """commit 성공 시 events에 'commit'을 남기고, fail_commits 횟수만큼 commit을 실패시킵니다."""

    def __init__(self, events, fail_commits=0):
        super().__init__()
        self.events = events
        self.fail_commits = fail_commits

    def commit(self):
        if self.fail_commits > 0:
            self.fail_commits -= 1
            raise sqlite3.OperationalError("주입된 커밋 실패")
        super().commit()
        self.events.append("commit")


def _rec(boot="boot-a", seq=1):
    return AlertRecord(alert_id=f"{boot}-{seq}", robot_id="robot1", boot_id=boot, seq=seq,
                       level=2, text="t", stamp_ns=0)


def _inbox(fail_commits=0, **kw):
    events = []
    db = EventDatabase(events)
    db.init_schema()
    events.clear()
    db.fail_commits = fail_commits
    inbox = AlertInbox(db, send_ack=lambda aid: events.append(f"ack:{aid}"), **kw)
    return inbox, events


def _acks(events):
    return [e for e in events if e.startswith("ack:")]


@pytest.mark.plan_id("UT-04")
def test_duplicate_alert_reacks(record):
    inbox, events = _inbox()
    results = [inbox.receive(_rec()), inbox.receive(_rec())]
    assert results == [R.STORED, R.DUPLICATE]
    assert inbox.count() == 1
    assert len(_acks(events)) == 2
    record.observe("results", [r.value for r in results])


@pytest.mark.plan_id("UT-05")
def test_ack_only_after_commit(record):
    inbox, events = _inbox(fail_commits=1)
    rec = _rec()
    assert inbox.receive(rec) == R.COMMIT_FAILED
    assert inbox.count() == 0
    assert _acks(events) == []
    assert inbox.receive(rec) == R.STORED
    assert inbox.count() == 1
    assert _acks(events) == [f"ack:{rec.alert_id}"]
    assert events.index("commit") < events.index(f"ack:{rec.alert_id}")
    record.observe("events", list(events))


@pytest.mark.plan_id("UT-05-대조")
def test_ack_before_commit_loses_alert(record):
    inbox, events = _inbox(fail_commits=1, ack_before_commit=True)
    rec = _rec()
    inbox.receive(rec)
    # 위반 변형에서 기대한 실패: ACK는 나갔는데 저장은 0건 → 발신 측은 재전송하지 않아 유실
    assert inbox.count() == 0
    assert _acks(events) == [f"ack:{rec.alert_id}"]
    record.observe("acked_but_not_stored", [rec.alert_id])
    record.note("위반 변형(ack_before_commit)에서 커밋 실패 후에도 ACK 1회 관측 — 기대한 실패")


def test_new_boot_same_seq_is_not_duplicate():
    inbox, _ = _inbox()
    assert inbox.receive(_rec("boot-a", 1)) == R.STORED
    assert inbox.receive(_rec("boot-b", 1)) == R.STORED


def test_alerts_since_order():
    inbox, _ = _inbox()
    for s in (1, 2, 3):
        inbox.receive(_rec(seq=s))
    got = inbox.alerts_since(1)
    assert [seq for seq, _ in got] == [2, 3]
    assert [r.alert_id for _, r in got] == ["boot-a-2", "boot-a-3"]


def test_max_seq():
    inbox, _ = _inbox()
    assert inbox.max_seq() == 0
    inbox.receive(_rec(seq=1))
    inbox.receive(_rec(seq=2))
    assert inbox.max_seq() == 2
