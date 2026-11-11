"""Launchers that start test nodes on this PC or on the Raspberry Pi over SSH."""
import os
import re
import select
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from r2g import proc

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
REMOTE_ROOT = "r2g"  # relative to the remote home
REMOTE_NODES = f"{REMOTE_ROOT}/tests/ros2_guide/nodes"
NODES = Path(__file__).resolve().parent.parent / "nodes"
# Stage-2-only scripts. Never matches l01_* / l03_* (stage 1 nodes).
LOCAL_CLEANUP_PATTERN = r"tests/ros2_guide/nodes/(l02_|l04_|g11_node\.py|udp_count\.py)"
SYNC_EXCLUDES = ["results/", "__pycache__/", "unit/", ".pytest_cache/"]
_PID_WAIT = 10.0
_PROBE_TIMEOUT = 15.0


def _ssh(host: str, remote_command: str) -> list[str]:
    return ["ssh", *SSH_OPTS, host, remote_command]


def _env_args(env_over: Mapping[str, str]) -> str:
    return " ".join(shlex.quote(f"{k}={v}") for k, v in env_over.items())


def remote_cmd(host: str, script: str, args: list[str], env_over: Mapping[str, str]) -> list[str]:
    parts = [f"bash {REMOTE_NODES}/pi_exec.sh"]
    if env_over:
        parts.append(_env_args(env_over))
    parts += ["--", "python3", "-u", f"{REMOTE_NODES}/{script}", *(shlex.quote(a) for a in args)]
    return _ssh(host, " ".join(parts))


def remote_command_cmd(host: str, command: str, env_over: Mapping[str, str]) -> list[str]:
    parts = [f"bash {REMOTE_NODES}/pi_exec.sh"]
    if env_over:
        parts.append(_env_args(env_over))
    parts += ["--", "bash", "-c", shlex.quote(command)]
    return _ssh(host, " ".join(parts))


def parse_pid(stdout: str) -> int | None:
    for line in stdout.splitlines():
        m = re.fullmatch(r"PID (\d+)", line.strip())
        if m:
            return int(m.group(1))
    return None


def kill_cmd(host: str, pid: int, signal: str) -> list[str]:
    return ["ssh", *SSH_OPTS, host, "kill", f"-{signal}", str(pid)]


def _read_pid_line(p: subprocess.Popen, timeout: float) -> int | None:
    """Read byte-wise up to the PID line so a later READY line stays in the pipe."""
    if p.stdout is None:
        return None
    fd = p.stdout.fileno()
    deadline = time.monotonic() + timeout
    buf = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return None
        ch = os.read(fd, 1)
        if not ch:
            return None
        if ch != b"\n":
            buf += ch
            continue
        pid = parse_pid(buf.decode(errors="replace"))
        if pid is not None:
            return pid
        buf = b""


class Launcher:
    is_local: bool
    host: str

    def start(self, script: str, args: list[str], env_over: Mapping[str, str]):
        raise NotImplementedError

    def start_command(self, command: str, env_over: Mapping[str, str]):
        raise NotImplementedError

    def run(self, script: str, args: list[str], timeout: float,
            env_over: Mapping[str, str]) -> proc.ChildResult:
        raise NotImplementedError

    def stop(self, p: subprocess.Popen) -> None:
        raise NotImplementedError

    def shell(self, command: str, timeout: float) -> proc.ChildResult:
        raise NotImplementedError

    def cleanup(self) -> None:
        raise NotImplementedError


class LocalLauncher(Launcher):
    is_local = True
    host = "local"

    def __init__(self, env: Mapping[str, str]):
        self.env = proc.child_env(env)

    def _env(self, env_over: Mapping[str, str]) -> dict[str, str]:
        return {**self.env, **env_over}

    @staticmethod
    def _script_cmd(script: str, args: list[str]) -> list[str]:
        return [sys.executable, "-u", str(NODES / script), *args]

    def start(self, script, args, env_over):
        return proc.start_child(self._script_cmd(script, args), self._env(env_over))

    def start_command(self, command, env_over):
        return proc.start_child(["bash", "-c", command], self._env(env_over))

    def run(self, script, args, timeout, env_over):
        return proc.run_child(self._script_cmd(script, args), timeout, self._env(env_over))

    def stop(self, p):
        proc.stop_child(p)

    def shell(self, command, timeout):
        return proc.run_child(["bash", "-c", command], timeout, self.env)

    def cleanup(self):
        for pattern in (LOCAL_CLEANUP_PATTERN, r"gst-launch-1\.0"):
            proc.run_child(["pkill", "-f", pattern], 10.0, self.env)


