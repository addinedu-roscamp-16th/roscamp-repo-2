#!/usr/bin/env python3
"""G-11 runner: PC <-> Raspberry Pi isolation (other domain, LOCALHOST on one side)."""
import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2g import config, judge, parts, proc, remote, results  # noqa: E402

HERE = Path(__file__).resolve().parent

TEST_ID = "G-11"
LOCAL_DEVICE_NOTE = "장비 1대(PI_HOST=local): 공식 판정 아님"
STANDARD_OBSERVE_S = 10
DIRECTIONS = ("PC→파이", "파이→PC")
CONTROL = "대조"
ISOLATION_CASES = ("도메인", "PC_LOCALHOST", "파이_LOCALHOST")
READY_TIMEOUT_S = 15.0
DAEMON_STOP_TIMEOUT_S = 30.0
# The subscriber ends by itself (its final JSON stays readable on stdout; a SIGINT-stopped
# remote ssh child would lose it), so it outlives the publisher by this margin.
SUB_EXTRA_S = 8.0
SUB_FINISH_MARGIN_S = 15.0
PUB_MARGIN_S = 20.0


def build_cases(base_domain: str, other_domain_id: int) -> list[dict]:
    base = str(base_domain)
    other = str(other_domain_id)

    def env(domain: str, rng: str) -> dict:
        return {"ROS_DOMAIN_ID": domain, "ROS_AUTOMATIC_DISCOVERY_RANGE": rng}

    return [
        {"name": CONTROL, "pc": env(base, "SUBNET"), "pi": env(base, "SUBNET")},
        {"name": "도메인", "pc": env(base, "SUBNET"), "pi": env(other, "SUBNET")},
        {"name": "PC_LOCALHOST", "pc": env(base, "LOCALHOST"), "pi": env(base, "SUBNET")},
        {"name": "파이_LOCALHOST", "pc": env(base, "SUBNET"), "pi": env(base, "LOCALHOST")},
    ]


CASES = build_cases("87", 88)


def case_key(case: str, direction: str) -> str:
    return f"{case}_{direction}"


def topic_name(case: str, direction: str) -> str:
    d = "pc_to_pi" if direction == DIRECTIONS[0] else "pi_to_pc"
    return f"/r2g/g11/{CASES_ASCII.get(case, case)}_{d}"


CASES_ASCII = {"대조": "control", "도메인": "domain", "PC_LOCALHOST": "pc_localhost",
               "파이_LOCALHOST": "pi_localhost"}  # fmt: skip


def daemon_stop_cmd(env: dict) -> str:
    return ("source /opt/ros/jazzy/setup.bash && "
            f"ROS_DOMAIN_ID={shlex.quote(env['ROS_DOMAIN_ID'])} "
            f"ROS_AUTOMATIC_DISCOVERY_RANGE={shlex.quote(env['ROS_AUTOMATIC_DISCOVERY_RANGE'])} "
            "ros2 daemon stop")  # fmt: skip


