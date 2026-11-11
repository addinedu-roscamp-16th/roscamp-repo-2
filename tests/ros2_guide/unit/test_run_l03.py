from run_l03 import (
    CONTROL_COMBOS,
    RULE,
    combo_observation,
    data_problem,
    gated_rule_verdict,
    parse_pub,
    rule_notes,
    rule_verdict,
    sub_verdict,
    target_files,
    topic_for,
)

MS = 1_000_000


def test_combos():
    assert sorted(CONTROL_COMBOS) == sorted(
        [(d, r) for d in (1, 5, 50) for r in ("best_effort", "reliable")]
    )


def test_rule():
    assert RULE == {"mode": "latest", "depth": 1, "reliability": "best_effort", "repeats": 5}


def test_topic_names():
    assert topic_for("naive", 50, "reliable", 2) == "/r2g/l03/naive_d50_rel_2"
    assert topic_for("latest", 1, "best_effort", 0) == "/r2g/l03/latest_d1_be_0"


def test_sub_verdict():
    assert sub_verdict(True, True, "측정") == "측정"
    assert sub_verdict(False, True, "측정") == "판정 불가"
    assert sub_verdict(True, False, "측정") == "판정 불가"


def test_parse_pub():
    line = '{"sent": 100, "last_publish_ns": 5, "matched": true}'
    assert parse_pub("noise\n" + line + "\n") == {
        "sent": 100, "last_publish_ns": 5, "matched": True}
    assert parse_pub('{"sent": "x"}') is None
    assert parse_pub("") is None
    assert parse_pub('{"sent": 0, "last_publish_ns": null, "matched": false}') == {
        "sent": 0, "last_publish_ns": None, "matched": False}


def _out(samples):
    return {"processed": len(samples), "samples": samples, "bad": 0}


def test_combo_observation_live_window():
    pub = {"sent": 100, "last_publish_ns": 1000 * MS, "matched": True}
    samples = [[0, 30 * MS], [500 * MS, 540 * MS], [980 * MS, 1140 * MS]]
    obs = combo_observation(pub, _out(samples))
    assert obs["sent"] == 100 and obs["processed"] == 3 and obs["dropped"] == 97
    assert obs["processed_live"] == 2
    assert obs["staleness_stats"]["max"] == 40.0 and obs["staleness_stats"]["count"] == 2
    assert obs["tail_count"] == 1 and obs["tail_max_ms"] == 160.0
    assert obs["matched"] is True and obs["last_publish_ns"] == 1000 * MS
    assert "samples" not in obs
    assert combo_observation(None, _out([])) is None
    assert combo_observation(pub, None) is None
    assert combo_observation({**pub, "last_publish_ns": None}, _out(samples))["processed_live"] == 0


def test_data_problem():
    pub = {"sent": 10, "last_publish_ns": 1000 * MS, "matched": True}
    good = combo_observation(pub, _out([[0, 30 * MS]]))
    assert data_problem(good) is None
    assert data_problem(combo_observation({**pub, "matched": False}, _out([[0, 30 * MS]]))) \
        == "구독자 매칭 실패"
    assert data_problem(combo_observation(pub, _out([]))) == "처리한 메시지 없음"
    tail_only = combo_observation(pub, _out([[990 * MS, 1100 * MS]]))
    assert "처리한 메시지 없음" in data_problem(tail_only)


def test_rule_verdict():
    good = {"count": 10, "max": 50.0}
    assert rule_verdict([good] * 5) == "통과"
    assert rule_verdict([good] * 4) == "실패"
    assert rule_verdict([good] * 4 + [None]) == "판정 불가"
    assert rule_verdict([good] * 4 + [{"count": 10, "max": 200.0}]) == "실패"


def test_gated_rule_verdict():
    good = {"count": 10, "max": 50.0}
    assert gated_rule_verdict([good] * 5, 20.0, 5) == ("통과", None)
    v, note = gated_rule_verdict([good] * 2, 3.0, 2)
    assert v == "판정 불가" and note == "개발 실행(조합 3초, 반복 2회): 공식 판정 아님"
    assert gated_rule_verdict([good] * 5, 3.0, 5)[0] == "판정 불가"
    assert gated_rule_verdict([good] * 2, 20.0, 2)[0] == "판정 불가"


def test_rule_notes():
    reps = [{"ok": True, "reason": None}, {"ok": False, "reason": "처리한 메시지 없음"}]
    notes = rule_notes(reps)
    assert notes == ["반복 1: 처리한 메시지 없음", "일부 반복에 데이터가 없어 판정 불가"]
    assert rule_notes([{"ok": True, "reason": None}]) == []


def test_target_files():
    assert target_files() == [
        "L-03-대조.json", "L-03-대조.samples.json", "L-03-규칙.json", "L-03-규칙.samples.json"]
