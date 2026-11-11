import os
import subprocess
import time
from pathlib import Path

from r2g import proc, remote
from r2g.remote import (
    kill_cmd,
    make_launcher,
    parse_iwconfig,
    parse_pid,
    remote_cmd,
    sync_cmd,
)


def test_remote_cmd_quotes_args_and_sets_env():
    cmd = remote_cmd("sbt-pi", "l02_pub.py", ["--topic", "/a b"], {"ROS_DOMAIN_ID": "87"})
    assert cmd[:6] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "sbt-pi"]
    tail = cmd[6]
    assert "pi_exec.sh ROS_DOMAIN_ID=87 --" in tail
    assert "r2g/tests/ros2_guide/nodes/l02_pub.py" in tail and "'/a b'" in tail


def test_parse_pid():
    assert parse_pid("PID 4242\nREADY\n") == 4242
    assert parse_pid("READY\n") is None


def test_kill_cmd():
    assert kill_cmd("sbt-pi", 7, "INT")[-3:] == ["kill", "-INT", "7"]


def test_parse_iwconfig():
    text = 'wlan0  IEEE 802.11  ESSID:"x"\n   Link Quality=58/70  Signal level=-52 dBm\n'
    info = parse_iwconfig(text)
    assert (info["signal_dbm"], info["link_quality"]) == (-52, "58/70")
    assert parse_iwconfig("no wireless")["signal_dbm"] is None


def test_make_launcher_local():
    assert make_launcher("local", {}).is_local


def test_local_launcher_shell_uses_env():
    r = make_launcher("local", {"A": "1"}).shell("echo $A", 5.0)
    assert r.stdout.strip() == "1"


def test_make_launcher_remote():
    lau = make_launcher("sbt-pi", {"ROS_DOMAIN_ID": "12"})
    assert not lau.is_local and lau.host == "sbt-pi"


def test_remote_cmd_command_goes_through_bash_c():
    tail = remote.remote_command_cmd("sbt-pi", "gst-launch-1.0 a ! b", {"X": "1"})[6]
    assert "pi_exec.sh X=1 -- bash -c 'gst-launch-1.0 a ! b'" in tail


def test_sync_cmd_copies_both_trees_with_relative_paths():
    cmd = sync_cmd("sbt-pi", Path("/repo"))
    assert cmd[0] == "rsync" and "-azR" in cmd and "--delete" in cmd
    for ex in ("results/", "__pycache__/", "unit/", ".pytest_cache/"):
        assert f"--exclude={ex}" in cmd
    assert "/repo/./tests/ros2_guide" in cmd
    assert "/repo/./interfaces/fleet_interfaces/contract.yaml" in cmd
    assert cmd[-1] == "sbt-pi:r2g/"


def test_local_cleanup_patterns_spare_stage1():
    import re

    pat = re.compile(remote.LOCAL_CLEANUP_PATTERN)
    for s in ("l02_pub.py", "l04_state.py", "g11_node.py", "udp_count.py"):
        assert pat.search(f"python3 -u /x/tests/ros2_guide/nodes/{s}")
    for s in ("l01_pub.py", "l01_sub.py", "l03_pub.py", "l01_srv.py"):
        assert not pat.search(f"python3 -u /x/tests/ros2_guide/nodes/{s}")


def test_local_start_command_merges_env_over():
    lau = make_launcher("local", {"A": "1", "B": "old"})
    p = lau.start_command("echo $A-$B", {"B": "2"})
    out = proc.finish_child(p, 5.0)
    assert out.stdout.strip() == "1-2"


def _bash(script):
    return proc.start_child(["bash", "-c", script], os.environ)


def test_read_pid_line_keeps_ready_in_pipe():
    p = _bash("echo PID 5; echo READY; sleep 5")
    try:
        assert remote._read_pid_line(p, 5.0) == 5
        assert proc.wait_ready(p, 5.0)
    finally:
        proc.stop_child(p)


