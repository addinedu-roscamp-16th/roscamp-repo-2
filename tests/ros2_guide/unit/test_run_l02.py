from run_l02 import combo_key, combos, fill_counts, l02_problem, parse_pub


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
    assert parse_pub(out) == {"sent": 3, "matched": True, "elapsed_s": None,
                              "achieved_hz": None, "error": None}
    assert parse_pub("nothing\n") is None


def test_parse_pub_rate_and_error():
    out = ('{"sent": 300, "matched": true, "elapsed_s": 29.9, "achieved_hz": 10.0, '
           '"error": null}\n')
    got = parse_pub(out)
    assert (got["elapsed_s"], got["achieved_hz"], got["error"]) == (29.9, 10.0, None)
    err = parse_pub('{"sent": 12, "matched": true, "error": "RuntimeError(\'x\')"}\n')
    assert err["sent"] == 12 and err["error"] == "RuntimeError('x')"


def test_l02_problem_reports_publisher_error():
    pub = {"sent": 12, "matched": True, "error": "RuntimeError('x')"}
    assert l02_problem(pub, False, 300) == "발행 노드 오류(RuntimeError('x'))"


def test_fill_counts_with_and_without_publisher_line():
    pub = {"sent": 4, "matched": True, "elapsed_s": 0.3, "achieved_hz": 10.0, "error": None}
    rec = fill_counts({}, pub, {"seqs": [0, 1, 3], "recv_ns": [0, 100_000_000], "bad": 0})
    assert (rec["sent"], rec["received"], rec["lost"]) == (4, 3, 1)
    assert (rec["elapsed_s"], rec["achieved_hz"], rec["error"]) == (0.3, 10.0, None)
    assert rec["max_interval_ms"] == 100.0
    none = fill_counts({}, None, {"seqs": [0, 1], "recv_ns": []})
    assert none["sent"] is None and none["loss_rate"] is None and none["received"] is None
    assert none["elapsed_s"] is None and none["achieved_hz"] is None
    no_out = fill_counts({}, pub, None)
    assert no_out["sent"] == 4 and no_out["loss_rate"] is None


def test_combo_order_reliability_size_rate_rep():
    cfg = {"reliabilities": ["a", "b"], "sizes": {"S": 1, "L": 2}, "rates_hz": [10, 50]}
    got = combos(cfg, 2)
    assert len(got) == 16
    assert got[0] == ("a", "S", 1, 10, 0)
    assert got[1] == ("a", "S", 1, 10, 1)
    assert got[2] == ("a", "S", 1, 50, 0)
    assert got[4] == ("a", "L", 2, 10, 0)
    assert got[8][0] == "b"
