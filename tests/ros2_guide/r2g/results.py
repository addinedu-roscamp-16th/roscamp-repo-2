"""Result files: one JSON per test id, plus the `ros2 doctor` report."""
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

RESULTS = ("통과", "실패", "미실행", "판정 불가", "측정")
UNKNOWN_COMMIT = "알 수 없음"
TOOL_DIR = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def tool_commit() -> str:
    """Short git commit of the test tool (best effort)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(TOOL_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_COMMIT
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else UNKNOWN_COMMIT


def existing_targets(run_dir: Path, names: Iterable[str]) -> list[str]:
    """Names of target files that already exist in run_dir (a reused run ID)."""
    return [n for n in names if (Path(run_dir) / n).exists()]


def samples_name(test_id: str) -> str:
    return f"{test_id}.samples.json"


def write_samples(run_dir: Path, test_id: str, data: dict) -> Path:
    """Raw per-sample sidecar for a result; never overwrites."""
    path = Path(run_dir) / samples_name(test_id)
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return path


def env_info(env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "ROS_DISTRO": env.get("ROS_DISTRO", ""),
        "RMW_IMPLEMENTATION": env.get("RMW_IMPLEMENTATION") or "기본",
        "ROS_DOMAIN_ID": env.get("ROS_DOMAIN_ID", ""),
        "ROS_AUTOMATIC_DISCOVERY_RANGE": env.get("ROS_AUTOMATIC_DISCOVERY_RANGE", ""),
        "host": socket.gethostname(),
    }


def new_run_dir(root: Path, run_id: str | None = None) -> Path:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    path = Path(root) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_result(
    run_dir: Path,
    test_id: str,
    result: str,
    observations: dict,
    conditions: dict,
    notes: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    started_at: str | None = None,
) -> Path:
    if result not in RESULTS:
        raise ValueError(f"unknown result {result!r}; expected one of {RESULTS}")
    path = Path(run_dir) / f"{test_id}.json"
    record = {
        "id": test_id,
        "result": result,
        "run_id": Path(run_dir).name,
        "started_at": started_at,
        "written_at": now_iso(),
        "tool_commit": tool_commit(),
        "env": env_info(env),
        "conditions": conditions,
        "observations": observations,
        "notes": notes or [],
    }
    with path.open("x", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path


def save_doctor(run_dir: Path) -> Path:
    path = Path(run_dir) / "doctor.txt"
    if path.exists():
        return path
    try:
        proc = subprocess.run(
            ["ros2", "doctor", "--report"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        text = proc.stdout
        if proc.returncode != 0:
            text += f"\n[ros2 doctor exit code {proc.returncode}]\n{proc.stderr}"
    except (OSError, subprocess.SubprocessError) as exc:
        text = f"ros2 doctor failed: {exc!r}\n"
    path.write_text(text, encoding="utf-8")
    return path
