#!/usr/bin/env python3
"""L-01 runner: one message published right after start, per variant."""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r2g import judge, parts, proc, qos, remote, results  # noqa: E402

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

W_ORDER = ["A", "B", "C", "F", "D", "E-VOL", "E-TL", "S"]
STAGE1_ID_PREFIX = "L-01"
LOCAL_DEVICE_NOTE = "장비 1대(PI_HOST=local): 공식 판정 아님"
CLOCK_NOTE = "2대 실행: 장비 간 시계를 비교하지 않아 첫 수신까지의 시간(ms)은 생략"
LATE_OFFSET_BASIS = "PC가 발행 노드 READY를 받은 시각 기준(하한)"

PUB_TIMEOUT = {"once": 15.0, "wait_match": 15.0, "stream": 15.0}
TRIAL_GAP_S = 0.5
SETTLE_S = 2.0
READY_TIMEOUT_S = 15.0
RATE_HZ = 10
STREAM_SECONDS = 3
WAIT_TIMEOUT_S = 5.0
LATE_SUB_DELAY_S = 1.0
MIN_LATE_OFFSET_MS = 500.0
LATE_PUB_LINGER_S = 4.0
LATE_SUB_DURATION_S = 3.0
LATE_SUB_TIMEOUT_S = 15.0
LATE_PUB_TIMEOUT_S = 15.0
CLIENT_TIMEOUT_S = 5.0
CLIENT_PROC_TIMEOUT_S = 15.0
NOTHING_SENT = "발행 노드가 보낸 메시지 없음"
MATCH_TIMEOUT = "구독자 매칭 시간 초과(발행 생략)"


def topic_for(variant: str, prefix: str = "l01") -> str:
    return f"/r2g/{prefix}/" + variant.lower().replace("-", "_")


def topic_prefix(id_prefix: str) -> str:
    """`L-01` -> `l01`, `L-01-W` -> `l01w`."""
    return id_prefix.lower().replace("-", "")


def target_files(variants: Iterable[str], id_prefix: str = STAGE1_ID_PREFIX) -> list[str]:
    return [f"{id_prefix}-{v}.json" for v in variants]


def device_gate(judged: str, variant: str, pi_host: str | None) -> tuple[str, str | None]:
    """One machine (PI_HOST=local) cannot give an official rule verdict."""
    if pi_host != "local":
        return judged, None
    return (
        judge.UNJUDGEABLE if variant in judge.RULE_VARIANTS_L01 else judged,
        LOCAL_DEVICE_NOTE,
    )


class PiSide:
    """Runs the Raspberry Pi side (publisher, service client).

    Without a launcher it spawns the same local commands stage 1 always did.
    """

    def __init__(self, env: dict, launcher: remote.Launcher | None = None,
                 pi_env: dict | None = None, prefix: str = "l01"):
        self.env = env
        self.launcher = launcher
        self.pi_env = pi_env or {}
        self.prefix = prefix

    @property
    def remote(self) -> bool:
        return self.launcher is not None and not self.launcher.is_local

    def _cmd(self, script: str, args: list[str]) -> list[str]:
        return [sys.executable, str(NODES / script), *args]

    def start(self, script: str, args: list[str]):
        if self.launcher is None:
            return proc.start_child(self._cmd(script, args), self.env)
        return self.launcher.start(script, args, self.pi_env)

    def run(self, script: str, args: list[str], timeout: float) -> proc.ChildResult:
        if self.launcher is None:
            return proc.run_child(self._cmd(script, args), timeout, self.env)
        return self.launcher.run(script, args, timeout, self.pi_env)

    def stop(self, p) -> None:
        if self.launcher is None:
            proc.stop_child(p)
        else:
            self.launcher.stop(p)


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


def late_offset_ms(sub_created_ns: int | None, publish_ns: int | None) -> float | None:
    if sub_created_ns is None or publish_ns is None:
        return None
    return round((sub_created_ns - publish_ns) / 1e6, 1)


