#!/usr/bin/env python3
"""Stage 2 preflight: checks the PC <-> Raspberry Pi setup before the evening batch.

Order: SSH -> remote ROS_DISTRO -> sync -> cleanup -> two-way 3 s communication check
(g11_node.py, control settings) -> gst-launch-1.0 / x264enc. Writes `preflight.json`,
`doctor.txt` (PC) and `doctor_pi.txt` (`ros2 doctor --report` on the Pi) into the run folder.
Exit code 3 when a required check fails.
"""
import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_g11  # noqa: E402
from r2g import proc, remote, results  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

REQUIRED = ("ssh", "pi_ros_jazzy", "sync", "pc_to_pi", "pi_to_pc")
FAIL_REASONS = {
    "ssh": "SSH 접속 실패(PI_HOST, SSH 키, 같은 공유기 확인)",
    "pi_ros_jazzy": "라즈베리파이에서 ROS_DISTRO가 jazzy가 아님(/opt/ros/jazzy 확인)",
    "sync": "라즈베리파이로 시험 도구 복사(rsync) 실패",
    "pc_to_pi": "PC → 라즈베리파이 수신 0건(라즈베리파이 방화벽·같은 공유기·ROS_DOMAIN_ID 확인)",
    "pi_to_pc": "라즈베리파이 → PC 수신 0건(PC 방화벽 확인)",
}
GST_WARNING = "gst: gst-launch-1.0 또는 x264enc 없음(L-04 영상 동시 조합은 사유를 남기고 진행)"
TWO_WAY_SECONDS = 3.0
LOCAL_SKIP = "PI_HOST=local: 생략"
SHELL_TIMEOUT_S = 30.0
DOCTOR_TIMEOUT_S = 90.0


def preflight_verdict(checks: dict[str, bool | None]) -> tuple[bool, list[str]]:
    """(proceed, reasons). Required checks must all be True; None (unchecked) is a failure."""
    reasons: list[str] = []
    for key in REQUIRED:
        value = checks.get(key)
        if value is True:
            continue
        if value is None:
            reasons.append(f"{key}: 확인하지 못함(앞 단계 실패 또는 미실행)")
        else:
            reasons.append(f"{key}: {FAIL_REASONS[key]}")
    ok = not reasons
    if checks.get("gst") is not True:
        reasons.append(GST_WARNING)
    return ok, reasons


def write_preflight(run_dir: Path, record: dict) -> Path:
    """Write preflight.json; an earlier one (resume) is kept under a timestamped name."""
    run_dir = Path(run_dir)
    path = run_dir / "preflight.json"
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(path.stat().st_mtime))
        old = run_dir / f"preflight.{stamp}.json"
        n = 1
        while old.exists():
            n += 1
            old = run_dir / f"preflight.{stamp}-{n}.json"
        path.rename(old)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def doctor_pi_cmd(env: dict[str, str]) -> str:
    return ("source /opt/ros/jazzy/setup.bash && "
            f"ROS_DOMAIN_ID={shlex.quote(env['ROS_DOMAIN_ID'])} "
            f"ROS_AUTOMATIC_DISCOVERY_RANGE={shlex.quote(env['ROS_AUTOMATIC_DISCOVERY_RANGE'])} "
            "ros2 doctor --report 2>&1")  # fmt: skip


GST_CHECK_CMD = "command -v gst-launch-1.0 >/dev/null && gst-inspect-1.0 x264enc >/dev/null 2>&1"


