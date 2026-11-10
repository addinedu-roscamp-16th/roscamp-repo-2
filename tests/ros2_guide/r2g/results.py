"""Result files: one JSON per test id, plus the `ros2 doctor` report."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path

RESULTS = ("통과", "실패", "미실행", "판정 불가", "측정")


def env_info() -> dict:
    return {
        "ROS_DISTRO": os.environ.get("ROS_DISTRO", ""),
        "RMW_IMPLEMENTATION": os.environ.get("RMW_IMPLEMENTATION") or "기본",
        "ROS_DOMAIN_ID": os.environ.get("ROS_DOMAIN_ID", ""),
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
) -> Path:
    if result not in RESULTS:
        raise ValueError(f"unknown result {result!r}; expected one of {RESULTS}")
    path = Path(run_dir) / f"{test_id}.json"
    record = {
        "id": test_id,
        "result": result,
        "run_id": Path(run_dir).name,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "env": env_info(),
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
