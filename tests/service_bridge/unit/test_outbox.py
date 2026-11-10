"""UT-01, UT-02: outbox 상태 전이."""
import pathlib

import pytest

from sbt_bridge.core.db import Database
from sbt_bridge.core.outbox import InvalidTransition, OrderState as S, Outbox

PKG = pathlib.Path(__file__).resolve().parents[1] / "reference" / "sbt_bridge"


def _outbox():
    db = Database()
    db.init_schema()
    return Outbox(db)


@pytest.mark.plan_id("UT-01")
def test_outbox_transitions(record):
    ob = _outbox()
    ob.add("a", "{}")
    for to in (S.SENT, S.ACCEPTED, S.DONE):
        ob.transition("a", to)
    assert ob.state("a") == S.DONE
    ob.add("b", "{}")
    ob.transition("b", S.SENT)
    ob.transition("b", S.REJECTED)
    assert ob.state("b") == S.REJECTED

    rejected = []
    cases = [(S.PENDING, S.DONE), (S.DONE, S.SENT), (S.REJECTED, S.ACCEPTED), (S.ACCEPTED, S.SENT)]
    paths = {S.PENDING: [], S.DONE: [S.SENT, S.ACCEPTED, S.DONE],
             S.REJECTED: [S.SENT, S.REJECTED], S.ACCEPTED: [S.SENT, S.ACCEPTED]}
    for i, (frm, to) in enumerate(cases):
        oid = f"c{i}"
        ob.add(oid, "{}")
        for step in paths[frm]:
            ob.transition(oid, step)
        with pytest.raises(InvalidTransition):
            ob.transition(oid, to)
        assert ob.state(oid) == frm
        rejected.append(f"{frm.value}->{to.value}")
    record.observe("rejected_transitions", rejected)


# 계획서 UT-01 허용 전이 (ALLOWED를 import하지 않고 따로 적음).
# SENT→DONE은 복구 조회가 DONE을 돌려줄 때.
EXPECTED_ALLOWED = {("PENDING", "SENT"), ("SENT", "ACCEPTED"), ("SENT", "REJECTED"),
                    ("SENT", "DONE"), ("ACCEPTED", "DONE")}
PATH_TO = {"PENDING": [], "SENT": ["SENT"], "ACCEPTED": ["SENT", "ACCEPTED"],
           "DONE": ["SENT", "ACCEPTED", "DONE"], "REJECTED": ["SENT", "REJECTED"]}


def test_outbox_full_transition_matrix():
    ob = _outbox()
    bad = []
    for i, frm in enumerate(PATH_TO):
        for j, to in enumerate(PATH_TO):
            oid = f"m{i}{j}"
            ob.add(oid, "{}")
            for step in PATH_TO[frm]:
                ob.transition(oid, S(step))
            try:
                ob.transition(oid, S(to))
                ok = True
            except InvalidTransition:
                ok = False
            if ok != ((frm, to) in EXPECTED_ALLOWED):
                bad.append(f"{frm}->{to} ok={ok}")
    assert bad == []


def test_single_transition_site():
    hits = [p.relative_to(PKG).as_posix() for p in PKG.rglob("*.py")
            if "UPDATE outbox SET state" in p.read_text(encoding="utf-8")]
    assert hits == ["core/outbox.py"]


@pytest.mark.plan_id("UT-02")
def test_goal_id_action_path(record):
    pytest.skip("미실행: 최소 브리지에 ROS 2 Action 경로가 없음 (계획서 3장)")
