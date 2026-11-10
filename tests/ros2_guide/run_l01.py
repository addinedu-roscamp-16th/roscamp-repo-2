#!/usr/bin/env python3
"""L-01 runner: one message published right after start, per variant."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2g import judge, proc, qos, results  # noqa: E402

HERE = Path(__file__).resolve().parent
NODES = HERE / "nodes"

VARIANTS: dict[str, dict] = {
    "A": {"profile": "EVENT", "mode": "once", "late_sub": False},
    "B": {"profile": "EVENT", "mode": "wait_match", "late_sub": False},
    "C": {"profile": "LATCHED", "mode": "once", "late_sub": False},
    "D": {"profile": "STATE", "mode": "once", "late_sub": False},
    "E-VOL": {"profile": "EVENT", "mode": "once", "late_sub": True},
    "E-TL": {"profile": "LATCHED", "mode": "once", "late_sub": True},
    "F": {"profile": "EVENT", "mode": "stream", "late_sub": False},
    "S": {"profile": None, "mode": "service", "late_sub": False},
}
IMPLEMENTED = ("A", "B", "C", "D", "F")

PUB_TIMEOUT = {"once": 15.0, "wait_match": 15.0, "stream": 15.0}
TRIAL_GAP_S = 0.5
SETTLE_S = 2.0
READY_TIMEOUT_S = 15.0
RATE_HZ = 10
STREAM_SECONDS = 3
WAIT_TIMEOUT_S = 5.0


def topic_for(variant: str) -> str:
    return "/r2g/l01/" + variant.lower().replace("-", "_")


def sub_verdict(alive_through_settle: bool, summary_present: bool, judged: str) -> str:
    """Return `judged` only if the subscriber stayed up and wrote its summary."""
    return judged if alive_through_settle and summary_present else "판정 불가"


def _parse_pub_line(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _read_rx(path: Path) -> dict[int, list[tuple[int, int]]]:
    rx: dict[int, list[tuple[int, int]]] = {}
    if not path.exists():
        return rx
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            rx.setdefault(int(d["trial"]), []).append((int(d["seq"]), int(d["rx_ns"])))
        except (ValueError, KeyError, TypeError):
            continue
    return rx


def _read_bad(path: Path) -> int | None:
    summary = path.with_name(path.name + ".summary.json")
    try:
        return int(json.loads(summary.read_text(encoding="utf-8"))["bad"])
    except (OSError, ValueError, KeyError):
        return None


def run_variant(variant: str, trials: int, env: dict, tmp: Path) -> tuple[dict, dict, list]:
    cfg = VARIANTS[variant]
    topic = topic_for(variant)
    mode = cfg["mode"]
    out_path = tmp / f"l01_{variant}.jsonl"
    sub = None
    pubs: dict[int, dict] = {}
    timed_out: list[int] = []
    failed: list[int] = []
    sub_ready = False
    sub_alive = True
    sub_rc = None
    try:
        sub = proc.start_child(
            [sys.executable, str(NODES / "l01_sub.py"), "--topic", topic,
             "--profile", cfg["profile"], "--out", str(out_path)],
            env,
        )  # fmt: skip
        sub_ready = proc.wait_ready(sub, READY_TIMEOUT_S)
        if sub_ready:
            for trial in range(trials):
                cmd = [
                    sys.executable, str(NODES / "l01_pub.py"), "--topic", topic,
                    "--profile", cfg["profile"], "--trial", str(trial), "--mode", mode,
                    "--wait-timeout", str(WAIT_TIMEOUT_S), "--rate", str(RATE_HZ),
                    "--stream-seconds", str(STREAM_SECONDS),
                ]  # fmt: skip
                r = proc.run_child(cmd, PUB_TIMEOUT[mode], env)
                info = _parse_pub_line(r.stdout)
                if r.timed_out:
                    timed_out.append(trial)
                elif info is None or r.rc != 0:
                    failed.append(trial)
                if info is not None:
                    pubs[trial] = info
                if sub.poll() is not None:
                    sub_alive = False
                time.sleep(TRIAL_GAP_S)
            time.sleep(SETTLE_S)
            if sub.poll() is not None:
                sub_alive = False
    finally:
        if sub is not None:
            sub_rc = sub.poll()
            proc.stop_child(sub)
    rx = _read_rx(out_path)
    bad = _read_bad(out_path)
    sub_alive = sub_alive and sub_ready
    excluded = set(timed_out) | set(failed)
    received = [t for t in rx if t not in excluded]
    t = judge.tally(trials, received)
    obs = dict(t)
    obs["sub_ready"] = sub_ready
    obs["bad_messages"] = bad
    obs["sub_alive"] = sub_alive
    obs["sub_exit_code"] = sub_rc
    obs["sub_summary_present"] = bad is not None
    obs["excluded_trials"] = sorted(excluded)
    obs["lost_trials_counted"] = [x for x in t["lost_trials"] if x not in excluded]
    obs["pub_timed_out_trials"] = timed_out
    obs["pub_failed_trials"] = failed
    if mode == "wait_match":
        obs["matched_trials"] = sum(1 for p in pubs.values() if p.get("matched"))
        obs["wait_ms"] = {str(k): p.get("wait_ms") for k, p in sorted(pubs.items())}
    if mode == "stream":
        firsts = {}
        for trial in range(trials):
            p = pubs.get(trial)
            fr = None
            if p is not None and trial not in excluded:
                fr = judge.first_receive(p["pub_start_ns"], rx.get(trial, []))
            firsts[str(trial)] = fr
        obs["first_receive"] = firsts
    cond = {
        "trials": trials,
        "profile": cfg["profile"],
        "qos": qos.load_profiles()[cfg["profile"]],
        "mode": mode,
        "topic": topic,
        "trial_gap_s": TRIAL_GAP_S,
        "settle_s": SETTLE_S,
    }
    if mode == "wait_match":
        cond["wait_timeout_s"] = WAIT_TIMEOUT_S
    if mode == "stream":
        cond["rate_hz"] = RATE_HZ
        cond["stream_seconds"] = STREAM_SECONDS
    return obs, cond, [] if sub_ready else ["구독 노드가 READY를 내지 않음"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)

    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        ap.error(f"unknown variants: {unknown}")

    env = proc.child_env(os.environ)
    run_dir = results.new_run_dir(Path(args.out_root), args.run_id)
    results.save_doctor(run_dir)
    with tempfile.TemporaryDirectory(prefix="r2g_l01_") as td:
        for v in names:
            test_id = f"L-01-{v}"
            if v not in IMPLEMENTED:
                results.write_result(
                    run_dir, test_id, "미실행", {}, {"trials": args.trials},
                    ["Task 4에서 구현"], env=env,
                )  # fmt: skip
                continue
            obs, cond, notes = run_variant(v, args.trials, env, Path(td))
            result = "판정 불가"
            if obs["sub_ready"]:
                result = sub_verdict(
                    obs["sub_alive"],
                    obs["sub_summary_present"],
                    judge.judge_l01(v, obs, args.trials),
                )
            if obs["sub_ready"] and result == "판정 불가":
                if not obs["sub_alive"]:
                    notes.append(f"구독 노드가 시험 중 종료됨(rc={obs['sub_exit_code']})")
                if not obs["sub_summary_present"]:
                    notes.append("구독 노드 summary 파일 없음")
            if result != "판정 불가" and v == "A" and obs["lost_trials_counted"]:
                notes.append("재현됨")
            results.write_result(run_dir, test_id, result, obs, cond, notes, env=env)
            print(f"{test_id}: {result} lost={obs['lost']}/{obs['sent']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
