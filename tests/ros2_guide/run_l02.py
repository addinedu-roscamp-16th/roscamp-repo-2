#!/usr/bin/env python3
"""L-02 runner: periodic message loss over Wi-Fi (publisher on the Pi, subscriber on the PC)."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2g import config, judge, lines, parts, proc, remote, results  # noqa: E402

HERE = Path(__file__).resolve().parent
NODES = HERE / "nodes"

TEST_ID = "L-02"
LOCAL_DEVICE_NOTE = "장비 1대(PI_HOST=local): 공식 판정 아님"
READY_TIMEOUT_S = 15.0
MATCH_TIMEOUT_S = 10.0
PUB_MARGIN_S = 40.0
SUB_CAP_MARGIN_S = 60.0
STOP_AFTER_PUB_S = 2.0


def combo_key(reliability: str, size_name: str, rate: int, rep: int) -> str:
    return f"{reliability}_{size_name}_{rate}Hz_r{rep}"


def combos(cfg: dict, repeats: int) -> list[tuple[str, str, int, int, int]]:
    """(reliability, size_name, size, rate, rep), reliability -> size -> rate -> repeat."""
    return [
        (rel, name, size, rate, rep)
        for rel in cfg["reliabilities"]
        for name, size in cfg["sizes"].items()
        for rate in cfg["rates_hz"]
        for rep in range(repeats)
    ]


def expected_sent(seconds: float, rate: int) -> int:
    """Same rounding as l02_pub."""
    return int(round(seconds * rate))


def parse_pub(stdout: str) -> dict | None:
    """Last JSON object line of l02_pub: {sent, matched, elapsed_s, achieved_hz, error}."""
    obj = lines.last_json_object(stdout)
    if obj is None or not isinstance(obj.get("sent"), int):
        return None
    return {"sent": obj["sent"], "matched": obj.get("matched") is True,
            "elapsed_s": obj.get("elapsed_s"), "achieved_hz": obj.get("achieved_hz"),
            "error": obj.get("error")}  # fmt: skip


def l02_problem(pub: dict | None, timed_out: bool, expected: int) -> str | None:
    if timed_out:
        return "발행 노드 시간 초과"
    if pub is None:
        return "발행 노드 결과 줄 없음"
    if pub.get("error"):
        return f"발행 노드 오류({pub['error']})"
    if not pub["matched"]:
        return "구독자 매칭 실패"
    if pub["sent"] < expected:
        return f"발행 수 미달(실제 {pub['sent']} / 예정 {expected})"
    return None


def fill_counts(rec: dict, pub_info: dict | None, out: dict | None) -> dict:
    """Add the publisher's numbers and the loss stats to rec. Without a publisher result line
    sent is None (unknown, not 0) and every loss stat is None."""
    pub = pub_info or {}
    sent = pub_info["sent"] if pub_info else None
    rec.update({"elapsed_s": pub.get("elapsed_s"), "achieved_hz": pub.get("achieved_hz"),
                "error": pub.get("error")})  # fmt: skip
    if out is not None and sent is not None:
        rec.update(judge.loss_stats(sent, out.get("seqs", [])))
        rec["max_interval_ms"] = judge.max_interval_ms(out.get("recv_ns", []))
        rec["bad"] = out.get("bad")
    else:
        rec.update({"sent": sent, "received": None, "lost": None, "loss_rate": None,
                    "dup": None, "longest_gap": None, "max_interval_ms": None})  # fmt: skip
    return rec


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def run_combo(rel: str, size_name: str, size: int, rate: int, rep: int, seconds: float,
              env: dict, launcher, pi_over: dict, tmp: Path) -> dict:  # fmt: skip
    """One subscriber (PC) + publisher (Pi) run. Returns the combo record."""
    key = combo_key(rel, size_name, rate, rep)
    topic = f"/r2g/l02/{key}"
    out_path = tmp / f"{key}.json"
    expected = expected_sent(seconds, rate)
    wifi = remote.wifi_info(launcher)
    sub_cmd = [
        sys.executable, "-u", str(NODES / "l02_sub.py"), "--topic", topic,
        "--reliability", rel, "--seconds", str(seconds + SUB_CAP_MARGIN_S),
        "--out", str(out_path),
    ]  # fmt: skip
    pub_args = [
        "--topic", topic, "--reliability", rel, "--size", str(size), "--rate", str(rate),
        "--seconds", str(seconds), "--match-timeout", str(MATCH_TIMEOUT_S),
    ]  # fmt: skip
    sub = None
    pub_info = None
    pub_tail = None
    reason = None
    try:
        sub = proc.start_child(sub_cmd, env)
        if not proc.wait_ready(sub, READY_TIMEOUT_S):
            reason = "구독 노드가 READY를 내지 않음"
        else:
            pub = launcher.run("l02_pub.py", pub_args, seconds + PUB_MARGIN_S, pi_over)
            pub_info = parse_pub(pub.stdout)
            if pub_info is None or pub.rc != 0:
                pub_tail = lines.stdout_tail(pub.stdout)
            reason = l02_problem(pub_info, pub.timed_out, expected)
            if reason is None and pub.rc != 0:
                reason = f"발행 노드 비정상 종료(rc={pub.rc})"
            time.sleep(STOP_AFTER_PUB_S)
            if sub.poll() is not None and reason is None:
                reason = f"구독 노드가 시험 중 종료됨(rc={sub.poll()})"
    finally:
        if sub is not None:
            proc.stop_child(sub)
    out = _read_json(out_path)
    if reason is None and out is None:
        reason = "구독 노드 out 파일 없음"
    rec = {
        "reliability": rel, "size": size_name, "rate_hz": rate, "rep": rep,
        "ok": reason is None, "reason": reason, "topic": topic, "wifi": wifi,
        "expected": expected,
    }  # fmt: skip
    rec = fill_counts(rec, pub_info, out)
    if pub_tail is not None:
        rec["pub_stdout_tail"] = pub_tail
    return rec


def size_desc(seconds: float, repeats: int) -> str:
    return f"조합 {seconds:g}초, 반복 {repeats}회"


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()["l02"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi-host", default="local",
                    help="발행 노드를 돌릴 라즈베리파이(기본 local = 이 PC 한 대, 공식 판정 아님)")
    ap.add_argument("--seconds", type=float, default=float(cfg["seconds"]))
    ap.add_argument("--repeats", type=int, default=int(cfg["repeats"]))
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--resume", action="store_true",
                    help="끝난 조합을 건너뛰고 이어서 실행(실패한 조합은 다시 실행)")
    args = ap.parse_args(argv)

    two_devices = args.pi_host != "local"
    proc.pin_discovery(os.environ, "SUBNET" if two_devices else "LOCALHOST")
    env = proc.child_env(os.environ)
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_path = Path(args.out_root) / run_id
    done = parts.done_keys(run_path, TEST_ID)
    clash = results.existing_targets(run_path, [f"{TEST_ID}.json"])
    if (clash or done) and not args.resume:
        print(f"실행 ID {run_id!r}에 이미 결과가 있습니다: {', '.join(clash or sorted(done))}. "
              "다른 --run-id를 쓰세요.", file=sys.stderr)  # fmt: skip
        return 2
    run_dir = results.new_run_dir(Path(args.out_root), run_id)
    results.save_doctor(run_dir)
    if args.resume:
        for key in parts.retire_for_resume(run_dir, TEST_ID):
            print(f"{TEST_ID} {key}: 실패한 조합 다시 실행", flush=True)
        done = parts.done_keys(run_dir, TEST_ID)
    launcher = remote.make_launcher(args.pi_host, env)
    pi_over = {"ROS_AUTOMATIC_DISCOVERY_RANGE": env["ROS_AUTOMATIC_DISCOVERY_RANGE"],
               "ROS_DOMAIN_ID": env["ROS_DOMAIN_ID"]}  # fmt: skip
    all_combos = combos(cfg, args.repeats)
    started_at = results.now_iso()
    try:
        pi_info = remote.pi_env(launcher)
        with tempfile.TemporaryDirectory(prefix="r2g_l02_") as td:
            for rel, name, size, rate, rep in all_combos:
                key = combo_key(rel, name, rate, rep)
                if key in done:
                    print(f"{TEST_ID} {key}: 건너뜀(이미 끝남)", flush=True)
                    continue
                rec = run_combo(rel, name, size, rate, rep, args.seconds, env, launcher,
                                pi_over, Path(td))  # fmt: skip
                parts.save_part(run_dir, TEST_ID, key, rec)
                print(f"{TEST_ID} {key}: " + _line(rec), flush=True)
    finally:
        launcher.cleanup()

    if (run_dir / f"{TEST_ID}.json").exists():
        return 0
    recs = {}
    for rel, name, _size, rate, rep in all_combos:
        key = combo_key(rel, name, rate, rep)
        recs[key] = parts.load_part(run_dir, TEST_ID, key) or {
            "ok": False, "reason": "실행 기록 없음"}
    notes = [f"{k}: {v['reason']}" for k, v in recs.items() if not v["ok"]]
    _, size_note = judge.size_gate(
        judge.MEASURE, False, judge.l02_is_standard(args.seconds, args.repeats),
        size_desc(args.seconds, args.repeats),
    )  # fmt: skip
    if size_note:
        notes.append(size_note)
    if not two_devices:
        notes.append(LOCAL_DEVICE_NOTE)
    conditions = {
        "seconds": args.seconds, "repeats": args.repeats, "depth": cfg["depth"],
        "sizes": cfg["sizes"], "rates_hz": cfg["rates_hz"],
        "reliabilities": cfg["reliabilities"], "durability": "volatile",
        "pi_host": args.pi_host, "devices": "2대" if two_devices else "1대",
        "pi_env": pi_info,
        "interval_clock": "구독 PC의 time.monotonic_ns() (두 장비 시계는 빼지 않음)",
    }  # fmt: skip
    results.write_result(run_dir, TEST_ID, "측정", {"combos": recs}, conditions, notes,
                         env=env, started_at=started_at)  # fmt: skip
    print(f"{TEST_ID}: 측정", flush=True)
    return 0


def _line(v: dict) -> str:
    if not v.get("ok"):
        return f"데이터 없음 ({v.get('reason')})"
    return (f"sent={v['sent']} received={v['received']} lost={v['lost']} "
            f"loss_rate={v['loss_rate']} dup={v['dup']} gap={v['longest_gap']} "
            f"max_interval_ms={v['max_interval_ms']} achieved_hz={v.get('achieved_hz')}")


if __name__ == "__main__":
    sys.exit(main())
