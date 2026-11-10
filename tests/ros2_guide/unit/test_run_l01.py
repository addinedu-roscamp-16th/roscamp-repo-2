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


def test_all_variants_implemented():
    from run_l01 import IMPLEMENTED

    assert set(IMPLEMENTED) == set(VARIANTS)


def test_late_trial_excluded():
    from run_l01 import late_trial_excluded

    assert late_trial_excluded(False, 0, True, False, 0, True) is False
    assert late_trial_excluded(True, None, True, False, 0, True) is True  # pub timed out
    assert late_trial_excluded(False, 1, True, False, 0, True) is True  # pub crashed
    assert late_trial_excluded(False, 0, False, False, 0, True) is True  # no pub line
    assert late_trial_excluded(False, 0, True, True, None, True) is True  # sub timed out
    assert late_trial_excluded(False, 0, True, False, 1, True) is True  # sub crashed
    assert late_trial_excluded(False, 0, True, False, 0, False) is True  # no sub summary


def test_service_call_ok():
    from run_l01 import service_call_ok

    assert service_call_ok(False, 0, {"trial": 0, "ok": True, "wait_ms": 3.0}) is True
    assert service_call_ok(False, 0, {"trial": 0, "ok": False, "wait_ms": 5000.0}) is False
    assert service_call_ok(False, 0, None) is False
    assert service_call_ok(True, None, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 1, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 0, {"trial": 0, "ok": "true"}) is False