def run_checks(pi_host: str, env: dict[str, str], pc_launcher, pi_launcher,
               run_dir: Path) -> tuple[dict[str, bool | None], dict[str, str]]:
    local = pi_host == "local"
    checks: dict[str, bool | None] = {k: None for k in (*REQUIRED, "gst")}
    detail: dict[str, str] = {}

    if local:
        checks["ssh"] = True
        detail["ssh"] = LOCAL_SKIP
    else:
        r = pi_launcher.shell("echo r2g-ok", SHELL_TIMEOUT_S)
        checks["ssh"] = r.rc == 0 and "r2g-ok" in r.stdout
        detail["ssh"] = f"rc={r.rc}" + (" 시간 초과" if r.timed_out else "")
    if not checks["ssh"]:
        return checks, detail

    info = remote.pi_env(pi_launcher)
    checks["pi_ros_jazzy"] = info["ROS_DISTRO"] == "jazzy"
    detail["pi_ros_jazzy"] = f"ROS_DISTRO={info['ROS_DISTRO'] or '없음'} host={info['host']}"
    if not checks["pi_ros_jazzy"]:
        return checks, detail

    if local:
        checks["sync"] = True
        detail["sync"] = LOCAL_SKIP
    else:
        r = remote.sync(pi_host, REPO_ROOT)
        checks["sync"] = r.rc == 0 and not r.timed_out
        detail["sync"] = f"rc={r.rc}" + (" 시간 초과" if r.timed_out else "")
    if not checks["sync"]:
        return checks, detail

    pi_launcher.cleanup()
    pc_launcher.cleanup()

    case_env = {"ROS_DOMAIN_ID": env["ROS_DOMAIN_ID"],
                "ROS_AUTOMATIC_DISCOVERY_RANGE": env["ROS_AUTOMATIC_DISCOVERY_RANGE"]}
    case = {"name": run_g11.CONTROL, "pc": case_env, "pi": dict(case_env)}
    for key, direction in (("pc_to_pi", run_g11.DIRECTIONS[0]),
                           ("pi_to_pc", run_g11.DIRECTIONS[1])):
        rec = run_g11.run_direction(case, direction, TWO_WAY_SECONDS, pc_launcher, pi_launcher)
        received = rec.get("received")
        checks[key] = isinstance(received, int) and received > 0
        detail[key] = f"received={received} sent={rec.get('sent')} reason={rec.get('reason')}"

    r = pi_launcher.shell(GST_CHECK_CMD, SHELL_TIMEOUT_S)
    checks["gst"] = r.rc == 0 and not r.timed_out
    detail["gst"] = f"rc={r.rc}"
    return checks, detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi-host", default="local")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--cleanup-only", action="store_true",
                    help="양쪽 시험 프로세스만 정리하고 끝냄")
    args = ap.parse_args(argv)

    two_devices = args.pi_host != "local"
    proc.pin_discovery(os.environ, "SUBNET" if two_devices else "LOCALHOST")
    env = proc.child_env(os.environ)
    pc_launcher = remote.LocalLauncher(env)
    pi_launcher = remote.make_launcher(args.pi_host, env)
    if args.cleanup_only:
        pi_launcher.cleanup()
        pc_launcher.cleanup()
        return 0

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = results.new_run_dir(Path(args.out_root), run_id)
    started_at = results.now_iso()
    results.save_doctor(run_dir)
    checks: dict[str, bool | None] = {}
    detail: dict[str, str] = {}
    try:
        checks, detail = run_checks(args.pi_host, env, pc_launcher, pi_launcher, run_dir)
        if checks.get("ssh"):
            doc = pi_launcher.shell(doctor_pi_cmd(env), DOCTOR_TIMEOUT_S)
            (run_dir / "doctor_pi.txt").write_text(doc.stdout, encoding="utf-8")
    finally:
        pi_launcher.cleanup()
        pc_launcher.cleanup()

    ok, reasons = preflight_verdict(checks)
    write_preflight(run_dir, {
        "run_id": run_id, "pi_host": args.pi_host, "devices": "2대" if two_devices else "1대",
        "started_at": started_at, "written_at": results.now_iso(),
        "env": results.env_info(env), "checks": checks, "detail": detail,
        "proceed": ok, "reasons": reasons,
    })
    for key, value in checks.items():
        mark = {True: "OK", False: "실패", None: "미확인"}[value]
        print(f"사전 점검 {key}: {mark} ({detail.get(key, '')})", flush=True)
    for line in reasons:
        print(("경고: " if ok else "중단 사유: ") + line, file=sys.stderr, flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
