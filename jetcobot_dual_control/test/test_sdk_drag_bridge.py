"""Exercise drag action routing with synthetic feedback; no serial device."""
import math
import threading
import time
from types import SimpleNamespace

import sdk_drag_bridge


class FakeHandle:
    is_cancel_requested = False
    is_active = True

    def __init__(self):
        self.request = object()
        self.succeeded = False

    def publish_feedback(self, feedback):
        pass

    def succeed(self):
        self.succeeded = True

    def abort(self):
        raise AssertionError('Synthetic drag path should succeed')


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.actual = (0.,)*6

    def _mesg(self, genre, encoders, speeds, _async):
        assert genre == sdk_drag_bridge.ProtocolCode.SET_ENCODERS_DRAG
        assert _async is True
        self.commands.append((encoders, speeds))
        signs = (-1, -1, 1, -1, -1, -1)
        self.actual = tuple(math.radians((value-2048)*360/4096/sign)
                            for value, sign in zip(encoders, signs))


class FakeBridge:
    def __init__(self, points):
        self.points = points
        self.mc = FakeSerial()
        self.arm_names = tuple(f'{i}_Joint' for i in range(1, 7))
        self.step_deg = 5.
        self.deviation_deg = 1.5
        self.speed = 60
        self.interval = 0.
        self.lead_deg = 12.
        self.corridor_deg = 6.
        self.goal_tolerance_deg = 5.
        self.max_move_deg = 180.
        self.guard = threading.Lock()
        self.busy = True
        self.samples = 0

    def validated(self, _):
        return self.points

    def current(self):
        return self.points[0][1]

    def current_sample(self):
        self.samples += 1
        return time.monotonic()+self.samples*.001, self.mc.actual

    def get_clock(self):
        return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: SimpleNamespace()))

    def get_logger(self):
        return SimpleNamespace(warning=lambda _: None, info=lambda _: None, error=lambda _: None)

    def stop_arm(self):
        raise AssertionError('Synthetic success should not request stop')


def test_curved_moveit_path_sends_intermediate_drag_packets(monkeypatch):
    monkeypatch.setattr(sdk_drag_bridge.rclpy, 'ok', lambda: True)
    monkeypatch.setattr(sdk_drag_bridge.time, 'sleep', lambda _: None)
    q = lambda a, b: (math.radians(a), math.radians(b), 0., 0., 0., 0.)
    bridge = FakeBridge([(0., q(0, 0)), (1., q(5, 0)),
                         (2., q(5, 5)), (3., q(10, 5))])
    handle = FakeHandle()
    result = sdk_drag_bridge.SDKDragBridge.execute(bridge, handle)
    assert result.error_code == 0 and handle.succeeded
    assert len(bridge.mc.commands) == 3
    assert bridge.mc.commands[0][1] == [60, 0, 0, 0, 0, 0]
    assert bridge.mc.commands[1][1] == [0, 60, 0, 0, 0, 0]
    assert bridge.mc.commands[2][1] == [60, 0, 0, 0, 0, 0]
    assert bridge.busy is False
