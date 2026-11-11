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
CONTROL_ID = "L-03-대조"
RULE_ID = "L-03-규칙"

RATE_HZ = 50
WORK_MS = 100
LIMIT_MS = 150.0
READY_TIMEOUT_S = 15.0
STOP_AFTER_PUB_S = 1.0
SUB_CAP_MARGIN_S = 30.0
PUB_MARGIN_S = 20.0
UNJUDGEABLE = "판정 불가"
LIVE_WINDOW = (
    "판정·통계는 처리 시각이 발행 노드의 마지막 발행 시각 이하인 처리(라이브 구간)만 씀. "
    "발행이 끝난 뒤의 처리는 tail_count, tail_max_ms로 따로 기록"
)


def topic_for(mode: str, depth: int, reliability: str, rep: int) -> str:
    short = "be" if reliability == "best_effort" else "rel"
    return f"/r2g/l03/{mode}_d{depth}_{short}_{rep}"


def target_files() -> list[str]:
    names = []
    for test_id in (CONTROL_ID, RULE_ID):
        names += [f"{test_id}.json", results.samples_name(test_id)]
    return names


def sub_verdict(alive_through_run: bool, out_present: bool, judged: str) -> str:
    """Return `judged` only if the subscriber stayed up and wrote its out file."""
    return judged if alive_through_run and out_present else UNJUDGEABLE