def test_read_pid_line_none_without_pid():
    p = _bash("echo hello; sleep 5")
    try:
        t0 = time.monotonic()
        assert remote._read_pid_line(p, 0.5) is None
        assert time.monotonic() - t0 < 3.0
    finally:
        proc.stop_child(p)


def test_remote_run_timeout_kills_remote_pid(monkeypatch):
    lau = remote.RemoteLauncher("h", "87")
    kills = []
    monkeypatch.setattr(proc, "run_child",
                        lambda cmd, t, env: proc.ChildResult(None, "PID 77\nx\n", True))
    monkeypatch.setattr(lau, "_kill", lambda pid, sig: kills.append((pid, sig)))
    assert lau.run("l02_pub.py", [], 1.0, {}).timed_out
    assert kills == [(77, "KILL")]


def test_remote_run_timeout_without_pid_no_kill(monkeypatch):
    lau = remote.RemoteLauncher("h", "87")
    kills = []
    monkeypatch.setattr(proc, "run_child",
                        lambda cmd, t, env: proc.ChildResult(None, "nothing\n", True))
    monkeypatch.setattr(lau, "_kill", lambda pid, sig: kills.append((pid, sig)))
    lau.run("l02_pub.py", [], 1.0, {})
    assert kills == []


def test_remote_run_ok_no_kill(monkeypatch):
    lau = remote.RemoteLauncher("h", "87")
    kills = []
    monkeypatch.setattr(proc, "run_child",
                        lambda cmd, t, env: proc.ChildResult(0, "PID 77\n", False))
    monkeypatch.setattr(lau, "_kill", lambda pid, sig: kills.append((pid, sig)))
    lau.run("l02_pub.py", [], 1.0, {})
    assert kills == []


def test_remote_stop_sends_int_then_kill_and_stops_local(monkeypatch):
    lau = remote.RemoteLauncher("h", "87")
    kills = []
    monkeypatch.setattr(lau, "_kill", lambda pid, sig: kills.append((pid, sig)))
    p = _bash("trap '' INT; sleep 30")  # ignores INT, so only the local stop reaps it
    p.r2g_pid = 99
    lau.stop(p)
    assert p.poll() is not None
    assert kills == [(99, "INT"), (99, "KILL")]


def test_remote_stop_without_pid_warns(capsys):
    lau = remote.RemoteLauncher("h", "87")
    p = _bash("sleep 30")
    p.r2g_pid = None
    lau.stop(p)
    assert p.poll() is not None
    assert "only the local ssh" in capsys.readouterr().err


def test_remote_start_warns_without_pid(monkeypatch, capsys):
    lau = remote.RemoteLauncher("h", "87")
    monkeypatch.setattr(remote, "_PID_WAIT", 0.3)
    monkeypatch.setattr(remote, "remote_cmd", lambda *a: ["bash", "-c", "sleep 5"])
    p = lau.start("l02_pub.py", [], {})
    try:
        assert p.r2g_pid is None
        assert "no PID line from h" in capsys.readouterr().err
    finally:
        proc.stop_child(p)


def test_parse_pi_env():
    out = "ROS_DISTRO=jazzy\nRMW_IMPLEMENTATION=\nhost=pi\narch=aarch64\n"
    assert remote.parse_pi_env(out) == {
        "ROS_DISTRO": "jazzy", "RMW_IMPLEMENTATION": "기본", "host": "pi", "arch": "aarch64"}
    assert remote.parse_pi_env("")["RMW_IMPLEMENTATION"] == "기본"


def test_local_cleanup_stops_only_own_commands(monkeypatch):
    lau = make_launcher("local", {})
    monkeypatch.setattr(proc, "run_child", lambda cmd, t, env: proc.ChildResult(0, "", False))
    mine = lau.start_command("sleep 30", {})
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        lau.cleanup()
        assert mine.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()
