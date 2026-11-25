"""/system/mode 구독(F-13, TT-34, IR-73). 모드가 바뀔 때 로그를 남기고 로봇에 알린다."""
from types import SimpleNamespace

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_interfaces.msg import SystemMode  # noqa: E402
from fleet_manager.fleet_manager_node import FleetManagerNode  # noqa: E402
from fleet_manager.mode_sync import ModeSync  # noqa: E402
from rclpy.qos import DurabilityPolicy  # noqa: E402


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, text):
        self.lines.append(text)


def test_a_mode_is_logged_once_per_change():
    log = _Log()
    node = SimpleNamespace(_mode_sync=ModeSync(["pode1"]), get_logger=lambda: log)
    for mode in ("TEST", "TEST", "NORMAL"):
        FleetManagerNode._on_system_mode(node, SystemMode(mode=mode))
    assert log.lines == ["system mode TEST, telling the robots",
                         "system mode NORMAL, telling the robots"]


def test_the_node_listens_to_the_latched_system_mode():
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]"])
    try:
        fms = FleetManagerNode()
        subs = {sub.topic_name: sub for sub in fms.subscriptions}
        assert "/system/mode" in subs
        assert subs["/system/mode"].qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL
        fms.destroy_node()
    finally:
        rclpy.shutdown()
