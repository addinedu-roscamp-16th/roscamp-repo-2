"""손실과 신선도 판정·집계. 순수 함수만 둔다."""
import math
from typing import Iterable

RULE_VARIANTS_L01 = frozenset({"B", "C", "E-TL", "S"})
PASS = "통과"
FAIL = "실패"
MEASURE = "측정"
UNJUDGEABLE = "판정 불가"

# 계획서 3.1의 공식 크기. 이 크기가 아니면 규칙 변형은 판정하지 않는다.
STANDARD_TRIALS_L01 = 50
STANDARD_SECONDS_L03 = 20.0
STANDARD_REPEATS_L03 = 5
STANDARD_SECONDS_L02 = 30.0
STANDARD_REPEATS_L02 = 3
STANDARD_SECONDS_L04 = 120.0


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
        return {"count": 0, "p50": 0.0, "p99": 0.0, "max": 0.0, "max_exact": 0.0,
                "trend_ms": 0.0}
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
        "max_exact": s[-1],  # judged unrounded; "max" is for display
        "trend_ms": round(trend, 1),
    }


def judge_l03_rule(runs: list[dict], limit_ms: float = 150.0, expected_runs: int = 5) -> str:
    ok = len(runs) == expected_runs and all(
        r["count"] > 0 and r.get("max_exact", r["max"]) <= limit_ms for r in runs
    )
    return PASS if ok else FAIL


def split_live(
    samples: Iterable[Iterable[int]], last_publish_ns: int
) -> tuple[list[float], list[float]]:
    """Staleness (ms) of [stamp_ns, proc_ns] samples, split at the publisher's last publish.

    live: processed while the stream was still running (proc_ns <= last_publish_ns).
    tail: processed after the stream ended; the last message ages until the next tick.
    """
    live: list[float] = []
    tail: list[float] = []
    for stamp_ns, proc_ns in samples:
        ms = (proc_ns - stamp_ns) / 1e6
        (live if proc_ns <= last_publish_ns else tail).append(ms)
    return live, tail


def tail_summary(tail_ms: list[float]) -> dict:
    return {
        "tail_count": len(tail_ms),
        "tail_max_ms": round(max(tail_ms), 1) if tail_ms else None,
    }


def l01_is_standard(trials: int) -> bool:
    return trials == STANDARD_TRIALS_L01


def l03_is_standard(seconds: float, repeats: int) -> bool:
    return seconds == STANDARD_SECONDS_L03 and repeats == STANDARD_REPEATS_L03


def size_gate(judged: str, is_rule: bool, standard: bool, size_desc: str) -> tuple[str, str | None]:
    """Non-standard runs are dev runs: rule verdicts become 판정 불가, all get a note."""
    if standard:
        return judged, None
    note = f"개발 실행({size_desc}): 공식 판정 아님"
    return (UNJUDGEABLE if is_rule else judged), note


def l02_is_standard(seconds: float, repeats: int) -> bool:
    return seconds == STANDARD_SECONDS_L02 and repeats == STANDARD_REPEATS_L02


def loss_stats(sent: int, seqs: Iterable[int]) -> dict:
    """Loss of one stream; only seqs in [0, sent) count, others are ignored."""
    valid = [s for s in seqs if 0 <= s < sent]
    got = set(valid)
    longest = run = 0
    for i in range(sent):
        run = 0 if i in got else run + 1
        longest = max(longest, run)
    lost = sent - len(got)
    return {
        "sent": sent,
        "received": len(got),
        "lost": lost,
        "loss_rate": round(lost / sent, 4) if sent else None,
        "dup": len(valid) - len(got),
        "longest_gap": longest,
    }


def max_interval_ms(recv_ns: list[int]) -> float | None:
    """Largest gap between consecutive receive times taken on ONE device's clock."""
    if len(recv_ns) < 2:
        return None
    return round(max(b - a for a, b in zip(recv_ns, recv_ns[1:], strict=False)) / 1e6, 1)


def l04_is_standard(seconds: float) -> bool:
    return seconds == STANDARD_SECONDS_L04


def heartbeat_gaps(recv_ns: list[int], window_start_ns: int, window_end_ns: int,
                   limit_s: float = 1.5) -> dict:  # fmt: skip
    """Gaps between heartbeat receipts on ONE device, with the window edges as boundaries.

    Receipts outside [window_start_ns, window_end_ns] are ignored. No receipt at all makes the
    whole window one gap. over_at_s: start of each gap longer than limit_s, seconds from the
    window start (1 decimal).
    """
    inside = sorted(t for t in recv_ns if window_start_ns <= t <= window_end_ns)
    points = [window_start_ns, *inside, window_end_ns]
    limit_ns = limit_s * 1e9
    max_gap = 0
    over_at: list[float] = []
    for a, b in zip(points, points[1:], strict=False):
        gap = b - a
        max_gap = max(max_gap, gap)
        if gap > limit_ns:
            over_at.append(round((a - window_start_ns) / 1e9, 1))
    return {"max_gap_ms": round(max_gap / 1e6, 1), "over_count": len(over_at),
            "over_at_s": over_at}  # fmt: skip


def l04_risk(robots: dict[str, dict], limit_s: float = 1.5) -> list[str]:
    """One line per robot whose heartbeat stopped longer than limit_s at least once."""
    lines = []
    for robot, g in robots.items():
        if g.get("over_count", 0) > 0:
            at = ", ".join(f"{s:.1f}초" for s in g.get("over_at_s", []))
            lines.append(f"{robot} heartbeat {g['over_count']}회 {limit_s:g}초 초과"
                         f"(창 시작 후 {at})")  # fmt: skip
    return lines
