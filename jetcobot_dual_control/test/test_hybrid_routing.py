"""The hybrid gate must reserve endpoint execution for separated straight plans."""
import math
from pathlib import Path

from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint

from jetcobot_dual_control.clearance import ArmClearance
from jetcobot_dual_control.execution_gate import ExecutionGate
from jetcobot_dual_control.model import build_model


ROOT = Path(__file__).resolve().parents[2]


def model_share(package):
    if package == 'jetcobot_description':
        return ROOT / 'jetcobot_ros2/jetcobot_description'
    return ROOT / 'jetcobot_gazebo'


class Probe:
    hybrid_route = ExecutionGate.hybrid_route
    def __init__(self, clearance):
        self.clearance = clearance
    def get_logger(self):
        return self
    def info(self, message):
        pass


def request(start, end, bend=False):
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [f'left_{i}_Joint' for i in range(1, 7)]
    start_point = JointTrajectoryPoint(positions=start)
    end_point = JointTrajectoryPoint(positions=end)
    end_point.time_from_start.sec = 2
    goal.trajectory.points = [start_point, end_point]
    if bend:
        middle = JointTrajectoryPoint(positions=[(a+b)/2 for a, b in zip(start, end)])
        middle.positions[1] += .01
        middle.time_from_start.sec = 1
        goal.trajectory.points.insert(1, middle)
    return goal


def test_far_straight_uses_endpoint_close_or_curved_uses_path():
    model = build_model(model_share('jetcobot_gazebo'),
                        ROOT / 'jetcobot_dual_control/config/layout.yaml', model_share)
    gate = Probe(ArmClearance(model['robot_description']))
    far = [math.radians(45), 0, 0, 0, 0, 0]
    far_end = [math.radians(48), 0, 0, 0, 0, 0]
    peer = [math.radians(-45), 0, 0, 0, 0, 0]
    assert gate.hybrid_route(request(far, far_end), 'left', far, peer) == 'endpoint'
    assert gate.hybrid_route(request(far, far_end, bend=True), 'left', far, peer) == 'path'
    near = [0]*6
    near_end = [math.radians(3), 0, 0, 0, 0, 0]
    assert gate.hybrid_route(request(near, near_end), 'left', near, near) == 'path'
    gate.clearance = None
    assert gate.hybrid_route(request(far, far_end), 'left', far, peer) == 'path'
