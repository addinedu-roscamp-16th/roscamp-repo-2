"""Subprocess helpers for running ROS node scripts as children."""
import select
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass

_KILL_WAIT = 2.0


def child_env(base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env.setdefault("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST")
    env.setdefault("ROS_DOMAIN_ID", "87")
    return env


@dataclass
class ChildResult:
    rc: int | None
    stdout: str
    timed_out: bool


def start_child(cmd: list[str], env: Mapping[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def run_child(cmd: list[str], timeout: float, env: Mapping[str, str]) -> ChildResult:
    p = start_child(cmd, env)
    try:
        out, _ = p.communicate(timeout=timeout)
        return ChildResult(p.returncode, out or "", False)
    except subprocess.TimeoutExpired:
        p.send_signal(signal.SIGINT)
        try:
            out, _ = p.communicate(timeout=_KILL_WAIT)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
        return ChildResult(p.returncode, out or "", True)


def wait_ready(p: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or p.stdout is None:
            return False
        ready, _, _ = select.select([p.stdout], [], [], remaining)
        if not ready:
            return False
        line = p.stdout.readline()
        if not line:
            return False
        if line.strip() == "READY":
            return True


def stop_child(p: subprocess.Popen, grace: float = 3.0) -> None:
    if p.poll() is None:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    if p.stdout is not None:
        p.stdout.close()
