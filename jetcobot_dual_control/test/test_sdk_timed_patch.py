"""Verify the vendor SDK patch applies and schedules without hardware access."""
import importlib.util
import inspect
from pathlib import Path
import shutil
import subprocess

import pytest
from pymycobot.mycobot280 import MyCobot280


def patched_class(tmp_path):
    source = Path(inspect.getfile(MyCobot280))
    target = tmp_path / 'mycobot280.py'
    shutil.copy2(source, target)
    patch = Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'sdk_patch' / 'mycobot280_4_0_7_timed_angles.patch'
    subprocess.run(['patch', '-p1', '-d', str(tmp_path), '-i', str(patch)],
                   check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location('mycobot280_timed_test', target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MyCobot280


def test_timed_method_sends_final_target_without_waiting_for_waypoint(tmp_path):
    base = patched_class(tmp_path)

    class Fake(base):
        def get_fresh_mode(self):
            return 1

        def send_angles(self, angles, speed, _async=False):
            self.sent.append((angles, speed, _async))

    robot = object.__new__(Fake)
    robot.sent = []
    path = [(0.0, [0.0]*6), (0.3, [3.0, 0, 0, 0, 0, 0])]
    observed = []
    count = robot.send_timed_angles(
        path, 10, period_s=0.4, lead_s=0.3,
        check=lambda target, elapsed: observed.append((target, elapsed)))
    assert count == 1
    assert robot.sent == [([3.0, 0.0, 0.0, 0.0, 0.0, 0.0], 10, True)]
    assert len(observed) == 1


def test_timed_method_requires_mode_one_and_honors_cancel(tmp_path):
    base = patched_class(tmp_path)

    class Fake(base):
        def get_fresh_mode(self):
            return self.mode

        def send_angles(self, angles, speed, _async=False):
            raise AssertionError('No SDK command should be sent')

    robot = object.__new__(Fake)
    path = [(0.0, [0.0]*6), (1.0, [1.0]*6)]
    robot.mode = 0
    with pytest.raises(RuntimeError, match='mode 1'):
        robot.send_timed_angles(path, 10)
    robot.mode = 1
    with pytest.raises(InterruptedError, match='canceled'):
        robot.send_timed_angles(path, 10, should_stop=lambda: True)


def test_checked_target_must_reach_final_before_stream_returns(tmp_path):
    base = patched_class(tmp_path)

    class Fake(base):
        def get_fresh_mode(self):
            return 1

        def send_angles(self, angles, speed, _async=False):
            self.sent.append(angles)

    robot = object.__new__(Fake)
    robot.sent = []
    calls = 0

    def check(target, elapsed):
        nonlocal calls
        calls += 1
        return [1.0, 0, 0, 0, 0, 0] if calls == 1 else target

    path = [(0.0, [0.0]*6), (0.1, [3.0, 0, 0, 0, 0, 0])]
    count = robot.send_timed_angles(path, 10, period_s=0.25, lead_s=0.1,
                                    check=check)
    assert count == 2
    assert robot.sent[0][0] == 1.0
    assert robot.sent[-1][0] == 3.0


def test_checked_final_target_finishes_without_waiting_for_planned_time(tmp_path):
    base = patched_class(tmp_path)

    class Fake(base):
        def get_fresh_mode(self):
            return 1

        def send_angles(self, angles, speed, _async=False):
            self.sent.append(angles)

    robot = object.__new__(Fake)
    robot.sent = []
    path = [(0.0, [0.0]*6), (10.0, [3.0, 0, 0, 0, 0, 0])]
    count = robot.send_timed_angles(
        path, 10, lead_s=0.1,
        check=lambda target, elapsed: [3.0, 0, 0, 0, 0, 0])
    assert count == 1
    assert robot.sent == [[3.0, 0.0, 0.0, 0.0, 0.0, 0.0]]
