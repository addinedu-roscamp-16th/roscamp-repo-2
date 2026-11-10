import pytest

from r2g.judge import (
    first_receive,
    judge_l01,
    judge_l03_rule,
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
