"""받아들인 goal의 결과를 잃었을 때 노드가 코어에 넘기는 길(IR-62)."""
from types import SimpleNamespace

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("fleet_interfaces")

from fleet_manager.fleet_manager_node import FleetManagerNode  # noqa: E402
from fleet_manager.goal_tracker import GoalTracker  # noqa: E402


def test_a_lost_result_of_a_robot_goal_is_no_rejection():
    """로봇 goal의 결과를 잃으면 끝났는지 모른다. 거절로 넘기지 않는다.

    취소한 goal은 취소를 남긴다(코어는 막힘 표시 때 취소한 goal이 끝나야 구역을 놓는다).
    취소하지 않은 goal은 코어에 결과 모름(on_goal_lost)으로 넘긴다. 팔 goal은 그대로 거절이다.
    """
    def broken():
        raise RuntimeError("result lost")

    def robot_rejected(key):
        rejected.append(key)

    def arm_rejected(key):
        rejected.append(key)

    key = ("pode1", 7)
    for canceled, on_rejected, want_rejected, want_lost in (
            (True, robot_rejected, [], []),
            (False, robot_rejected, [], [key]),
            (True, arm_rejected, [key], [])):
        goals = GoalTracker()
        goals.sent(key)
        goals.accepted(key, SimpleNamespace(cancel_goal_async=lambda: None))
        if canceled:
            goals.cancel(key, 0.0)
        lost = []
        node = SimpleNamespace(_goals=goals, _robot_rejected=robot_rejected,
                               core=SimpleNamespace(on_goal_lost=lambda *k: lost.append(k)),
                               get_logger=lambda: SimpleNamespace(error=lambda msg: None))
        rejected = []
        FleetManagerNode._goal_result(node, key, lambda k, r: None, on_rejected,
                                      SimpleNamespace(result=broken))
        assert (rejected, lost) == (want_rejected, want_lost)
        assert goals.canceling(key) == (canceled and on_rejected is robot_rejected)
        if want_lost:  # 코어가 나중에 내는 취소는 아직 로봇에 닿는다
            asked = []
            goals._handles[key] = SimpleNamespace(cancel_goal_async=lambda: asked.append(1))
            goals.cancel(key, 1.0)
            assert asked == [1]
