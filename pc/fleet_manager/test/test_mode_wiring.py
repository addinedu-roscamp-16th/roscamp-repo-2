"""운영 모드(IR-73)를 실제 그래프로 본다: 모드를 받기 전에는 부르지 않고, 받으면 Pode의 set_mode를 부르며,
거절하는 Pode는 코어에 배정 제외로 알린다. 그래프에 참여하므로 로봇 네트워크와 분리된 도메인에서만 돈다."""
import os
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.commands import CancelCmd, FillCmd  # noqa: E402
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


# ---- 담기 목표: 작업대 팔이 시스템 모드를 보고하기 전에는 보내지 않는다 (IR-72, PI-20) ----
FILL = FillCmd(goal_id=7, robot="pode1", station_id="WORK", order_id="O-1", items={"choco_bar": 1})


def node_with_spied_sends():
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]"])
    fms = FleetManagerNode()
    sent, results, given_up = [], [], set()
    fms._send = lambda client, goal, key, *args, **kwargs: sent.append(key)
    fms.core.arm.holds = lambda goal_id: goal_id not in given_up
    fms.core.on_fill_result = (
        lambda goal_id, success, placed, missing, reason="", **kwargs:
        results.append((goal_id, success, reason)))

    def stop():
        fms.destroy_node()
        rclpy.shutdown()

    return fms, sent, results, given_up, stop


def arms_report(fms, left, right):
    now = time.monotonic()
    fms._mode_sync.on_state("arm_left", left, "b-left", now)
    fms._mode_sync.on_state("arm_right", right, "b-right", now)


def test_a_fill_waits_until_both_arms_report_the_system_mode():
    fms, sent, _, _, stop = node_with_spied_sends()
    try:
        fms._mode_sync.on_system_mode("TEST")
        arms_report(fms, "TEST", "UNKNOWN")
        fms._dispatch(FILL, time.monotonic())
        fms._release_fills()
        assert sent == []
        arms_report(fms, "TEST", "TEST")
        fms._release_fills()
        fms._release_fills()
        assert sent == [("workcell", 7)]
    finally:
        stop()


def test_a_fill_goes_at_once_while_the_system_mode_is_unknown():
    fms, sent, _, _, stop = node_with_spied_sends()
    try:
        arms_report(fms, "UNKNOWN", "UNKNOWN")
        fms._dispatch(FILL, time.monotonic())
        assert sent == [("workcell", 7)]
    finally:
        stop()


def test_a_held_fill_that_is_canceled_ends_as_canceled_and_is_never_sent():
    fms, sent, results, _, stop = node_with_spied_sends()
    try:
        fms._mode_sync.on_system_mode("TEST")
        arms_report(fms, "UNKNOWN", "UNKNOWN")
        fms._dispatch(FILL, time.monotonic())
        fms._dispatch(CancelCmd("workcell", 7), time.monotonic())
        arms_report(fms, "TEST", "TEST")
        fms._release_fills()
        assert sent == [] and results == [(7, False, "CANCELED")]
    finally:
        stop()


def test_a_held_fill_the_core_gave_up_on_is_dropped_and_never_sent():
    fms, sent, results, given_up, stop = node_with_spied_sends()
    try:
        fms._mode_sync.on_system_mode("TEST")
        arms_report(fms, "UNKNOWN", "UNKNOWN")
        fms._dispatch(FILL, time.monotonic())
        given_up.add(7)  # 팔 ERROR로 코어가 작업을 포기함 (취소 명령 없음)
        arms_report(fms, "TEST", "TEST")
        fms._release_fills()
        assert sent == [] and results == [] and fms._held_fills == []
    finally:
        stop()
