"""손실과 신선도 판정·집계. 순수 함수만 둔다."""
import math
from typing import Iterable

RULE_VARIANTS_L01 = frozenset({"B", "C", "E-TL", "S"})
PASS = "통과"
FAIL = "실패"
MEASURE = "측정"


def tally(sent: int, received_trials: Iterable[int]) -> dict:
    got = {t for t in received_trials if 0 <= t < sent}
    lost_trials = sorted(set(range(sent)) - got)
    lost = len(lost_trials)
    return {
        "sent": sent,
        "received": len(got),
        "lost": lost,
        "loss_rate": round(lost / sent, 4) if sent else 0.0,
        "lost_trials": lost_trials,
    }


def judge_l01(variant: str, t: dict, expected: int) -> str:
    if variant not in RULE_VARIANTS_L01:
        return MEASURE
    return PASS if t["sent"] == expected and t["lost"] == 0 else FAIL


def first_receive(pub_start_ns: int, rx: list[tuple[int, int]]) -> dict | None:
    if not rx:
        return None
    seq, rx_ns = min(rx, key=lambda r: r[1])
    return {"first_seq": seq, "time_to_first_ms": round((rx_ns - pub_start_ns) / 1e6, 1)}


def staleness_stats(samples_ms: list[float]) -> dict:
    n = len(samples_ms)
    if n == 0:
        return {"count": 0, "p50": 0.0, "p99": 0.0, "max": 0.0, "trend_ms": 0.0}
    s = sorted(samples_ms)
    k = n // 10
    trend = 0.0
    if n >= 10:
        head = sum(samples_ms[:k]) / k
        tail = sum(samples_ms[-k:]) / k
        trend = tail - head
    return {
        "count": n,
        "p50": round(s[math.ceil(0.5 * n) - 1], 1),
        "p99": round(s[math.ceil(0.99 * n) - 1], 1),
        "max": round(s[-1], 1),
        "trend_ms": round(trend, 1),
    }


def judge_l03_rule(runs: list[dict], limit_ms: float = 150.0, expected_runs: int = 5) -> str:
    ok = len(runs) == expected_runs and all(r["count"] > 0 and r["max"] <= limit_ms for r in runs)
    return PASS if ok else FAIL
