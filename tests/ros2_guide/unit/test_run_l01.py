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