def late_trial_exclusion(
    pub_ready: bool,
    pub_timed_out: bool,
    pub_rc: int | None,
    publish_ns: int | None,
    pub_sent: int | None,
    sub_timed_out: bool,
    sub_rc: int | None,
    sub_created_ns: int | None,
    offset_ms: float | None,
) -> str | None:
    """Reason a late-subscriber trial cannot be scored (None = countable)."""
    if not pub_ready:
        return "발행 노드가 publish 후 READY를 내지 않음"
    if pub_timed_out:
        return "발행 노드 타임아웃"
    if pub_rc != 0:
        return f"발행 노드 비정상 종료(rc={pub_rc})"
    if publish_ns is None:
        return "발행 노드 결과 파일 없음"
    if not pub_sent:
        return NOTHING_SENT
    if sub_timed_out:
        return "구독 노드 타임아웃"
    if sub_rc != 0:
        return f"구독 노드 비정상 종료(rc={sub_rc})"
    if sub_created_ns is None:
        return "구독 노드 summary 없음"
    if offset_ms is None or offset_ms < MIN_LATE_OFFSET_MS:
        return f"구독 생성 오프셋 {offset_ms}ms < {MIN_LATE_OFFSET_MS:g}ms (늦은 구독 아님)"
    return None


def pub_trial_exclusion(timed_out: bool, rc: int | None, info: dict | None) -> str | None:
    """Reason a publisher trial cannot be scored (None = countable).

    rc 0 alone is not enough: a publisher that sent nothing must not count as a loss.
    """
    if timed_out:
        return "발행 노드 타임아웃"
    if rc != 0:
        return f"발행 노드 비정상 종료(rc={rc})"
    if info is None:
        return "발행 노드 결과 줄 없음"
    if info.get("matched") is False:
        return MATCH_TIMEOUT
    if not info.get("sent") or info.get("publish_ns") is None:
        return NOTHING_SENT
    return None


def pub_sent_total(infos: Iterable[dict]) -> int:
    """Sum of the `sent` counts the publishers reported."""
    return sum(i["sent"] for i in infos if isinstance(i.get("sent"), int))


def gated_verdict(variant: str, judged: str, trials: int) -> tuple[str, str | None]:
    """Rule variants are judged only at the standard trial count."""
    return judge.size_gate(
        judged,
        variant in judge.RULE_VARIANTS_L01,
        judge.l01_is_standard(trials),
        f"시도 {trials}회",
    )


def offset_stats(offsets: list[float]) -> dict:
    if not offsets:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(offsets),
        "min": min(offsets),
        "median": statistics.median(offsets),
        "max": max(offsets),
    }


def remote_first_receive(rx: list[tuple[int, int]]) -> dict | None:
    """First received seq (ordered by this PC's rx clock only); no time across machines."""
    if not rx:
        return None
    seq, _ = min(rx, key=lambda r: r[1])
    return {"first_seq": seq, "time_to_first_ms": None}


def counted_tally(trials: int, received: Iterable[int], excluded: Iterable[int]) -> dict:
    """Tally over counted trials only (excluded ones leave the denominator)."""
    ex = set(excluded)
    counted = [t for t in range(trials) if t not in ex]
    got = {t for t in received if t in counted}
    lost_trials = [t for t in counted if t not in got]
    n = len(counted)
    return {
        "counted": n,
        "received": len(got),
        "lost": len(lost_trials),
        "loss_rate": round(len(lost_trials) / n, 4) if n else 0.0,
        "lost_trials": lost_trials,
    }


def l01_verdict(variant: str, t: dict, excluded_count: int) -> str:
    if t["counted"] == 0:
        return "판정 불가"
    if variant in judge.RULE_VARIANTS_L01 and excluded_count > 0:
        return "판정 불가"
    return judge.judge_l01(variant, {"sent": t["counted"], "lost": t["lost"]}, t["counted"])


def service_call_ok(timed_out: bool, rc: int | None, info: dict | None) -> bool:
    """A service trial counts as received only for a clean client reporting ok == true."""
    return not timed_out and rc == 0 and info is not None and info.get("ok") is True


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


