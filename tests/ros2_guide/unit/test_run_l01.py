from run_l01 import VARIANTS, topic_for


def test_topic_names():
    assert topic_for("A") == "/r2g/l01/a"
    assert topic_for("E-TL") == "/r2g/l01/e_tl"
    assert len({topic_for(v) for v in VARIANTS}) == len(VARIANTS)


def test_variant_table():
    assert VARIANTS["A"] == {"profile": "EVENT", "mode": "once", "late_sub": False}
    assert VARIANTS["B"] == {"profile": "EVENT", "mode": "wait_match", "late_sub": False}
    assert VARIANTS["C"] == {"profile": "LATCHED", "mode": "once", "late_sub": False}
    assert VARIANTS["D"] == {"profile": "STATE", "mode": "once", "late_sub": False}
    assert VARIANTS["F"] == {"profile": "EVENT", "mode": "stream", "late_sub": False}
    assert VARIANTS["S"] == {"profile": None, "mode": "service", "late_sub": False}
    assert set(VARIANTS) == {"A", "B", "C", "D", "E-VOL", "E-TL", "F", "S"}


def test_sub_verdict():
    from run_l01 import sub_verdict

    assert sub_verdict(True, True, "통과") == "통과"
    assert sub_verdict(True, True, "측정") == "측정"
    assert sub_verdict(False, True, "통과") == "판정 불가"
    assert sub_verdict(True, False, "실패") == "판정 불가"


def test_late_sub_variants():
    assert VARIANTS["E-VOL"] == {"profile": "EVENT", "mode": "once", "late_sub": True}
    assert VARIANTS["E-TL"] == {"profile": "LATCHED", "mode": "once", "late_sub": True}
    assert VARIANTS["S"] == {"profile": None, "mode": "service", "late_sub": False}


def test_no_stub_path():
    import run_l01

    assert not hasattr(run_l01, "IMPLEMENTED")


def test_target_files():
    from run_l01 import target_files

    assert target_files(["A", "E-TL"]) == ["L-01-A.json", "L-01-E-TL.json"]


def test_late_offset_and_exclusion():
    from run_l01 import late_offset_ms, late_trial_exclusion

    assert late_offset_ms(2_000_000_000, 1_000_000_000) == 1000.0
    assert late_offset_ms(None, 1) is None
    assert late_offset_ms(1, None) is None

    def ex(**kw):
        a = dict(pub_ready=True, pub_timed_out=False, pub_rc=0, publish_ns=1, pub_sent=1,
                 sub_timed_out=False, sub_rc=0, sub_created_ns=2, offset_ms=1000.0)
        a.update(kw)
        return late_trial_exclusion(**a)

    assert ex() is None
    assert ex(offset_ms=500.0) is None
    assert "오프셋" in ex(offset_ms=499.9)
    assert "발행" in ex(pub_ready=False)
    assert "발행" in ex(pub_timed_out=True, pub_rc=None)
    assert "rc=1" in ex(pub_rc=1)
    assert "발행" in ex(publish_ns=None, offset_ms=None)
    assert "구독" in ex(sub_timed_out=True, sub_rc=None)
    assert "rc=2" in ex(sub_rc=2)
    assert "구독" in ex(sub_created_ns=None, offset_ms=None)
    assert "보낸 메시지 없음" in ex(pub_sent=0)
    assert "보낸 메시지 없음" in ex(pub_sent=None)


def test_pub_trial_exclusion():
    from run_l01 import pub_trial_exclusion

    good = {"trial": 0, "publish_ns": 5, "sent": 1}
    assert pub_trial_exclusion(False, 0, good) is None
    assert "타임아웃" in pub_trial_exclusion(True, None, good)
    assert "rc=1" in pub_trial_exclusion(False, 1, good)
    assert "결과 줄 없음" in pub_trial_exclusion(False, 0, None)
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {**good, "sent": 0})
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {**good, "publish_ns": None})
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {"trial": 0, "sent": 1})


def test_sent_nothing_cannot_reproduce_loss():
    """A rc=0 publisher that sent nothing leaves the denominator instead of counting as lost."""
    from run_l01 import counted_tally, l01_verdict, pub_trial_exclusion

    infos = {0: {"publish_ns": 5, "sent": 1}, 1: {"publish_ns": None, "sent": 0}}
    excluded = {t for t, i in infos.items() if pub_trial_exclusion(False, 0, i)}
    t = counted_tally(2, [0], excluded)
    assert excluded == {1} and t["lost"] == 0 and t["counted"] == 1
    assert l01_verdict("A", t, len(excluded)) == "측정"


def test_pub_sent_total():
    from run_l01 import pub_sent_total

    assert pub_sent_total([{"sent": 1}, {"sent": 30}, {"sent": None}, {}]) == 31
    assert pub_sent_total([]) == 0


def test_offset_stats():
    from run_l01 import offset_stats

    assert offset_stats([]) == {"count": 0, "min": None, "median": None, "max": None}
    assert offset_stats([1000.0, 1200.0, 1100.0]) == {
        "count": 3, "min": 1000.0, "median": 1100.0, "max": 1200.0}


def test_counted_tally():
    from run_l01 import counted_tally

    t = counted_tally(5, [0, 1, 4], {2, 3})
    assert (t["counted"], t["received"], t["lost"], t["lost_trials"]) == (3, 3, 0, [])
    assert "sent" not in t
    t = counted_tally(5, [0, 3], {3})
    assert (t["counted"], t["received"], t["lost"], t["lost_trials"]) == (4, 1, 3, [1, 2, 4])
    assert counted_tally(2, [], {0, 1})["counted"] == 0


def test_l01_verdict():
    from run_l01 import counted_tally, l01_verdict

    full = counted_tally(3, [0, 1, 2], set())
    assert l01_verdict("C", full, 0) == "통과"
    assert l01_verdict("C", counted_tally(3, [0, 1], set()), 0) == "실패"
    assert l01_verdict("C", counted_tally(3, [0, 1], {2}), 1) == "판정 불가"
    assert l01_verdict("S", counted_tally(3, [0, 1, 2], {2}), 1) == "판정 불가"
    assert l01_verdict("A", counted_tally(3, [0], {2}), 1) == "측정"
    assert l01_verdict("E-VOL", full, 0) == "측정"
    assert l01_verdict("C", counted_tally(2, [], {0, 1}), 2) == "판정 불가"
    assert l01_verdict("A", counted_tally(2, [], {0, 1}), 2) == "판정 불가"


def test_service_call_ok():
    from run_l01 import service_call_ok

    assert service_call_ok(False, 0, {"trial": 0, "ok": True, "wait_ms": 3.0}) is True
    assert service_call_ok(False, 0, {"trial": 0, "ok": False, "wait_ms": 5000.0}) is False
    assert service_call_ok(False, 0, None) is False
    assert service_call_ok(True, None, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 1, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 0, {"trial": 0, "ok": "true"}) is False


def test_l01_size_gate():
    from run_l01 import gated_verdict

    assert gated_verdict("C", "통과", 50) == ("통과", None)
    assert gated_verdict("C", "통과", 2) == ("판정 불가", "개발 실행(시도 2회): 공식 판정 아님")
    assert gated_verdict("S", "실패", 10)[0] == "판정 불가"
    assert gated_verdict("A", "측정", 2) == ("측정", "개발 실행(시도 2회): 공식 판정 아님")
    assert gated_verdict("F", "측정", 50) == ("측정", None)
