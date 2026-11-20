"""No serial device is opened; test the action loop with synthetic feedback."""
import math
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'scripts'))
import sdk_path_bridge


class FakeHandle:
    is_cancel_requested = False
    is_active = True

    def __init__(self):
        self.feedback = []
        self.succeeded = False
        self.request = object()

    def publish_feedback(self, value):
        self.feedback.append(value)

    def succeed(self):
        self.succeeded = True

    def abort(self):
        raise AssertionError('Synthetic path should not abort')


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.speeds = []
        self.queue = []
        self.target = [0.] * 6

    def send_angles(self, values, speed, _async):
        assert 1 <= speed <= 100 and _async is True
        self.speeds.append(speed)
        self.commands.append(values)
        self.queue.append([math.radians(v) for v in values])
        self.target = [math.radians(v) for v in values]


class FakeBridge:
    def __init__(self, points):
        self.points = points
        self.mc = FakeSerial()
        self.arm_names = [f'{i}_Joint' for i in range(1, 7)]
        self.path_step_deg = 4.
        self.path_sdk_speed = 10
        self.path_deviation_deg = .5
        self.waypoint_tolerance_deg = 2.
        self.min_command_interval_s = 0.
        self.queue_lookahead = False
        self.commands_at_first_sample = None
        self.goal_tolerance_deg = 5.
        self.max_move_deg = 180.
        self.guard = threading.Lock()
        self.busy = True
        self.sample_count = 0
        self.messages = []

    def validated(self, request):
        return self.points

    def current(self):
        return self.points[0][1]

    def current_sample(self):
        self.sample_count += 1
        if self.commands_at_first_sample is None:
            self.commands_at_first_sample = len(self.mc.commands)
        if self.queue_lookahead and self.mc.queue:
            self.mc.target = self.mc.queue.pop(0)
        return time.monotonic() + self.sample_count * .001, tuple(self.mc.target)

    def get_clock(self):
        return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: SimpleNamespace()))

    def get_logger(self):
        return SimpleNamespace(warning=self.messages.append, info=self.messages.append,
                               error=self.messages.append)

    def stop_arm(self):
        raise AssertionError('Synthetic success must not stop the arm')


def test_sparse_path_sends_each_waypoint_once_and_waits_for_measured_feedback(monkeypatch):
    monkeypatch.setattr(sdk_path_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_path_bridge.time, 'sleep', lambda _: None)
    points = [(float(i), (math.radians(i), 0., 0., 0., 0., 0.)) for i in range(9)]
    bridge = FakeBridge(points)
    handle = FakeHandle()
    result = sdk_path_bridge.SDKPathBridge.execute(bridge, handle)
    assert handle.succeeded and result.error_code == 0
    assert [round(v[0]) for v in bridge.mc.commands] == [4, 8]
    assert len(handle.feedback) >= 3
    assert bridge.busy is False


def test_one_point_lookahead_queues_next_command_before_first_arrival(monkeypatch):
    monkeypatch.setattr(sdk_path_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_path_bridge.time, 'sleep', lambda _: None)
    points = [(float(i), (math.radians(i), 0., 0., 0., 0., 0.)) for i in range(9)]
    bridge = FakeBridge(points)
    bridge.queue_lookahead = True
    handle = FakeHandle()
    result = sdk_path_bridge.SDKPathBridge.execute(bridge, handle)
    assert handle.succeeded and result.error_code == 0
    assert [round(v[0]) for v in bridge.mc.commands] == [4, 8]
    assert bridge.commands_at_first_sample == 2


def test_lookahead_accepts_measured_passage_on_next_planned_segment(monkeypatch):
    monkeypatch.setattr(sdk_path_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_path_bridge.time, 'sleep', lambda _: None)
    points = [(float(i), (math.radians(i), 0., 0., 0., 0., 0.)) for i in range(9)]

    class SkippingBridge(FakeBridge):
        def current_sample(self):
            self.sample_count += 1
            if self.commands_at_first_sample is None:
                self.commands_at_first_sample = len(self.mc.commands)
            # Mode 0 can move into the queued segment between feedback samples.
            self.mc.queue.clear()
            self.mc.target = list(points[-1][1])
            return time.monotonic() + self.sample_count*.001, tuple(self.mc.target)

    bridge = SkippingBridge(points)
    bridge.queue_lookahead = True
    handle = FakeHandle()
    result = sdk_path_bridge.SDKPathBridge.execute(bridge, handle)
    assert result.error_code == 0 and handle.succeeded
    assert bridge.commands_at_first_sample == 2
    assert any('passed on next segment' in message for message in bridge.messages)