def run_variant(variant: str, trials: int, env: dict, tmp: Path,
                side: PiSide | None = None) -> tuple[dict, dict, list]:
    side = side or PiSide(env)
    if VARIANTS[variant]["mode"] == "service":
        return _run_service(variant, trials, env, side)
    if VARIANTS[variant]["late_sub"]:
        return _run_late_sub(variant, trials, env, tmp, side)
    return _run_persistent_sub(variant, trials, env, tmp, side)


def _read_summary(path: Path) -> dict | None:
    try:
        d = json.loads(path.with_name(path.name + ".summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _run_late_sub(variant: str, trials: int, env: dict, tmp: Path,
                  side: PiSide) -> tuple[dict, dict, list]:
    cfg = VARIANTS[variant]
    topic = topic_for(variant, side.prefix)
    tag = variant.lower().replace("-", "_")
    received: list[int] = []
    reasons: dict[int, str] = {}
    offsets: dict[str, float | None] = {}
    pub_infos: list[dict] = []
    for trial in range(trials):
        out_path = tmp / f"l01_e_{tag}_{trial}.jsonl"
        res_path = tmp / f"l01_e_{tag}_{trial}.pub.json"
        pub_args = [
            "--topic", topic,
            "--profile", cfg["profile"], "--trial", str(trial), "--mode", "once",
            "--linger", str(LATE_PUB_LINGER_S), "--ready-after-publish",
        ]  # fmt: skip
        if not side.remote:  # a remote publisher reports on stdout instead
            pub_args += ["--result-out", str(res_path)]
        sub_cmd = [
            sys.executable, str(NODES / "l01_sub.py"), "--topic", topic,
            "--profile", cfg["profile"], "--out", str(out_path),
            "--duration", str(LATE_SUB_DURATION_S),
        ]  # fmt: skip
        pub = None
        pub_ready = False
        pub_res = proc.ChildResult(None, "", True)
        sub_res = proc.ChildResult(None, "", True)
        try:
            pub = side.start("l01_pub.py", pub_args)
            pub_ready = proc.wait_ready(pub, READY_TIMEOUT_S)
            ready_ns = time.time_ns()  # this PC's clock, for remote offsets
            if pub_ready:
                time.sleep(LATE_SUB_DELAY_S)
                sub_res = proc.run_child(sub_cmd, LATE_SUB_TIMEOUT_S, env)
                pub_res = proc.finish_child(pub, LATE_PUB_TIMEOUT_S)
        finally:
            if pub is not None:
                side.stop(pub)
        if side.remote:
            pub_info = _parse_pub_line(pub_res.stdout) or {}
            # Two machines' clocks are never subtracted: READY seen on this PC (after the
            # publish) stands in for publish time, so the measured offset is a lower bound.
            publish_ns = ready_ns if pub_info.get("publish_ns") is not None else None
        else:
            pub_info = _read_json(res_path) or {}
            publish_ns = pub_info.get("publish_ns")
        pub_infos.append(pub_info)
        sub_created_ns = (_read_summary(out_path) or {}).get("sub_created_ns")
        offset = late_offset_ms(sub_created_ns, publish_ns)
        offsets[str(trial)] = offset
        why = late_trial_exclusion(
            pub_ready, pub_res.timed_out, pub_res.rc, publish_ns, pub_info.get("sent"),
            sub_res.timed_out, sub_res.rc, sub_created_ns, offset,
        )  # fmt: skip
        if why is not None:
            reasons[trial] = why
        elif trial in _read_rx(out_path):
            received.append(trial)
        time.sleep(TRIAL_GAP_S)
    t = counted_tally(trials, received, reasons)
    obs = dict(t)
    obs["trials"] = trials
    obs["pub_sent_total"] = pub_sent_total(pub_infos)
    obs["sub_ready"] = True
    obs["sub_alive"] = True
    obs["sub_summary_present"] = True
    obs["proc_label"] = "구독 노드"
    obs["sub_exit_code"] = None
    obs["bad_messages"] = None
    obs["excluded_trials"] = sorted(reasons)
    obs["excluded_reasons"] = {str(k): v for k, v in sorted(reasons.items())}
    obs["offset_ms"] = offsets
    obs["offset_stats_ms"] = offset_stats([o for o in offsets.values() if o is not None])
    cond = {
        "trials": trials,
        "profile": cfg["profile"],
        "qos": qos.load_profiles()[cfg["profile"]],
        "mode": cfg["mode"],
        "topic": topic,
        "trial_gap_s": TRIAL_GAP_S,
        "late_sub_delay_s": LATE_SUB_DELAY_S,
        "min_offset_ms": MIN_LATE_OFFSET_MS,
        "pub_linger_s": LATE_PUB_LINGER_S,
        "sub_duration_s": LATE_SUB_DURATION_S,
    }
    return obs, cond, []


def _run_service(variant: str, trials: int, env: dict,
                 side: PiSide) -> tuple[dict, dict, list]:
    service = topic_for(variant, side.prefix)
    server = None
    ready = False
    alive = True
    server_rc = None
    received: list[int] = []
    reasons: dict[int, str] = {}
    wait_ms: dict[str, float | None] = {}
    try:
        server = proc.start_child(
            [sys.executable, str(NODES / "l01_srv.py"), "server", "--service", service], env
        )
        ready = proc.wait_ready(server, READY_TIMEOUT_S)
        if ready:
            for trial in range(trials):
                cmd = [
                    "client", "--service", service,
                    "--trial", str(trial), "--timeout", str(CLIENT_TIMEOUT_S),
                ]  # fmt: skip
                r = side.run("l01_srv.py", cmd, CLIENT_PROC_TIMEOUT_S)
                info = _parse_pub_line(r.stdout)
                if info is not None:
                    wait_ms[str(trial)] = info.get("wait_ms")
                if service_call_ok(r.timed_out, r.rc, info):
                    received.append(trial)
                elif r.timed_out:
                    reasons[trial] = "클라이언트 타임아웃"
                elif r.rc != 0:
                    reasons[trial] = f"클라이언트 비정상 종료(rc={r.rc})"
                elif info is None:
                    reasons[trial] = "클라이언트 결과 줄 없음"
                if server.poll() is not None:
                    alive = False
                time.sleep(TRIAL_GAP_S)
            if server.poll() is not None:
                alive = False
    finally:
        if server is not None:
            server_rc = server.poll()
            proc.stop_child(server)
    alive = alive and ready
    t = counted_tally(trials, received, reasons)
    obs = dict(t)
    obs["trials"] = trials
    obs["pub_sent_total"] = None  # 서비스: 발행 노드 없음(클라이언트 호출 = 시도)
    obs["sub_ready"] = ready
    obs["sub_alive"] = alive
    obs["sub_summary_present"] = True
    obs["proc_label"] = "서비스 서버"
    obs["sub_exit_code"] = server_rc
    obs["bad_messages"] = None
    obs["excluded_trials"] = sorted(reasons)
    obs["excluded_reasons"] = {str(k): v for k, v in sorted(reasons.items())}
    obs["wait_ms"] = wait_ms
    cond = {
        "trials": trials,
        "mode": "service",
        "service": service,
        "service_type": "std_srvs/srv/Trigger",
        "trial_gap_s": TRIAL_GAP_S,
        "client_timeout_s": CLIENT_TIMEOUT_S,
    }
    return obs, cond, [] if ready else ["서버가 READY를 내지 않음"]


def _run_persistent_sub(
    variant: str, trials: int, env: dict, tmp: Path, side: PiSide
) -> tuple[dict, dict, list]:
    cfg = VARIANTS[variant]
    topic = topic_for(variant, side.prefix)
    mode = cfg["mode"]
    out_path = tmp / f"l01_{variant}.jsonl"
    sub = None
    pubs: dict[int, dict] = {}
    timed_out: list[int] = []
    failed: list[int] = []
    reasons: dict[int, str] = {}
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
                    "--topic", topic,
                    "--profile", cfg["profile"], "--trial", str(trial), "--mode", mode,
                    "--wait-timeout", str(WAIT_TIMEOUT_S), "--rate", str(RATE_HZ),
                    "--stream-seconds", str(STREAM_SECONDS),
                ]  # fmt: skip
                r = side.run("l01_pub.py", cmd, PUB_TIMEOUT[mode])
                info = _parse_pub_line(r.stdout)
                why = pub_trial_exclusion(r.timed_out, r.rc, info)
                if why is not None:
                    reasons[trial] = why
                    (timed_out if r.timed_out else failed).append(trial)
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
    excluded = set(reasons)
    t = counted_tally(trials, list(rx), excluded)
    obs = dict(t)
    obs["trials"] = trials
    obs["pub_sent_total"] = pub_sent_total(pubs.values())
    obs["proc_label"] = "구독 노드"
    obs["sub_ready"] = sub_ready
    obs["bad_messages"] = bad
    obs["sub_alive"] = sub_alive
    obs["sub_exit_code"] = sub_rc
    obs["sub_summary_present"] = bad is not None
    obs["excluded_trials"] = sorted(excluded)
    obs["excluded_reasons"] = {str(k): v for k, v in sorted(reasons.items())}
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
                if side.remote:
                    fr = remote_first_receive(rx.get(trial, []))
                else:
                    fr = judge.first_receive(p["pub_start_ns"], rx.get(trial, []))
            firsts[str(trial)] = fr
        obs["first_receive"] = firsts
        if side.remote:
            obs["first_seq_stats"] = offset_stats(
                [f["first_seq"] for f in firsts.values() if f is not None]
            )
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


