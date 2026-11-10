#!/usr/bin/env python3
"""L-03 runner: slow subscriber, staleness of processed messages vs depth."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2g import judge, proc, results  # noqa: E402

HERE = Path(__file__).resolve().parent
NODES = HERE / "nodes"

CONTROL_COMBOS: list[tuple[int, str]] = [
    (d, r) for d in (1, 5, 50) for r in ("best_effort", "reliable")
]
RULE = {"mode": "latest", "depth": 1, "reliability": "best_effort", "repeats": 5}

RATE_HZ = 50
WORK_MS = 100
READY_TIMEOUT_S = 15.0
STOP_AFTER_PUB_S = 1.0
SUB_CAP_MARGIN_S = 30.0
PUB_MARGIN_S = 20.0
UNJUDGEABLE = "판정 불가"


def topic_for(mode: str, depth: int, reliability: str, rep: int) -> str:
    short = "be" if reliability == "best_effort" else "rel"
    return f"/r2g/l03/{mode}_d{depth}_{short}_{rep}"


def sub_verdict(alive_through_run: bool, out_present: bool, judged: str) -> str:
    """Return `judged` only if the subscriber stayed up and wrote its out file."""
    return judged if alive_through_run and out_present else UNJUDGEABLE


def parse_sent(stdout: str) -> int | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            sent = obj.get("sent")
            return sent if isinstance(sent, int) else None
    return None


def combo_observation(sent: int | None, out: dict | None) -> dict | None:
    """sent/processed/dropped/stats, or None when either side produced no data."""
    if sent is None or out is None:
        return None
    samples = [float(x) for x in out.get("staleness_ms", [])]
    return {
        "sent": sent,
        "processed": len(samples),
        "dropped": sent - len(samples),
        "staleness_stats": judge.staleness_stats(samples),
        "bad": out.get("bad"),
    }


def rule_verdict(stats_runs: list[dict | None]) -> str:
    """Judge only when every repeat produced data."""
    if any(s is None for s in stats_runs):
        return UNJUDGEABLE
    return judge.judge_l03_rule(stats_runs)


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def run_one(mode: str, depth: int, reliability: str, rep: int, seconds: float,
            env: dict, tmp: Path) -> dict:  # fmt: skip
    """One subscriber + publisher run. Returns observation dict with `ok` and `reason`."""
    topic = topic_for(mode, depth, reliability, rep)
    out_path = tmp / (topic.strip("/").replace("/", "_") + ".json")
    common = ["--topic", topic, "--reliability", reliability, "--depth", str(depth)]
    sub_cmd = [
        sys.executable, str(NODES / "l03_sub.py"), *common, "--mode", mode,
        "--work-ms", str(WORK_MS), "--seconds", str(seconds + SUB_CAP_MARGIN_S),
        "--out", str(out_path),
    ]  # fmt: skip
    pub_cmd = [
        sys.executable, str(NODES / "l03_pub.py"), *common,
        "--rate", str(RATE_HZ), "--seconds", str(seconds),
    ]  # fmt: skip
    sub = None
    ready = False
    alive = False
    sent = None
    reason = None
    try:
        sub = proc.start_child(sub_cmd, env)
        ready = proc.wait_ready(sub, READY_TIMEOUT_S)
        if not ready:
            reason = "구독 노드가 READY를 내지 않음"
        else:
            pub = proc.run_child(pub_cmd, seconds + PUB_MARGIN_S, env)
            if pub.timed_out:
                reason = "발행 노드 타임아웃"
            elif pub.rc != 0:
                reason = f"발행 노드 비정상 종료(rc={pub.rc})"
            sent = parse_sent(pub.stdout)
            if reason is None and sent is None:
                reason = "발행 노드 결과 줄 없음"
            time.sleep(STOP_AFTER_PUB_S)
            alive = sub.poll() is None
            if not alive and reason is None:
                reason = f"구독 노드가 시험 중 종료됨(rc={sub.poll()})"
    finally:
        if sub is not None:
            proc.stop_child(sub)
    out = _read_json(out_path)
    obs = combo_observation(sent, out)
    if reason is None and obs is None:
        reason = "구독 노드 out 파일 없음"
    ok = sub_verdict(alive, obs is not None, "측정") == "측정" and reason is None
    obs = obs or {}
    obs.update({"ok": ok, "reason": reason, "sub_alive": alive, "topic": topic})
    return obs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--repeats", type=int, default=RULE["repeats"])
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)

    env = proc.child_env(os.environ)
    run_dir = results.new_run_dir(Path(args.out_root), args.run_id)
    results.save_doctor(run_dir)
    cond_common = {"rate_hz": RATE_HZ, "work_ms": WORK_MS, "seconds": args.seconds}

    with tempfile.TemporaryDirectory(prefix="r2g_l03_") as td:
        tmp = Path(td)
        combos = {}
        for depth, rel in CONTROL_COMBOS:
            combos[f"d{depth}_{rel}"] = {
                "depth": depth, "reliability": rel, "mode": "naive",
                **run_one("naive", depth, rel, 0, args.seconds, env, tmp),
            }  # fmt: skip
        notes = [f"{k}: {v['reason']}" for k, v in combos.items() if not v["ok"]]
        results.write_result(
            run_dir, "L-03-대조", "측정", {"combos": combos},
            {**cond_common, "mode": "naive"}, notes, env=env,
        )  # fmt: skip
        for k, v in combos.items():
            print(f"L-03-대조 {k}: " + _line(v), flush=True)

        reps = []
        for rep in range(args.repeats):
            reps.append(run_one(
                RULE["mode"], RULE["depth"], RULE["reliability"], rep, args.seconds, env, tmp
            ))  # fmt: skip
        stats = [r["staleness_stats"] if r["ok"] else None for r in reps]
        judged = rule_verdict(stats)
        rnotes = [f"반복 {i}: {r['reason']}" for i, r in enumerate(reps) if not r["ok"]]
        if any(s is None for s in stats):
            rnotes.append("모든 반복에서 데이터가 없어 판정 불가")
        elif args.repeats != RULE["repeats"]:
            rnotes.append(f"반복 {args.repeats}회 < {RULE['repeats']}회: 규칙 판정 실패가 정상")
        results.write_result(
            run_dir, "L-03-규칙", judged, {"repeats": reps},
            {**cond_common, **RULE, "repeats": args.repeats}, rnotes, env=env,
        )  # fmt: skip
        for i, r in enumerate(reps):
            print(f"L-03-규칙 rep{i}: " + _line(r), flush=True)
        print(f"L-03-규칙: {judged}", flush=True)
    return 0


def _line(v: dict) -> str:
    if not v.get("ok"):
        return f"판정 불가 ({v.get('reason')})"
    s = v["staleness_stats"]
    return (f"sent={v['sent']} processed={v['processed']} dropped={v['dropped']} "
            f"max={s['max']} trend_ms={s['trend_ms']}")  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
