from run_l02 import combo_key, combos, l02_problem, parse_pub


def test_combo_key():
    assert combo_key("best_effort", "1KB", 10, 0) == "best_effort_1KB_10Hz_r0"


def test_l02_problem():
    assert l02_problem(None, True, 300) == "발행 노드 시간 초과"
    assert l02_problem(None, False, 300) == "발행 노드 결과 줄 없음"
    assert l02_problem({"sent": 0, "matched": False}, False, 300) == "구독자 매칭 실패"
    assert (
        l02_problem({"sent": 120, "matched": True}, False, 300)
        == "발행 수 미달(실제 120 / 예정 300)"
    )
    assert l02_problem({"sent": 300, "matched": True}, False, 300) is None


def test_parse_pub_last_json_line():
    out = 'noise\n{"sent": 3, "matched": true}\n'
    assert parse_pub(out) == {"sent": 3, "matched": True}
    assert parse_pub("nothing\n") is None


def test_combo_order_reliability_size_rate_rep():
    cfg = {"reliabilities": ["a", "b"], "sizes": {"S": 1, "L": 2}, "rates_hz": [10, 50]}
    got = combos(cfg, 2)
    assert len(got) == 16
    assert got[0] == ("a", "S", 1, 10, 0)
    assert got[1] == ("a", "S", 1, 10, 1)
    assert got[2] == ("a", "S", 1, 50, 0)
    assert got[4] == ("a", "L", 2, 10, 0)
    assert got[8][0] == "b"
