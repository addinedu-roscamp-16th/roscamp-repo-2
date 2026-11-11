import pytest

from r2g.judge import (
    first_receive,
    heartbeat_gaps,
    judge_l01,
    judge_l03_rule,
    l02_is_standard,
    l04_is_standard,
    l04_risk,
    loss_stats,
    max_interval_ms,
    staleness_stats,
    tally,
)


def test_tally():
    t = tally(5, [0, 1, 1, 3, 9])
    assert t == {"sent": 5, "received": 3, "lost": 2, "loss_rate": 0.4, "lost_trials": [2, 4]}


@pytest.mark.parametrize("variant,lost,expected", [
    ("B", 0, "통과"), ("B", 1, "실패"), ("E-TL", 0, "통과"), ("S", 2, "실패"),
    ("A", 3, "측정"), ("D", 0, "측정"), ("E-VOL", 50, "측정"), ("F", 0, "측정")])
def test_judge_l01(variant, lost, expected):
    t = tally(50, range(lost, 50))
    assert judge_l01(variant, t, expected=50) == expected


def test_judge_l01_short_run_fails():
    assert judge_l01("B", tally(49, range(49)), expected=50) == "실패"


def test_first_receive():
    assert first_receive(1_000_000, [(4, 401_000_000), (5, 501_000_000)]) == \
        {"first_seq": 4, "time_to_first_ms": 400.0}
    assert first_receive(0, []) is None


def test_staleness_trend():
    s = staleness_stats([10.0] * 10 + [100.0] * 10)
    assert s["count"] == 20 and s["max"] == 100.0 and s["trend_ms"] == 90.0


def test_staleness_short_and_p99():
    s = staleness_stats([float(i) for i in range(1, 6)])
    assert s["trend_ms"] == 0.0 and s["p99"] == 5.0 and s["p50"] == 3.0


def test_judge_l03_rule():
    ok = {"count": 100, "max": 140.0}
    assert judge_l03_rule([ok] * 5) == "통과"
    assert judge_l03_rule([ok] * 4 + [{"count": 100, "max": 151.0}]) == "실패"
    assert judge_l03_rule([ok] * 4) == "실패"
    assert judge_l03_rule([ok] * 4 + [{"count": 0, "max": 0.0}]) == "실패"


def test_split_live():
    from r2g.judge import split_live

    samples = [[0, 30_000_000], [100_000_000, 140_000_000], [180_000_000, 340_000_000]]
    live, tail = split_live(samples, 200_000_000)
    assert live == [30.0, 40.0] and tail == [160.0]
    live, tail = split_live(samples, 340_000_000)
    assert live == [30.0, 40.0, 160.0] and tail == []
    assert split_live([], 1) == ([], [])


def test_tail_summary():
    from r2g.judge import tail_summary

    assert tail_summary([]) == {"tail_count": 0, "tail_max_ms": None}
    assert tail_summary([161.94, 20.0]) == {"tail_count": 2, "tail_max_ms": 161.9}


def test_standard_sizes():
    from r2g.judge import (
        STANDARD_REPEATS_L03,
        STANDARD_SECONDS_L03,
        STANDARD_TRIALS_L01,
        l01_is_standard,
        l03_is_standard,
    )

    assert (STANDARD_TRIALS_L01, STANDARD_SECONDS_L03, STANDARD_REPEATS_L03) == (50, 20.0, 5)
    assert l01_is_standard(50) and not l01_is_standard(2)
    assert l03_is_standard(20, 5) and l03_is_standard(20.0, 5)
    assert not l03_is_standard(3, 5) and not l03_is_standard(20, 2)


@pytest.mark.parametrize("judged,is_rule,standard,expected", [
    ("통과", True, True, ("통과", None)),
    ("실패", True, True, ("실패", None)),
    ("통과", True, False, ("판정 불가", "개발 실행(시도 2회): 공식 판정 아님")),
    ("실패", True, False, ("판정 불가", "개발 실행(시도 2회): 공식 판정 아님")),
    ("측정", False, False, ("측정", "개발 실행(시도 2회): 공식 판정 아님")),
    ("측정", False, True, ("측정", None)),
    ("판정 불가", True, False, ("판정 불가", "개발 실행(시도 2회): 공식 판정 아님")),
])
def test_size_gate(judged, is_rule, standard, expected):
    from r2g.judge import size_gate

    assert size_gate(judged, is_rule, standard, "시도 2회") == expected


def test_l03_boundary_uses_unrounded_max():
    s = staleness_stats([10.0, 150.04])
    assert s["max"] == 150.0 and s["max_exact"] == 150.04
    assert judge_l03_rule([s] * 5) == "실패"
    assert judge_l03_rule([staleness_stats([10.0, 150.0])] * 5) == "통과"


def test_loss_stats():
    s = loss_stats(10, [0, 1, 2, 2, 5, 6, 9, 42])
    assert (s["received"], s["lost"], s["dup"], s["longest_gap"]) == (6, 4, 1, 2)
    assert s["loss_rate"] == 0.4
    assert loss_stats(0, [])["loss_rate"] is None
    assert loss_stats(5, [])["longest_gap"] == 5


def test_max_interval_uses_one_clock():
    assert max_interval_ms([0, 100_000_000, 350_000_000]) == 250.0
    assert max_interval_ms([5]) is None


def test_l02_is_standard():
    assert l02_is_standard(30, 3)
    assert not l02_is_standard(3, 1)


def test_heartbeat_gaps_with_window_edges():
    s = 1_000_000_000
    g = heartbeat_gaps([2 * s, 2 * s + s // 2, 5 * s], window_start_ns=s, window_end_ns=6 * s)
    assert g["max_gap_ms"] == 2500.0 and g["over_count"] == 1 and g["over_at_s"] == [1.5]


def test_heartbeat_gaps_no_receive():
    g = heartbeat_gaps([], 0, 3_000_000_000)
    assert (g["max_gap_ms"], g["over_count"]) == (3000.0, 1)


def test_heartbeat_gaps_ignores_receipts_outside_window():
    s = 1_000_000_000
    g = heartbeat_gaps([0, s // 2, 2 * s, 9 * s], window_start_ns=s, window_end_ns=3 * s)
    assert g == {"max_gap_ms": 1000.0, "over_count": 0, "over_at_s": []}


def test_l04_risk():
    ok = {"max_gap_ms": 600.0, "over_count": 0, "over_at_s": []}
    bad = {"max_gap_ms": 1800.0, "over_count": 1, "over_at_s": [42.0]}
    assert l04_risk({"pinky1": ok}) == []
    assert l04_risk({"pinky1": bad})[0].startswith("pinky1 heartbeat 1회 1.5초 초과")
    assert "42.0초" in l04_risk({"pinky1": bad})[0]


def test_l04_is_standard():
    assert l04_is_standard(120.0) and not l04_is_standard(15.0)
