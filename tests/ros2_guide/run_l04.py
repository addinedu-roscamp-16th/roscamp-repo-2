#!/usr/bin/env python3
"""L-04 runner: integrated-operation load (Pi plays 6 robots, PC plays the fleet central).

Per combo (rate level x background): central on the PC, optional GStreamer video from the Pi
to the PC on the same Wi-Fi, then the six robot nodes on the Pi. Robot -> central gaps use only
PC receive times; heartbeat gaps use only Pi receive times and the Pi's window.
"""
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

TEST_ID = "L-04"
LOCAL_DEVICE_NOTE = "장비 1대(PI_HOST=local): 공식 판정 아님"
LEVELS = ("낮음", "중간", "높음")
BACKGROUNDS = ("ROS만", "영상 동시")
VIDEO_BG = "영상 동시"
COMBOS = [(level, background) for level in LEVELS for background in BACKGROUNDS]
ROBOTS = ("pinky1", "pinky2", "pinky3", "arm1", "arm2", "arm3")
SETTLE_S = 5.0
SMALL_BYTES = 128  # heartbeat, /fleet/robots, /central/events: 작성자 가정
READY_TIMEOUT_S = 15.0
ROBOTS_MARGIN_S = 30.0
CENTRAL_CAP_MARGIN_S = 120.0
GST_EARLY_EXIT_S = 2.0
VIDEO_MIN_RATIO = 0.1
VIDEO_PROBLEM = "영상 송신 확인 안 됨"


def combo_key(level: str, background: str) -> str:
    return f"{level}_{background}"


def gst_sender_cmd(video: dict, pc_addr: str) -> str:
    return (
        "gst-launch-1.0 -q videotestsrc is-live=true pattern=ball ! "
        f"video/x-raw,width={video['width']},height={video['height']},"
        f"framerate={video['fps']}/1 ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={video['bitrate_kbps']} ! "
        f"rtph264pay ! udpsink host={pc_addr} port={video['port']}"
    )


def parse_last_json(stdout: str) -> dict | None:
    """Last stdout line that is a JSON object (non-JSON lines after it are skipped)."""
    return lines.last_json_object(stdout)


def video_mbps(counter: dict | None) -> float | None:
    if counter is None:
        return None
    seconds = counter.get("seconds") or 0
    if seconds <= 0:
        return 0.0
    return round(counter.get("bytes", 0) * 8 / seconds / 1e6, 2)


def video_problem(early_exit: bool, mbps: float | None, bitrate_kbps: float) -> str | None:
    if early_exit or mbps is None or mbps < VIDEO_MIN_RATIO * bitrate_kbps / 1000:
        return VIDEO_PROBLEM
    return None


def pi_to_pc_stats(sent: int, seqs: list[int], recv_ns: list[int]) -> dict:
    """Robot -> central topic: robots' sent count, central's seqs and PC receive times."""
    return {**judge.loss_stats(sent, seqs), "max_gap_ms": judge.max_interval_ms(recv_ns)}


def pc_to_pi_stats(sent: int, seqs: list[int], max_gap_ms: float | None) -> dict:
    """Central -> robot topic, counted over [first received seq, last received seq].

    The central runs before the robots start and after they stop, so its full count also holds
    messages sent while no robot existed. The primary fields (sent, received, lost, loss_rate,
    dup, longest_gap) cover the received span only; sent_full / loss_rate_full keep the full
    count for reference. With nothing received the span falls back to the full count.
    """
    full = judge.loss_stats(sent, seqs)
    valid = sorted({s for s in seqs if 0 <= s < sent})
    if valid:
        first, last = valid[0], valid[-1]
        span = judge.loss_stats(last - first + 1, [s - first for s in seqs if 0 <= s < sent])
    else:
        first = last = None
        span = full
    return {**span, "first_seq": first, "last_seq": last, "sent_full": full["sent"],
            "loss_rate_full": full["loss_rate"], "max_gap_ms": max_gap_ms}  # fmt: skip


