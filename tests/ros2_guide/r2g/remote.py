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
        self._commands: list[subprocess.Popen] = []  # start_command children (gst-launch)

    def _env(self, env_over: Mapping[str, str]) -> dict[str, str]:
        return {**self.env, **env_over}

    @staticmethod
    def _script_cmd(script: str, args: list[str]) -> list[str]:
        return [sys.executable, "-u", str(NODES / script), *args]

    def start(self, script, args, env_over):
        return proc.start_child(self._script_cmd(script, args), self._env(env_over))

    def start_command(self, command, env_over):
        p = proc.start_child(["bash", "-c", command], self._env(env_over))
        self._commands.append(p)
        return p

    def run(self, script, args, timeout, env_over):
        return proc.run_child(self._script_cmd(script, args), timeout, self._env(env_over))

    def stop(self, p):
        proc.stop_child(p)

    def shell(self, command, timeout):
        return proc.run_child(["bash", "-c", command], timeout, self.env)

    def cleanup(self):
        # Only commands this launcher started: unrelated gst-launch processes stay alone.
        commands, self._commands = self._commands, []
        for p in commands:
            proc.stop_child(p)
        proc.run_child(["pkill", "-f", LOCAL_CLEANUP_PATTERN], 10.0, self.env)


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
        if p.r2g_pid is None:
            print(f"warning: no PID line from {self.host} for {cmd[-1]!r}; "
                  "the remote process cannot be stopped by PID", file=sys.stderr)
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
        if pid is None and p.poll() is None:
            print(f"warning: no remote PID for {self.host}; stopping only the local ssh "
                  "(the remote process may keep running until cleanup)", file=sys.stderr)
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


def parse_iw_link(text: str) -> int | None:
    """`iw dev <if> link`: "signal: -52 dBm"."""
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
    return int(m.group(1)) if m else None


def _proc_wireless_rows(text: str) -> list[list[str]]:
    """Interface rows of /proc/net/wireless (after the two header lines)."""
    rows = []
    for line in text.splitlines()[2:]:
        name, sep, rest = line.partition(":")
        if sep and name.strip():
            rows.append([name.strip(), *rest.split()])
    return rows


def wireless_iface(text: str) -> str | None:
    """First wireless interface listed in /proc/net/wireless, or None."""
    rows = _proc_wireless_rows(text)
    return rows[0][0] if rows else None


def parse_proc_wireless(text: str, iface: str | None = None) -> tuple[int | None, str | None]:
    """(signal_dbm, link) from /proc/net/wireless: status, link, level, noise columns."""
    for row in _proc_wireless_rows(text):
        if iface is not None and row[0] != iface or len(row) < 4:
            continue
        try:
            link = int(float(row[2]))
            level = int(float(row[3]))
        except ValueError:
            return None, None
        if level > 0:  # 8-bit dBm reported unsigned
            level -= 256
        return (level if level < 0 else None), str(link)
    return None, None


_WIFI_CMD = (
    'export PATH="$PATH:/usr/sbin:/sbin"; '
    "echo '@@r2g proc'; cat /proc/net/wireless 2>/dev/null; "
    "IF=$(awk 'NR>2 {sub(\":\", \"\", $1); print $1; exit}' /proc/net/wireless 2>/dev/null); "
    'IF="${IF:-wlan0}"; echo \'@@r2g iface\'; echo "$IF"; '
    'echo \'@@r2g iwconfig\'; iwconfig "$IF" 2>&1; '
    'echo \'@@r2g iw\'; iw dev "$IF" link 2>&1; true'
)


def parse_wifi_report(text: str) -> dict:
    """Signal from iwconfig, else `iw dev <if> link`, else /proc/net/wireless."""
    sections: dict[str, str] = {}
    name = None
    for line in text.splitlines(keepends=True):
        if line.startswith("@@r2g "):
            name = line[len("@@r2g "):].strip()
            sections[name] = ""
        elif name is not None:
            sections[name] += line
    iface = sections.get("iface", "").strip() or None
    info = {"signal_dbm": None, "link_quality": None, "iface": iface, "source": None,
            "raw": text}  # fmt: skip
    iwc = parse_iwconfig(sections.get("iwconfig", ""))
    if iwc["signal_dbm"] is not None:
        info.update(signal_dbm=iwc["signal_dbm"], link_quality=iwc["link_quality"],
                    source="iwconfig")  # fmt: skip
        return info
    sig = parse_iw_link(sections.get("iw", ""))
    if sig is not None:
        info.update(signal_dbm=sig, source="iw")
        return info
    sig, link = parse_proc_wireless(sections.get("proc", ""), iface)
    if sig is not None:
        info.update(signal_dbm=sig, link_quality=link, source="proc")
    return info


def wifi_info(launcher: Launcher) -> dict:
    if launcher.is_local:
        return {"signal_dbm": None, "link_quality": None, "iface": None, "source": None,
                "raw": "PI_HOST=local"}  # fmt: skip
    return parse_wifi_report(launcher.shell(_WIFI_CMD, _PROBE_TIMEOUT).stdout)


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
    return parse_pi_env(launcher.shell(_PI_ENV_CMD, _PROBE_TIMEOUT).stdout)


def parse_pi_env(out: str) -> dict:
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "ROS_DISTRO": kv.get("ROS_DISTRO") or "",
        "RMW_IMPLEMENTATION": kv.get("RMW_IMPLEMENTATION") or "기본",
        "host": kv.get("host", ""),
        "arch": kv.get("arch", ""),
    }