def _finish_variant(v: str, obs: dict, cond: dict, notes: list, trials: int,
                    pi_host: str | None, remote_pi: bool) -> str:
    """Verdict and notes for one variant (shared by stage 1 and L-01-W)."""
    result = "판정 불가"
    label = obs["proc_label"]
    if obs["sub_ready"]:
        result = sub_verdict(
            obs["sub_alive"],
            obs["sub_summary_present"],
            l01_verdict(v, obs, len(obs["excluded_trials"])),
        )
    if obs["excluded_trials"]:
        why = "; ".join(f"{k}: {r}" for k, r in obs["excluded_reasons"].items())
        notes.append(
            f"제외 {len(obs['excluded_trials'])}건/{trials}건, "
            f"집계 {obs['counted']}건 ({why})"
        )
        if v in judge.RULE_VARIANTS_L01:
            notes.append("판정 규칙 변형에서 제외된 시도가 있어 판정 불가")
    if obs["sub_ready"] and not obs["sub_alive"]:
        notes.append(f"{label}가 시험 중 종료됨(rc={obs['sub_exit_code']})")
    if obs["sub_ready"] and not obs["sub_summary_present"]:
        notes.append(f"{label} summary 파일 없음")
    if result != "판정 불가" and v == "A" and obs["lost_trials"]:
        notes.append("재현됨")
    result, dev_note = gated_verdict(v, result, trials)
    if dev_note:
        notes.append(dev_note)
    if pi_host is not None:
        result, dev_note = device_gate(result, v, pi_host)
        if dev_note:
            notes.append(dev_note)
        cond["pi_host"] = pi_host
        cond["devices"] = "1대" if pi_host == "local" else "2대"
        if remote_pi and VARIANTS[v]["late_sub"]:
            cond["late_offset_basis"] = LATE_OFFSET_BASIS
    if remote_pi and VARIANTS[v]["mode"] == "stream":
        notes.append(CLOCK_NOTE)
    return result