def topic_table(robots_out: dict, central_out: dict, hb: dict) -> dict:
    """{topic: {sent, received, loss_rate, max_gap_ms, ...}} for every L-04 topic."""
    r_sent = robots_out.get("sent", {})
    r_seqs = robots_out.get("seqs", {})
    c_sent = central_out.get("sent", {})
    c_seqs = central_out.get("seqs", {})
    c_recv = central_out.get("recv_ns", {})
    table = {}
    for topic in sorted(r_sent):
        table[topic] = pi_to_pc_stats(int(r_sent[topic]), c_seqs.get(topic, []),
                                      c_recv.get(topic, []))  # fmt: skip
    hb_sent = int(c_sent.get("/fleet/heartbeat", 0))
    for robot in ROBOTS:
        key = f"/fleet/heartbeat@{robot}"
        if key in r_seqs:
            gap = hb.get(robot, {}).get("max_gap_ms")
            table[key] = pc_to_pi_stats(hb_sent, r_seqs[key], gap)
    for topic in ("/central/events", "/fleet/robots"):
        if topic in r_seqs:
            table[topic] = pc_to_pi_stats(int(c_sent.get(topic, 0)), r_seqs[topic], None)
    return table


def heartbeat_table(robots_out: dict, limit_s: float) -> dict:
    """heartbeat_gaps per robot, on the Pi's receive times and the Pi's window only."""
    start, end = robots_out["window"]
    hb = robots_out.get("hb_recv_ns", {})
    return {robot: judge.heartbeat_gaps(hb.get(robot, []), start, end, limit_s)
            for robot in ROBOTS if robot in hb}  # fmt: skip


def robots_problem(out: dict | None, timed_out: bool, rc: int | None) -> str | None:
    if timed_out:
        return "로봇 노드 시간 초과"
    if out is None or not isinstance(out.get("sent"), dict):
        if rc not in (0, None):
            return f"로봇 노드 비정상 종료(rc={rc})"
        return "로봇 노드 결과 줄 없음"
    if not out.get("matched"):
        return "관제 매칭 실패"
    if rc != 0:
        return f"로봇 노드 비정상 종료(rc={rc})"
    return None


def heartbeat_skip(core_reason: str | None, robots_out: dict | None,
                   central_out: dict | None) -> str | None:  # fmt: skip
    """Why the heartbeat of a combo cannot be judged, or None when it can.

    Only robots that ran to completion (no robots/central failure, matched) with a real Pi
    window (end > start) give heartbeat data; a video problem alone does not block it.
    """
    if core_reason is not None:
        return core_reason
    if robots_out is None or not robots_out.get("matched"):
        return "로봇 노드 결과 없음"
    if central_out is None:
        return "관제 노드 out 파일 없음"
    w = robots_out.get("window")
    if not (isinstance(w, list) and len(w) == 2 and all(isinstance(x, int) for x in w)
            and w[1] > w[0]):  # fmt: skip
        return "라즈베리파이 창(window) 없음"
    if not isinstance(robots_out.get("hb_recv_ns"), dict) or not robots_out["hb_recv_ns"]:
        return "heartbeat 수신 기록 없음"
    return None


def overall_risk(recs: dict[str, dict]) -> bool | None:
    """True if any combo shows a risk; None if none does but a combo lacks heartbeat data."""
    if any(v.get("l04_risk") for v in recs.values()):
        return True
    if any(v.get("heartbeat") is None for v in recs.values()):
        return None
    return False


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _start_video(launcher, video: dict, env: dict, tmp: Path, key: str):
    """Start the PC counter and the Pi sender. Returns (counter, gst, counter_out, early, why)."""
    counter_out = tmp / f"{key}_udp.json"
    pc_addr = "127.0.0.1" if launcher.is_local else remote.pc_address_seen_by(launcher.host)
    if pc_addr is None:
        return None, None, counter_out, True, "PC 주소 확인 안 됨"
    counter = proc.start_child(
        [sys.executable, "-u", str(NODES / "udp_count.py"), "--port", str(video["port"]),
         "--out", str(counter_out)], env,
    )  # fmt: skip
    if not proc.wait_ready(counter, READY_TIMEOUT_S):
        return counter, None, counter_out, True, "영상 수신 카운터가 READY를 내지 않음"
    gst = launcher.start_command(gst_sender_cmd(video, pc_addr), {})
    time.sleep(GST_EARLY_EXIT_S)
    early = gst.poll() is not None
    return counter, gst, counter_out, early, None


