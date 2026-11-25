"""운영 모드(IR-73)를 실제 그래프로 본다: 모드를 받기 전에는 부르지 않고, 받으면 Pode의 set_mode를 부르며,
거절하는 Pode는 코어에 배정 제외로 알린다. 그래프에 참여하므로 로봇 네트워크와 분리된 도메인에서만 돈다."""
import os
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.contract import to_ros_qos  # noqa: E402
from fleet_interfaces.msg import RobotState, SystemMode  # noqa: E402
from fleet_interfaces.srv import SetMode  # noqa: E402
from fleet_manager.fleet_manager_node import FleetManagerNode  # noqa: E402
from rclpy.callback_groups import ReentrantCallbackGroup  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("ROS_DOMAIN_ID", "") in ("", "6")
    or os.environ.get("ROS_AUTOMATIC_DISCOVERY_RANGE") != "LOCALHOST",
    reason="set ROS_DOMAIN_ID (not 6) and ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST first")


class Pode(Node):
    """상태를 내고 set_mode를 받는 Pode 하나. 받아들이면 그 모드를 상태에 싣는다."""

    def __init__(self, contract, accept):
        super().__init__("mode_pode")
        group = ReentrantCallbackGroup()
        self.mode, self.asked, self.accept = "UNKNOWN", [], accept
        self._state = self.create_publisher(
            RobotState, contract.name("robot_state", pode="pode1"),
            to_ros_qos(contract.qos("robot_state")))
        self._system = self.create_publisher(
            SystemMode, contract.name("system_mode"), to_ros_qos(contract.qos("system_mode")))
        self.create_timer(0.1, self._publish, callback_group=group)
        self.create_service(SetMode, contract.name("robot_set_mode", robot="pode1"),
                            self._on_set_mode, callback_group=group)

    def _publish(self):
        self._state.publish(RobotState(robot_id="pode1", state="IDLE", mode=self.mode,
                                       boot_id="b1"))

    def _on_set_mode(self, request, response):
        self.asked.append(request.mode)
        if self.accept:
            self.mode = request.mode
        response.accepted, response.request_id = self.accept, request.request_id
        response.reason = "" if self.accept else "MODE_MISMATCH"
        return response

    def send_system_mode(self, mode):
        self._system.publish(SystemMode(mode=mode))


def run(accept):
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", "tick_hz:=50.0"])
    fms = FleetManagerNode()
    blocks = []
    real = fms.core.set_mode_blocked

    def spy(robot, blocked, detail=""):
        blocks.append((robot, blocked))
        real(robot, blocked, detail)

    fms.core.set_mode_blocked = spy
    pode = Pode(fms.contract, accept)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(fms)
    executor.add_node(pode)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()

    def stop():
        executor.shutdown()
        thread.join(timeout=5)
        pode.destroy_node()
        fms.destroy_node()
        rclpy.shutdown()

    return pode, blocks, stop


def wait_for(predicate, timeout_s):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_nothing_is_asked_before_the_system_mode_comes():
    pode, blocks, stop = run(accept=True)
    try:
        time.sleep(3.0)
        assert pode.asked == [] and blocks == []
    finally:
        stop()


def test_a_pode_that_takes_the_mode_stays_in_service():
    pode, blocks, stop = run(accept=True)
    try:
        pode.send_system_mode("TEST")
        assert wait_for(lambda: pode.asked == ["TEST"], 10.0), pode.asked
        time.sleep(5.0)
        assert pode.asked == ["TEST"] and blocks == []
    finally:
        stop()


def test_a_pode_that_refuses_the_mode_is_taken_off_new_trips():
    pode, blocks, stop = run(accept=False)
    try:
        pode.send_system_mode("TEST")
        assert wait_for(lambda: blocks == [("pode1", True)], 15.0), (pode.asked, blocks)
        assert len(pode.asked) >= 2
    finally:
        stop()