def parse_result(stdout: str) -> dict | None:
    """Last JSON line of g11_node: {sent|received, peer_seen_max}, or None."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            return None
        peer = obj.get("peer_seen_max")
        if not isinstance(peer, int):
            return None
        if isinstance(obj.get("received"), int):
            return {"received": obj["received"], "peer_seen_max": peer}
        if isinstance(obj.get("sent"), int):
            return {"sent": obj["sent"], "peer_seen_max": peer}
        return None
    return None


def _count(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def judge_g11(res: dict[str, dict[str, dict]]) -> tuple[str, list[str]]:
    """results[case][direction] = {"received": int | None, "peer_seen_max": int | None}.

    Control must receive in both directions. Isolation = zero reception both ways; a missing
    count (None) can neither prove nor disprove it, but any reception is a failure.
    Graph discovery (peer_seen_max) is only noted, never judged (review P2).
    """
    notes: list[str] = []
    for case in (CONTROL, *ISOLATION_CASES):
        for d in DIRECTIONS:
            peer = _count((res.get(case, {}).get(d) or {}).get("peer_seen_max"))
            r = _count((res.get(case, {}).get(d) or {}).get("received"))
            if r == 0 and peer:
                notes.append(f"그래프에 상대 보임: {case} {d}")
                v = res[case][d]
                if v.get("sub_saw_publishers_max"):
                    notes.append(f"그래프에 상대 보임(받는 쪽이 발행자를 봄): {case} {d}")
                if v.get("pub_saw_subscribers_max"):
                    notes.append(f"그래프에 상대 보임(보내는 쪽이 구독자를 봄): {case} {d}")
    bad_control = [d for d in DIRECTIONS
                   if not (_count((res.get(CONTROL, {}).get(d) or {}).get("received")) or 0) > 0]
    if bad_control:
        return judge.UNJUDGEABLE, [f"대조에서 통신 안 됨: {d}" for d in bad_control] + notes
    got: list[str] = []
    missing: list[str] = []
    for case in ISOLATION_CASES:
        for d in DIRECTIONS:
            r = _count((res.get(case, {}).get(d) or {}).get("received"))
            if r is None:
                missing.append(f"{case} {d}")
            elif r > 0:
                got.append(f"{case} {d}")
    if got:
        return judge.FAIL, [f"격리 사례에서 수신됨: {x}" for x in got] + notes
    if missing:
        return judge.UNJUDGEABLE, [f"수신 수 없음(격리 판단 불가): {x}" for x in missing] + notes
    return judge.PASS, notes


def build_record(direction: str, topic: str, sub_res: dict | None, pub_res: dict | None,
                 reason: str | None) -> dict:
    """Record of one direction. `received` is kept whenever the subscriber result parsed,
    even if the publisher failed: a real reception in an isolation case must stay visible."""
    sub_saw = sub_res["peer_seen_max"] if sub_res else None
    pub_saw = pub_res["peer_seen_max"] if pub_res else None
    seen = [v for v in (sub_saw, pub_saw) if v is not None]
    return {
        "direction": direction, "topic": topic, "ok": reason is None, "reason": reason,
        "received": sub_res["received"] if sub_res else None,
        "sent": pub_res.get("sent") if pub_res else None,
        "sub_saw_publishers_max": sub_saw, "pub_saw_subscribers_max": pub_saw,
        "peer_seen_max": max(seen) if seen else None,
    }


def run_direction(case: dict, direction: str, observe: float, pc_launcher, pi_launcher) -> dict:
    """Subscriber first (READY), publisher for `observe` s, then the subscriber's last line."""
    topic = topic_name(case["name"], direction)
    pc_to_pi = direction == DIRECTIONS[0]
    pub_l, pub_over = (pc_launcher, case["pc"]) if pc_to_pi else (pi_launcher, case["pi"])
    sub_l, sub_over = (pi_launcher, case["pi"]) if pc_to_pi else (pc_launcher, case["pc"])
    sub_s = observe + SUB_EXTRA_S
    sub = None
    reason = None
    sub_res = pub_res = None
    try:
        sub = sub_l.start("g11_node.py",
                          ["--role", "sub", "--topic", topic, "--seconds", str(sub_s)], sub_over)
        if not proc.wait_ready(sub, READY_TIMEOUT_S):
            reason = "받는 노드가 READY를 내지 않음"
        else:
            pub_args = ["--role", "pub", "--topic", topic, "--seconds", str(observe)]
            pub = pub_l.run("g11_node.py", pub_args, observe + PUB_MARGIN_S, pub_over)
            pub_res = parse_result(pub.stdout)
            if pub.timed_out:
                reason = "보내는 노드 시간 초과"
            elif pub_res is None:
                reason = "보내는 노드 결과 줄 없음"
            done = proc.finish_child(sub, sub_s + SUB_FINISH_MARGIN_S)
            sub = None
            sub_res = parse_result(done.stdout)
            if sub_res is None and reason is None:
                reason = "받는 노드 결과 줄 없음" + ("(시간 초과)" if done.timed_out else "")
    finally:
        if sub is not None:
            sub_l.stop(sub)
    return build_record(direction, topic, sub_res, pub_res, reason)