def parse_pub(stdout: str) -> dict | None:
    """Last JSON line of l03_pub: {sent, last_publish_ns, matched}, or None."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("sent"), int):
            return None
        last = obj.get("last_publish_ns")
        return {
            "sent": obj["sent"],
            "last_publish_ns": last if isinstance(last, int) else None,
            "matched": obj.get("matched") is True,
        }
    return None


def combo_observation(pub: dict | None, out: dict | None) -> dict | None:
    """Counts and live-window stats, or None when either side produced no data."""
    if pub is None or out is None:
        return None
    samples = out.get("samples", [])
    last = pub["last_publish_ns"]
    if last is None:
        live, tail = [], [(p - s) / 1e6 for s, p in samples]
    else:
        live, tail = judge.split_live(samples, last)
    return {
        "sent": pub["sent"],
        "matched": pub["matched"],
        "last_publish_ns": last,
        "processed": len(samples),
        "processed_live": len(live),
        "dropped": pub["sent"] - len(samples),
        "staleness_stats": judge.staleness_stats(live),
        **judge.tail_summary(tail),
        "bad": out.get("bad"),
    }


def data_problem(obs: dict) -> str | None:
    """A run with no matched subscriber or no processed message is not data."""
    if not obs["matched"]:
        return "구독자 매칭 실패"
    if obs["processed"] == 0:
        return "처리한 메시지 없음"
    if obs["processed_live"] == 0:
        return "처리한 메시지 없음(라이브 구간)"
    return None


def rule_verdict(stats_runs: list[dict | None]) -> str:
    """Judge only when every repeat produced data."""
    if any(s is None for s in stats_runs):
        return UNJUDGEABLE
    return judge.judge_l03_rule(stats_runs, LIMIT_MS, RULE["repeats"])


def gated_rule_verdict(
    stats_runs: list[dict | None], seconds: float, repeats: int
) -> tuple[str, str | None]:
    return judge.size_gate(
        rule_verdict(stats_runs), True, judge.l03_is_standard(seconds, repeats),
        size_desc(seconds, repeats),
    )  # fmt: skip


def size_desc(seconds: float, repeats: int) -> str:
    return f"조합 {seconds:g}초, 반복 {repeats}회"


def rule_notes(reps: list[dict]) -> list[str]:
    notes = [f"반복 {i}: {r['reason']}" for i, r in enumerate(reps) if not r["ok"]]
    if notes:
        notes.append("일부 반복에 데이터가 없어 판정 불가")
    return notes


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def run_one(mode: str, depth: int, reliability: str, rep: int, seconds: float,
            env: dict, tmp: Path) -> tuple[dict, dict]:  # fmt: skip
    """One subscriber + publisher run. Returns (observation with `ok`/`reason`, raw samples)."""
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
    alive = False
    pub_info = None
    reason = None
    try:
        sub = proc.start_child(sub_cmd, env)
        if not proc.wait_ready(sub, READY_TIMEOUT_S):
            reason = "구독 노드가 READY를 내지 않음"
        else:
            pub = proc.run_child(pub_cmd, seconds + PUB_MARGIN_S, env)
            if pub.timed_out:
                reason = "발행 노드 타임아웃"
            elif pub.rc != 0:
                reason = f"발행 노드 비정상 종료(rc={pub.rc})"
            pub_info = parse_pub(pub.stdout)
            if reason is None and pub_info is None:
                reason = "발행 노드 결과 줄 없음"
            time.sleep(STOP_AFTER_PUB_S)
            alive = sub.poll() is None
            if not alive and reason is None:
                reason = f"구독 노드가 시험 중 종료됨(rc={sub.poll()})"
    finally:
        if sub is not None:
            proc.stop_child(sub)
    out = _read_json(out_path)
    obs = combo_observation(pub_info, out)
    if reason is None and obs is None:
        reason = "구독 노드 out 파일 없음"
    if reason is None and obs is not None:
        reason = data_problem(obs)
    ok = sub_verdict(alive, obs is not None, "측정") == "측정" and reason is None
    raw = {
        "topic": topic,
        "last_publish_ns": (pub_info or {}).get("last_publish_ns"),
        "samples": (out or {}).get("samples", []),
    }
    obs = obs or {}
    obs.update({"ok": ok, "reason": reason, "sub_alive": alive, "topic": topic})
    return obs, raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=judge.STANDARD_SECONDS_L03)
    ap.add_argument("--repeats", type=int, default=RULE["repeats"])
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)

    env = proc.child_env(os.environ)
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    clash = results.existing_targets(Path(args.out_root) / run_id, target_files())
    if clash:
        print(f"실행 ID {run_id!r}에 이미 결과가 있습니다: {', '.join(clash)}. "
              "다른 --run-id를 쓰세요.", file=sys.stderr)  # fmt: skip
        return 2
    run_dir = results.new_run_dir(Path(args.out_root), run_id)
    results.save_doctor(run_dir)
    standard = judge.l03_is_standard(args.seconds, args.repeats)
    dev_note = None if standard else (
        f"개발 실행({size_desc(args.seconds, args.repeats)}): 공식 판정 아님"
    )
    cond_common = {
        "rate_hz": RATE_HZ, "work_ms": WORK_MS, "seconds": args.seconds,
        "live_window": LIVE_WINDOW,
    }  # fmt: skip

    with tempfile.TemporaryDirectory(prefix="r2g_l03_") as td:
        tmp = Path(td)
        started_at = results.now_iso()
        combos, raw = {}, {}
        for depth, rel in CONTROL_COMBOS:
            key = f"d{depth}_{rel}"
            obs, raw[key] = run_one("naive", depth, rel, 0, args.seconds, env, tmp)
            combos[key] = {"depth": depth, "reliability": rel, "mode": "naive", **obs}
        notes = [f"{k}: {v['reason']}" for k, v in combos.items() if not v["ok"]]
        if dev_note:
            notes.append(dev_note)
        results.write_samples(run_dir, CONTROL_ID, raw)
        results.write_result(
            run_dir, CONTROL_ID, "측정",
            {"combos": combos, "samples_file": results.samples_name(CONTROL_ID)},
            {**cond_common, "mode": "naive"}, notes, env=env, started_at=started_at,
        )  # fmt: skip
        for k, v in combos.items():
            print(f"{CONTROL_ID} {k}: " + _line(v), flush=True)

        started_at = results.now_iso()
        reps, raw = [], {}
        for rep in range(args.repeats):
            obs, raw[f"rep{rep}"] = run_one(
                RULE["mode"], RULE["depth"], RULE["reliability"], rep, args.seconds, env, tmp
            )
            reps.append(obs)
        stats = [r["staleness_stats"] if r["ok"] else None for r in reps]
        judged, gate_note = gated_rule_verdict(stats, args.seconds, args.repeats)
        rnotes = rule_notes(reps)
        if gate_note:
            rnotes.append(gate_note)
        results.write_samples(run_dir, RULE_ID, raw)
        results.write_result(
            run_dir, RULE_ID, judged,
            {"repeats": reps, "samples_file": results.samples_name(RULE_ID)},
            {**cond_common, **RULE, "repeats": args.repeats, "limit_ms": LIMIT_MS},
            rnotes, env=env, started_at=started_at,
        )  # fmt: skip
        for i, r in enumerate(reps):
            print(f"{RULE_ID} rep{i}: " + _line(r), flush=True)
        print(f"{RULE_ID}: {judged}", flush=True)
    return 0


def _line(v: dict) -> str:
    if not v.get("ok"):
        return f"판정 불가 ({v.get('reason')})"
    s = v["staleness_stats"]
    return (f"sent={v['sent']} processed={v['processed']} live={v['processed_live']} "
            f"dropped={v['dropped']} live_max={s['max']} trend_ms={s['trend_ms']} "
            f"tail={v['tail_count']} tail_max={v['tail_max_ms']}")  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
