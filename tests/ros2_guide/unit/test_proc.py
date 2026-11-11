import os
import sys

import pytest

from r2g.proc import (
    child_env,
    pin_discovery,
    run_child,
    start_child,
    stop_child,
    wait_ready,
)


def test_child_env_defaults():
    env = child_env({"PATH": "/bin"})
    assert env["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    assert env["ROS_DOMAIN_ID"] == "87"


def test_child_env_keeps_domain():
    assert child_env({"ROS_DOMAIN_ID": "12"})["ROS_DOMAIN_ID"] == "12"


def test_run_child_timeout():
    r = run_child([sys.executable, "-c", "import time; time.sleep(30)"], 1.0, os.environ)
    assert r.timed_out


def test_run_child_ok():
    r = run_child([sys.executable, "-c", "print('hi')"], 5.0, os.environ)
    assert (r.rc, r.stdout.strip(), r.timed_out) == (0, "hi", False)


def test_wait_ready_and_stop():
    p = start_child(
        [sys.executable, "-u", "-c", "import time; print('READY'); time.sleep(30)"],
        os.environ,
    )
    assert wait_ready(p, 5.0)
    stop_child(p, grace=1.0)
    assert p.poll() is not None


def test_wait_ready_multiple_lines_in_one_write():
    code = (
        "import sys, time; sys.stdout.write('a\\nb\\nREADY\\n'); "
        "sys.stdout.flush(); time.sleep(30)"
    )
    p = start_child([sys.executable, "-u", "-c", code], os.environ)
    try:
        assert wait_ready(p, 5.0)
    finally:
        stop_child(p, grace=1.0)


def test_finish_child_timeout_and_ok():
    from r2g.proc import finish_child, start_child

    p = start_child([sys.executable, "-c", "print('x')"], os.environ)
    r = finish_child(p, 5.0)
    assert (r.rc, r.timed_out, r.stdout.strip()) == (0, False, "x")
    p = start_child([sys.executable, "-c", "import time; time.sleep(30)"], os.environ)
    r = finish_child(p, 0.5)
    assert r.timed_out is True and r.rc is not None


def test_pin_discovery_overrides_sourced_subnet():
    environ = {"ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET"}
    pin_discovery(environ)
    assert environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    assert child_env(environ)["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"


def test_pin_discovery_explicit_and_invalid():
    environ = {}
    pin_discovery(environ, "SUBNET")
    assert environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "SUBNET"
    with pytest.raises(ValueError):
        pin_discovery(environ, "subnet")