def stop_daemons(case: dict, pc_launcher, pi_launcher) -> list[str]:
    """`ros2 daemon stop` on both sides with the case's env; failures only become notes."""
    notes = []
    for side, launcher in (("PC", pc_launcher), ("파이", pi_launcher)):
        env = case["pc"] if side == "PC" else case["pi"]
        try:
            r = launcher.shell(daemon_stop_cmd(env), DAEMON_STOP_TIMEOUT_S)
            if r.timed_out or r.rc != 0:
                notes.append(f"{side} ros2 daemon stop 실패(rc={r.rc})")
        except OSError as exc:
            notes.append(f"{side} ros2 daemon stop 실패({exc!r})")
    return notes


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()["g11"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi-host", default="local",
                    help="상대 라즈베리파이(기본 local = 이 PC 한 대, 공식 판정 아님)")
    ap.add_argument("--observe", type=float, default=float(cfg["observe_s"]))
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--resume", action="store_true", help="끝난 방향을 건너뛰고 이어서 실행")
    args = ap.parse_args(argv)

    two_devices = args.pi_host != "local"
    env = proc.child_env(os.environ)  # base domain = ROS_DOMAIN_ID (default 87)
    cases = build_cases(env["ROS_DOMAIN_ID"], cfg["other_domain_id"])
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
    pc_launcher = remote.LocalLauncher(env)
    pi_launcher = remote.make_launcher(args.pi_host, env)
    started_at = results.now_iso()
    try:
        pi_info = remote.pi_env(pi_launcher)
        for case in cases:
            for direction in DIRECTIONS:
                key = case_key(case["name"], direction)
                if key in done:
                    print(f"{TEST_ID} {key}: 건너뜀(이미 끝남)", flush=True)
                    continue
                dnotes = stop_daemons(case, pc_launcher, pi_launcher)
                rec = run_direction(case, direction, args.observe, pc_launcher, pi_launcher)
                rec["case"] = case["name"]
                rec["pc_env"] = case["pc"]
                rec["pi_env"] = case["pi"]
                rec["notes"] = dnotes
                parts.save_part(run_dir, TEST_ID, key, rec)
                print(f"{TEST_ID} {key}: " + _line(rec), flush=True)
    finally:
        pc_launcher.cleanup()
        pi_launcher.cleanup()

    if (run_dir / f"{TEST_ID}.json").exists():
        return 0
    recs: dict[str, dict[str, dict]] = {}
    for case in cases:
        recs[case["name"]] = {}
        for direction in DIRECTIONS:
            part = parts.load_part(run_dir, TEST_ID, case_key(case["name"], direction))
            recs[case["name"]][direction] = part or {
                "received": None, "peer_seen_max": None, "ok": False, "reason": "실행 기록 없음"}
    verdict, notes = judge_g11(recs)
    for c, by_dir in recs.items():
        for d, v in by_dir.items():
            if not v.get("ok"):
                notes.append(f"{c} {d}: {v.get('reason')}")
            notes.extend(f"{c} {d}: {n}" for n in v.get("notes", []))
    standard = args.observe == STANDARD_OBSERVE_S
    verdict, size_note = judge.size_gate(
        verdict, True, standard, f"방향 {args.observe:g}초 관찰")  # fmt: skip
    if size_note:
        notes.append(size_note)
    if not two_devices:
        verdict = judge.UNJUDGEABLE
        notes.append(LOCAL_DEVICE_NOTE)
    conditions = {
        "observe_s": args.observe, "other_domain_id": cfg["other_domain_id"],
        "base_domain_id": env["ROS_DOMAIN_ID"],
        "cases": {c["name"]: {"pc": c["pc"], "pi": c["pi"]} for c in cases},
        "pi_host": args.pi_host, "devices": "2대" if two_devices else "1대",
        "pi_env": pi_info,
    }  # fmt: skip
    results.write_result(run_dir, TEST_ID, verdict, {"cases": recs}, conditions, notes,
                         env=env, started_at=started_at)  # fmt: skip
    print(f"{TEST_ID}: {verdict}", flush=True)
    return 0


def _line(v: dict) -> str:
    if not v.get("ok"):
        return f"데이터 없음 ({v.get('reason')})"
    return f"sent={v['sent']} received={v['received']} peer_seen_max={v['peer_seen_max']}"


if __name__ == "__main__":
    sys.exit(main())