def retire_unjudgeable(run_dir: Path, prefix: str, names: Iterable[str]) -> list[str]:
    """--resume of L-01-W: move aside variants whose result is 판정 불가 with excluded trials
    (or with every trial excluded), plus their part, so they run again. Returns the variants."""
    retired = []
    for v in names:
        path = Path(run_dir) / f"{prefix}-{v}.json"
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            obs = d.get("observations") or {}
        except (OSError, ValueError, AttributeError):
            continue
        excluded = obs.get("excluded_trials") or []
        all_out = bool(excluded) and obs.get("counted") == 0
        if (d.get("result") == "판정 불가" and excluded) or all_out:
            parts.retire_file(path)
            part = parts.part_path(run_dir, prefix, v)
            if part.exists():
                parts.retire_file(part)
            retired.append(v)
    return retired


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--out-root", default=str(HERE / "results"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument(
        "--discovery-range", choices=proc.DISCOVERY_RANGES, default=None,
        help="ROS_AUTOMATIC_DISCOVERY_RANGE for every child and ros2 doctor (기본 LOCALHOST; "
        "--pi-host와 함께 쓸 수 없음)",
    )
    ap.add_argument("--pi-host", default=None,
                    help="발행·클라이언트 측을 돌릴 라즈베리파이(local = 이 PC 한 대). 기본: 1단계")
    ap.add_argument("--id-prefix", default=STAGE1_ID_PREFIX,
                    help="결과 ID 접두사(2단계는 L-01-W)")
    ap.add_argument("--resume", action="store_true",
                    help="--pi-host 실행에서 끝난 변형을 건너뛰고 이어서 실행"
                         "(제외된 시도로 판정 불가인 변형은 다시 실행)")
    args = ap.parse_args(argv)

    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        ap.error(f"unknown variants: {unknown}")
    if args.pi_host is not None and args.discovery_range is not None:
        ap.error("--discovery-range cannot be combined with --pi-host "
                 "(SUBNET for a real host, LOCALHOST for local)")
    if args.resume and args.pi_host is None:
        ap.error("--resume needs --pi-host")
    if args.pi_host is not None:
        names.sort(key=W_ORDER.index)

    two_devices = args.pi_host is not None and args.pi_host != "local"
    local_range = args.discovery_range or proc.STAGE1_DISCOVERY
    proc.pin_discovery(os.environ, "SUBNET" if two_devices else local_range)
    env = proc.child_env(os.environ)
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    run_path = Path(args.out_root) / run_id
    prefix = args.id_prefix
    done: set[str] = set()
    if args.pi_host is not None:
        done = parts.done_keys(run_path, prefix)
    clash = results.existing_targets(run_path, target_files(names, prefix))
    if (clash or done) and not args.resume:
        print(f"실행 ID {run_id!r}에 이미 결과가 있습니다: {', '.join(clash or sorted(done))}. "
              "다른 --run-id를 쓰세요.", file=sys.stderr)  # fmt: skip
        return 2
    run_dir = results.new_run_dir(Path(args.out_root), run_id)
    results.save_doctor(run_dir)
    if args.resume:
        for v in retire_unjudgeable(run_dir, prefix, names):
            print(f"{prefix}-{v}: 제외된 시도로 판정 불가 → 다시 실행", flush=True)
        done = parts.done_keys(run_dir, prefix)
    launcher = None
    side = PiSide(env, prefix=topic_prefix(prefix))
    if args.pi_host is not None:
        launcher = remote.make_launcher(args.pi_host, env)
        side = PiSide(
            env, launcher,
            {"ROS_AUTOMATIC_DISCOVERY_RANGE": env["ROS_AUTOMATIC_DISCOVERY_RANGE"],
             "ROS_DOMAIN_ID": env["ROS_DOMAIN_ID"]},
            topic_prefix(prefix),
        )
    try:
        with tempfile.TemporaryDirectory(prefix="r2g_l01_") as td:
            for v in names:
                test_id = f"{prefix}-{v}"
                if (run_dir / f"{test_id}.json").exists():
                    print(f"{test_id}: 건너뜀(이미 끝남)", flush=True)
                    continue
                started_at = results.now_iso()
                obs, cond, notes = run_variant(v, args.trials, env, Path(td), side)
                result = _finish_variant(
                    v, obs, cond, notes, args.trials, args.pi_host, side.remote
                )
                results.write_result(
                    run_dir, test_id, result, obs, cond, notes, env=env, started_at=started_at
                )
                if args.pi_host is not None and v not in done:
                    parts.save_part(run_dir, prefix, v, {"variant": v, "result": result})
                print(
                    f"{test_id}: {result} lost={obs['lost']}/{obs['counted']} "
                    f"excluded={len(obs['excluded_trials'])}",
                    flush=True,
                )
    finally:
        if launcher is not None:
            launcher.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