class RemoteLauncher(Launcher):
    is_local = False

    def __init__(self, host: str, domain_id: str):
        self.host = host
        self.domain_id = domain_id

    def _env(self, env_over: Mapping[str, str]) -> dict[str, str]:
        return {"ROS_DOMAIN_ID": self.domain_id,
                "ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET", **env_over}

    def _kill(self, pid: int, signal: str) -> None:
        proc.run_child(kill_cmd(self.host, pid, signal), 10.0, os.environ)

    def _start(self, cmd: list[str]) -> subprocess.Popen:
        p = proc.start_child(cmd, os.environ)
        p.r2g_pid = _read_pid_line(p, _PID_WAIT)
        return p

    def start(self, script, args, env_over):
        return self._start(remote_cmd(self.host, script, args, self._env(env_over)))

    def start_command(self, command, env_over):
        return self._start(remote_command_cmd(self.host, command, self._env(env_over)))

    def run(self, script, args, timeout, env_over):
        cmd = remote_cmd(self.host, script, args, self._env(env_over))
        result = proc.run_child(cmd, timeout, os.environ)
        if result.timed_out:
            pid = parse_pid(result.stdout)
            if pid is not None:
                self._kill(pid, "KILL")
        return result

    def stop(self, p):
        pid = getattr(p, "r2g_pid", None)
        if pid is not None and p.poll() is None:
            self._kill(pid, "INT")
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._kill(pid, "KILL")
        proc.stop_child(p)

    def shell(self, command, timeout):
        return proc.run_child(_ssh(self.host, command), timeout, os.environ)

    def cleanup(self):
        # [r]/[g] keep the pattern from matching the remote shell running pkill itself.
        self.shell("pkill -f '[r]2g/tests/ros2_guide/nodes/'; "
                   "pkill -f '[g]st-launch-1.0'; true", 15.0)


def make_launcher(pi_host: str, env: Mapping[str, str]) -> Launcher:
    if pi_host == "local":
        return LocalLauncher(env)
    return RemoteLauncher(pi_host, env.get("ROS_DOMAIN_ID", "87"))


def sync_cmd(host: str, repo_root: Path) -> list[str]:
    root = str(repo_root)
    return [
        "rsync", "-azR", "--delete",
        *(f"--exclude={e}" for e in SYNC_EXCLUDES),
        "-e", " ".join(["ssh", *SSH_OPTS]),
        f"{root}/./tests/ros2_guide",
        f"{root}/./interfaces/fleet_interfaces/contract.yaml",
        f"{host}:{REMOTE_ROOT}/",
    ]


def sync(host: str, repo_root: Path) -> proc.ChildResult:
    return proc.run_child(sync_cmd(host, repo_root), 120.0, os.environ)


def parse_iwconfig(text: str) -> dict:
    sig = re.search(r"Signal level[=:]\s*(-?\d+)", text)
    lq = re.search(r"Link Quality[=:]\s*(\d+/\d+)", text)
    return {
        "signal_dbm": int(sig.group(1)) if sig else None,
        "link_quality": lq.group(1) if lq else None,
        "raw": text,
    }


def wifi_info(launcher: Launcher) -> dict:
    if launcher.is_local:
        return {"signal_dbm": None, "link_quality": None, "raw": "PI_HOST=local"}
    return parse_iwconfig(launcher.shell("iwconfig wlan0 2>&1", _PROBE_TIMEOUT).stdout)


def pc_address_seen_by(host: str) -> str | None:
    r = proc.run_child(_ssh(host, "echo $SSH_CLIENT"), _PROBE_TIMEOUT, os.environ)
    fields = r.stdout.split()
    return fields[0] if r.rc == 0 and fields else None


_PI_ENV_CMD = (
    "[ -f /opt/ros/jazzy/setup.bash ] && source /opt/ros/jazzy/setup.bash >/dev/null 2>&1; "
    "echo ROS_DISTRO=$ROS_DISTRO; echo RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION; "
    "echo host=$(hostname); echo arch=$(uname -m)"
)


def pi_env(launcher: Launcher) -> dict:
    out = launcher.shell(_PI_ENV_CMD, _PROBE_TIMEOUT).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "ROS_DISTRO": kv.get("ROS_DISTRO") or "",
        "RMW_IMPLEMENTATION": kv.get("RMW_IMPLEMENTATION") or "기본",
        "host": kv.get("host", ""),
        "arch": kv.get("arch", ""),
    }