def test_next_segment_progress_rejects_lateral_miss():
    from trajectory_waypoints import segment_progress
    start = [math.radians(4), 0., 0., 0., 0., 0.]
    end = [math.radians(8), 0., 0., 0., 0., 0.]
    actual = [math.radians(6), math.radians(5), 0., 0., 0., 0.]
    fraction, deviation = segment_progress(actual, start, end)
    assert fraction >= .20
    assert math.degrees(deviation) > 2.0


def test_two_point_lookahead_prefills_queue_and_accepts_fresh_progress(monkeypatch):
    monkeypatch.setattr(sdk_path_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_path_bridge.time, 'sleep', lambda _: None)
    points = [(float(i), (math.radians(i), 0., 0., 0., 0., 0.)) for i in range(13)]

    class FastFeedback(FakeBridge):
        def current_sample(self):
            self.sample_count += 1
            if self.commands_at_first_sample is None:
                self.commands_at_first_sample = len(self.mc.commands)
            self.mc.queue.clear()
            self.mc.target = [math.radians(v) for v in self.mc.commands[-1]]
            return time.monotonic() + self.sample_count*.001, tuple(self.mc.target)

    bridge = FastFeedback(points)
    bridge.queue_lookahead = True
    bridge.queue_lookahead_depth = 2
    handle = FakeHandle()
    result = sdk_path_bridge.SDKPathBridge.execute(bridge, handle)
    assert result.error_code == 0 and handle.succeeded
    assert bridge.commands_at_first_sample == 3
    from trajectory_waypoints import select_waypoints
    expected = [round(math.degrees(q[0])) for _, q in select_waypoints(points, 4., .5)[1:]]
    assert [round(v[0]) for v in bridge.mc.commands] == expected
    assert any('passed on next segment' in message for message in bridge.messages)


def test_two_point_stop_requests_stop_then_queue_clear():
    calls = []
    class Serial:
        def stop(self):
            calls.append('stop')
        def clear_queue(self):
            calls.append('clear_queue')
            return 1
    class Path(sdk_path_bridge.SDKPathBridge):
        def __init__(self):
            self.mc = Serial()
            self.queue_lookahead = True
            self.queue_lookahead_depth = 2
        def get_logger(self):
            return SimpleNamespace(warning=lambda _: None, error=lambda _: None)
    Path().stop_arm()
    assert calls == ['stop', 'clear_queue']


def test_configured_path_sdk_speed_is_sent_to_each_waypoint(monkeypatch):
    monkeypatch.setattr(sdk_path_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_path_bridge.time, 'sleep', lambda _: None)
    points = [(float(i), (math.radians(i), 0., 0., 0., 0., 0.)) for i in range(9)]
    bridge = FakeBridge(points)
    bridge.path_sdk_speed = 15
    handle = FakeHandle()
    result = sdk_path_bridge.SDKPathBridge.execute(bridge, handle)
    assert result.error_code == 0 and handle.succeeded
    assert bridge.mc.speeds == [15, 15]


def test_failed_queue_clear_latches_rejection_of_new_goal():
    class Serial:
        def stop(self):
            return 1
        def clear_queue(self):
            return -1
    class Path(sdk_path_bridge.SDKPathBridge):
        def __init__(self):
            self.mc = Serial()
            self.queue_lookahead = True
            self.queue_lookahead_depth = 2
            self.queue_stop_unconfirmed = False
        def get_logger(self):
            return SimpleNamespace(warning=lambda _: None, error=lambda _: None)
    bridge = Path()
    bridge.stop_arm()
    assert bridge.queue_stop_unconfirmed is True
    assert bridge.goal(object()) == sdk_path_bridge.GoalResponse.REJECT
