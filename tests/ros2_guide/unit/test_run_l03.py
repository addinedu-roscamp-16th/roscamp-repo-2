from run_l03 import CONTROL_COMBOS, RULE, combo_observation, rule_verdict, sub_verdict, topic_for


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


def test_combo_observation():
    obs = combo_observation(100, {"processed": 3, "staleness_ms": [1.0, 2.0, 3.0], "bad": 0})
    assert obs["sent"] == 100 and obs["processed"] == 3 and obs["dropped"] == 97
    assert obs["staleness_stats"]["max"] == 3.0
    assert combo_observation(None, {"processed": 0, "staleness_ms": []}) is None
    assert combo_observation(10, None) is None


def test_rule_verdict():
    good = {"count": 10, "max": 50.0}
    assert rule_verdict([good] * 5) == "통과"
    assert rule_verdict([good] * 4) == "실패"
    assert rule_verdict([good] * 4 + [None]) == "판정 불가"
    assert rule_verdict([good] * 4 + [{"count": 10, "max": 200.0}]) == "실패"