def run_combo(level: str, background: str, cfg: dict, seconds: float, env: dict, launcher,
              pi_over: dict, tmp: Path) -> dict:  # fmt: skip
    key = combo_key(level, background)
    lv = cfg["levels"][level]
    central_out_path = tmp / f"{key}_central.json"
    wifi = remote.wifi_info(launcher)
    central_cmd = [
        sys.executable, "-u", str(NODES / "l04_central.py"),
        "--heartbeat-hz", str(cfg["heartbeat_hz"]), "--robots-hz", str(cfg["robots_hz"]),
        "--events-hz", str(cfg["events_hz"]), "--msg-bytes", str(SMALL_BYTES),
        "--seconds", str(seconds + SETTLE_S + CENTRAL_CAP_MARGIN_S),
        "--out", str(central_out_path),
    ]  # fmt: skip
    robots_args = [
        "--state-hz", str(lv["state_hz"]), "--vision-hz", str(lv["vision_hz"]),
        "--state-bytes", str(cfg["state_bytes"]), "--vision-bytes", str(cfg["vision_bytes"]),
        "--seconds", str(seconds), "--settle", str(SETTLE_S),
    ]  # fmt: skip
    central = counter = gst = None
    counter_out = None
    robots_out = None
    robots_tail = None
    video_early = False
    video_reason = None
    reason = None
    try:
        central = proc.start_child(central_cmd, env)
        if not proc.wait_ready(central, READY_TIMEOUT_S):
            reason = "관제 노드가 READY를 내지 않음"
        else:
            if background == VIDEO_BG:
                counter, gst, counter_out, video_early, video_reason = _start_video(
                    launcher, cfg["video"], env, tmp, key)  # fmt: skip
            res = launcher.run("l04_robots.py", robots_args,
                               seconds + SETTLE_S + ROBOTS_MARGIN_S, pi_over)  # fmt: skip
            robots_out = parse_last_json(res.stdout)
            reason = robots_problem(robots_out, res.timed_out, res.rc)
            if robots_out is None or res.rc != 0:
                robots_tail = lines.stdout_tail(res.stdout)
            if central.poll() is not None and reason is None:
                reason = f"관제 노드가 시험 중 종료됨(rc={central.poll()})"
    finally:
        if central is not None:
            proc.stop_child(central)
        if gst is not None:
            launcher.stop(gst)
        if counter is not None:
            proc.stop_child(counter)
    central_out = _read_json(central_out_path)
    if reason is None and central_out is None:
        reason = "관제 노드 out 파일 없음"
    skip = heartbeat_skip(reason, robots_out, central_out)
    mbps = None
    if background == VIDEO_BG:
        mbps = video_mbps(_read_json(counter_out)) if counter_out is not None else None
        problem = video_reason or video_problem(video_early, mbps, cfg["video"]["bitrate_kbps"])
        if reason is None and problem is not None:
            reason = VIDEO_PROBLEM if problem == VIDEO_PROBLEM else f"{VIDEO_PROBLEM}({problem})"
    rec = {
        "level": level, "background": background, "ok": reason is None, "reason": reason,
        "state_hz": lv["state_hz"], "vision_hz": lv["vision_hz"],
        "topics": {}, "heartbeat": None, "heartbeat_skip": skip, "l04_risk": [],
        "video_mbps": mbps,
        "video_early_exit": video_early if background == VIDEO_BG else None, "wifi": wifi,
    }  # fmt: skip
    if robots_tail is not None:
        rec["robots_stdout_tail"] = robots_tail
    if skip is None:
        hb = heartbeat_table(robots_out, cfg["heartbeat_stop_s"])
        rec["heartbeat"] = hb
        rec["l04_risk"] = judge.l04_risk(hb, cfg["heartbeat_stop_s"])
        start, end = robots_out["window"]
        rec["window_s"] = round((end - start) / 1e9, 1)
    if robots_out is not None and isinstance(robots_out.get("sent"), dict) and \
            robots_out.get("matched") and central_out is not None:
        rec["topics"] = topic_table(robots_out, central_out, rec["heartbeat"] or {})
    return rec


