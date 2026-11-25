"""IR-30 구조: 코어 그룹(틱, heartbeat, /fleet/robots, 서버)만 코어를 부르고, 받은 편지함 그룹(구독, 액션 · 서비스 응답)은
큐에 넣기만 한다. 큐에서 꺼낸 순서가 코어 입력 기록 순서이고, 그 기록을 재생하면 같은 판단이 나온다."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_core.common.contract import to_ros_qos  # noqa: E402
from fleet_interfaces.msg import Order, OrderBoard, OrderItem, RobotState, SystemMode  # noqa: E402
from fleet_interfaces.srv import SetMode  # noqa: E402
from fleet_manager.fleet_manager_node import FleetManagerNode, make_executor  # noqa: E402
from rclpy.action import ActionClient  # noqa: E402
from rclpy.callback_groups import ReentrantCallbackGroup  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402

REPLAY = Path(__file__).parents[3] / "tools" / "replay.py"

pytestmark = pytest.mark.skipif(
    os.environ.get("ROS_DOMAIN_ID", "") in ("", "6")
    or os.environ.get("ROS_AUTOMATIC_DISCOVERY_RANGE") != "LOCALHOST",
    reason="set ROS_DOMAIN_ID (not 6) and ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST first")


class CoreSpy:
    """코어를 감싸 부른 메서드와, 그때 코어 그룹 콜백 안이었는지를 남긴다."""

    def __init__(self, core, node):
        self._core, self._node, self.calls = core, node, []

    def __getattr__(self, name):
        value = getattr(self._core, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            self.calls.append((name, threading.get_ident() == self._node._core_thread))
            return value(*args, **kwargs)
        return call


class World(Node):
    """상태를 내고 set_mode를 거절하는 Pode 하나와 주문판."""

    def __init__(self, contract):
        super().__init__("inbox_world")
        group = ReentrantCallbackGroup()
        self.state = self.create_publisher(
            RobotState, contract.name("robot_state", pode="pode1"),
            to_ros_qos(contract.qos("robot_state")))
        self.system = self.create_publisher(
            SystemMode, contract.name("system_mode"), to_ros_qos(contract.qos("system_mode")))
        self.board = self.create_publisher(
            OrderBoard, contract.name("order_board"), to_ros_qos(contract.qos("order_board")))
        self.asked = []
        self.create_timer(0.1, self._publish, callback_group=group)
        self.create_service(SetMode, contract.name("robot_set_mode", robot="pode1"),
                            self._on_set_mode, callback_group=group)

    def _publish(self):
        self.state.publish(RobotState(robot_id="pode1", state="IDLE", mode="UNKNOWN",
                                      boot_id="b1"))

    def _on_set_mode(self, request, response):
        self.asked.append(request.request_id)
        response.accepted, response.reason = False, "MODE_MISMATCH"
        response.request_id = request.request_id
        return response


@pytest.fixture
def node():
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", "tick_hz:=50.0"])
    made = FleetManagerNode()
    yield made
    made.destroy_node()
    rclpy.shutdown()


def board(mode, order_id="", counts=None):
    orders = []
    if order_id:
        orders = [Order(order_id=order_id,
                        items=[OrderItem(kind=k, count=c) for k, c in (counts or {}).items()])]
    return OrderBoard(run_mode=mode, orders=orders)


def callbacks_of(node, topic):
    return [s.callback for s in node.subscriptions if s.topic_name == topic]


def wait_for(predicate, timeout_s):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_callbacks_sit_in_the_core_or_the_inbox_group(node):
    """틱 · heartbeat · /fleet/robots · fleet 서버는 코어 그룹, 구독과 응답을 받는 클라이언트는 받은 편지함 그룹."""
    core, inbox = node._core_group, node._inbox_group
    assert core is not inbox
    timers = list(node.timers)
    assert len(timers) == 3 and all(t.callback_group is core for t in timers)
    served = [s for s in node.services if s.srv_name.startswith("/fleet/")]
    assert len(served) >= 2 and all(s.callback_group is core for s in served)
    subs = list(node.subscriptions)
    assert subs and all(s.callback_group is inbox for s in subs)
    clients = list(node.clients)
    assert clients and all(c.callback_group is inbox for c in clients)
    actions = [w for w in node.waitables if isinstance(w, ActionClient)]
    assert actions and all(a.callback_group is inbox for a in actions)


def test_inbox_callbacks_only_queue_and_the_tick_hands_them_to_the_core(node):
    spy = CoreSpy(node.core, node)
    node.core = spy
    contract = node.contract
    [on_board] = callbacks_of(node, contract.name("order_board"))
    [on_mode] = callbacks_of(node, contract.name("system_mode"))
    [on_state] = callbacks_of(node, contract.name("robot_state", pode="pode1"))
    on_board(board("RUN"))
    on_mode(SystemMode(mode="TEST"))
    on_state(RobotState(robot_id="pode1", state="IDLE", mode="TEST", boot_id="b1"))
    assert spy.calls == []
    assert node._mode_sync.system_mode is None
    assert node._inbox.qsize() == 3
    node._on_tick()
    assert node._inbox.qsize() == 0
    assert ("on_order_board", True) in spy.calls
    assert node._mode_sync.system_mode == "TEST"


def test_drained_events_are_recorded_in_order_and_replay_the_same(tmp_path):
    record = tmp_path / "fms.jsonl"
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", "tick_hz:=50.0"])
    node = FleetManagerNode(record=record)
    try:
        [on_board] = callbacks_of(node, node.contract.name("order_board"))
        for msg in (board("PAUSE"), board("RUN", "O-1", {"A": 1}), board("RUN", "O-2", {"C": 2})):
            on_board(msg)
        before = record.read_text(encoding="utf-8")
        node._silent_until = 0.0  # 시작 침묵을 건너뛰어 코어 틱까지 돈다
        node._on_tick()
    finally:
        node.destroy_node()
        rclpy.shutdown()
    assert '"in"' not in before
    inputs = [e for e in map(json.loads, record.read_text(encoding="utf-8").splitlines())
              if "in" in e]
    assert [e["in"] for e in inputs[:3]] == ["on_order_board"] * 3
    assert [e["args"][0] for e in inputs[:3]] == ["PAUSE", "RUN", "RUN"]
    assert "O-1" in json.dumps(inputs[1]["args"]) and "O-2" in json.dumps(inputs[2]["args"])
    assert inputs[3]["in"] == "tick"
    played = subprocess.run([sys.executable, str(REPLAY), str(record)], capture_output=True,
                            text=True, timeout=120)
    assert played.returncode == 0, played.stdout + played.stderr
    assert "same as recorded" in played.stdout


def test_core_calls_happen_only_inside_core_callbacks_while_running():
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]", "-p", "tick_hz:=50.0"])
    node = FleetManagerNode()
    spy = CoreSpy(node.core, node)
    node.core = spy
    world = World(node.contract)
    executors = [make_executor(), MultiThreadedExecutor(num_threads=4)]
    executors[0].add_node(node)
    executors[1].add_node(world)
    threads = [threading.Thread(target=e.spin, daemon=True) for e in executors]
    for thread in threads:
        thread.start()
    try:
        world.board.publish(board("RUN"))
        world.system.publish(SystemMode(mode="TEST"))
        assert wait_for(lambda: any(n == "set_mode_blocked" for n, _ in spy.calls), 20.0), \
            (world.asked, sorted({n for n, _ in spy.calls}))
    finally:
        for executor, thread in zip(executors, threads, strict=True):
            executor.shutdown()
            thread.join(timeout=5)
        world.destroy_node()
        node.destroy_node()
        rclpy.shutdown()
    names = {name for name, _ in spy.calls}
    assert {"tick", "on_order_board", "set_mode_blocked"} <= names, sorted(names)
    outside = [name for name, inside in spy.calls if not inside]
    assert outside == []


def test_a_slow_tick_is_warned_once_in_a_while(monkeypatch):
    logged = []

    class Recorder:
        def get_child(self, _name):
            return self

        def __getattr__(self, level):
            return lambda text, **_: logged.append((level, text))

    monkeypatch.setattr(FleetManagerNode, "get_logger", lambda self: Recorder())
    rclpy.init(args=["--ros-args", "-p", "robots:=[pode1]"])
    try:
        node = FleetManagerNode()
        try:
            node._tick_period_s = 0.0
            node._on_tick()
            node._on_tick()
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()
    slow = [text for level, text in logged if level == "warning" and "tick took" in text]
    assert len(slow) == 1, logged