def size_desc(seconds: float) -> str:
    return f"조합 {seconds:g}초"


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()["l04"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi-host", default="local",
                    help="로봇 노드를 돌릴 라즈베리파이(기본 local = 이 PC 한 대, 공식 판정 아님)")
    ap.add_argument("--seconds", type=float, default=float(cfg["seconds"]))
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
    started_at = results.now_iso()
    try:
        pi_info = remote.pi_env(launcher)
        with tempfile.TemporaryDirectory(prefix="r2g_l04_") as td:
            for level, background in COMBOS:
                key = combo_key(level, background)
                if key in done:
                    print(f"{TEST_ID} {key}: 건너뜀(이미 끝남)", flush=True)
                    continue
                rec = run_combo(level, background, cfg, args.seconds, env, launcher, pi_over,
                                Path(td))  # fmt: skip
                parts.save_part(run_dir, TEST_ID, key, rec)
                print(f"{TEST_ID} {key}: " + _line(rec), flush=True)
    finally:
        launcher.cleanup()

    if (run_dir / f"{TEST_ID}.json").exists():
        return 0
    recs = {}
    for level, background in COMBOS:
        key = combo_key(level, background)
        recs[key] = parts.load_part(run_dir, TEST_ID, key) or {
            "ok": False, "reason": "실행 기록 없음"}
    notes = [f"{k}: {v['reason']}" for k, v in recs.items() if not v["ok"]]
    risk_notes = [f"통합 운영 위험: {k} {line}"
                  for k, v in recs.items() for line in v.get("l04_risk", [])]  # fmt: skip
    notes += risk_notes
    notes += [f"heartbeat 확인 못 함: {k} ({v.get('heartbeat_skip') or v.get('reason')})"
              for k, v in recs.items() if v.get("heartbeat") is None]  # fmt: skip
    _, size_note = judge.size_gate(judge.MEASURE, False, judge.l04_is_standard(args.seconds),
                                   size_desc(args.seconds))  # fmt: skip
    if size_note:
        notes.append(size_note)
    if not two_devices:
        notes.append(LOCAL_DEVICE_NOTE)
    conditions = {
        "seconds": args.seconds, "settle_s": SETTLE_S, "levels": cfg["levels"],
        "heartbeat_hz": cfg["heartbeat_hz"], "robots_hz": cfg["robots_hz"],
        "events_hz": cfg["events_hz"], "state_bytes": cfg["state_bytes"],
        "vision_bytes": cfg["vision_bytes"], "central_msg_bytes": SMALL_BYTES,
        "heartbeat_stop_s": cfg["heartbeat_stop_s"], "video": cfg["video"],
        "qos": "contract.yaml STATE/LATCHED/EVENT", "msg_type": "std_msgs/String",
        "pi_host": args.pi_host, "devices": "2대" if two_devices else "1대",
        "pi_env": pi_info,
        "interval_clock": ("로봇→관제 끊김은 PC의 time.monotonic_ns(), heartbeat 끊김은 "
                           "라즈베리파이의 time.monotonic_ns()와 그 장비의 창 "
                           "(두 장비 시계는 빼지 않음)"),
        "pc_to_pi_loss": ("관제→로봇 토픽의 sent·lost·loss_rate는 처음~마지막 받은 seq "
                          "구간만 셈. sent_full·loss_rate_full은 관제 발행 전체(로봇 시작 "
                          "전·종료 후 포함), 참고용"),
        "vision_loss_note": "LATCHED depth 1: 덮어쓴 샘플도 순번 빈자리로 셈",
    }  # fmt: skip
    observations = {"combos": recs, "risk": overall_risk(recs)}
    results.write_result(run_dir, TEST_ID, judge.MEASURE, observations, conditions, notes,
                         env=env, started_at=started_at)  # fmt: skip
    print(f"{TEST_ID}: 측정", flush=True)
    return 0


def _line(v: dict) -> str:
    hb = v.get("heartbeat") or {}
    worst = max((g["max_gap_ms"] for g in hb.values()), default=None)
    head = "ok" if v.get("ok") else f"실패({v.get('reason')})"
    return (f"{head} heartbeat_max_gap_ms={worst} video_mbps={v.get('video_mbps')} "
            f"risk={len(v.get('l04_risk', []))}")


if __name__ == "__main__":
    sys.exit(main())
