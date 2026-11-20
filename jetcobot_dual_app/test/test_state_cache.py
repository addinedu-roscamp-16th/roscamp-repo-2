"""Offline CDR cache checks; no MoveIt service or robot motion is started."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from moveit_msgs.msg import MotionPlanResponse
from trajectory_msgs.msg import JointTrajectoryPoint

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'right_place_trial'))
from coordinated_right_case import (ALL_JOINTS, load_checked_plan,
                                    save_checked_plan)  # noqa: E402


class FakeCoordinator:
    def __init__(self, valid=True):
        self.is_valid = valid
        self.checked = 0
        self.connections = 0

    def valid(self, state, group_name=''):
        return self.is_valid and group_name == 'both_arms'

    def check_trajectory(self, trajectory, state, group_name='',
                         geometric_only=False):
        assert group_name == 'both_arms' and geometric_only
        self.checked += 1

    def valid_many(self, states, group_name=''):
        assert group_name == 'both_arms'
        self.connections += 1
        return self.is_valid


class CacheTests(unittest.TestCase):
    def test_saved_plan_is_rechecked_and_stale_start_rejected(self):
        start = {name: 0.0 for name in ALL_JOINTS}
        end = {name: 0.1 for name in ALL_JOINTS}
        result = MotionPlanResponse()
        path = result.trajectory.joint_trajectory
        path.joint_names = list(ALL_JOINTS)
        path.points = [JointTrajectoryPoint(positions=[0.0] * 12),
                       JointTrajectoryPoint(positions=[0.1] * 12)]
        phase = {'side': 'both', 'goal': end, 'label': 'case',
                 'start': start, 'end': end, 'result': result}
        case_xy = np.array([-.23, .155])
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            save_checked_plan(folder, 'right', 'chocobar', case_xy, 76.0,
                              'paired', [phase])
            coordinator = FakeCoordinator()
            strategy, phases = load_checked_plan(
                folder, coordinator, start, 'right', 'chocobar', case_xy, 76.0)
            self.assertEqual((strategy, len(phases), coordinator.checked),
                             ('paired', 1, 1))
            nearby = dict(start, right_1_Joint=0.03)
            coordinator = FakeCoordinator()
            load_checked_plan(folder, coordinator, nearby, 'right',
                              'chocobar', case_xy, 76.0)
            self.assertEqual(coordinator.connections, 1)
            stale = dict(start, right_1_Joint=0.2)
            with self.assertRaisesRegex(RuntimeError, 'start differs'):
                load_checked_plan(folder, FakeCoordinator(), stale, 'right',
                                  'chocobar', case_xy, 76.0)
            with self.assertRaisesRegex(RuntimeError, 'target does not match'):
                load_checked_plan(folder, FakeCoordinator(), start, 'left',
                                  'chocobar', case_xy, 76.0)
            with self.assertRaisesRegex(RuntimeError, 'current arms collide'):
                load_checked_plan(folder, FakeCoordinator(valid=False), start,
                                  'right', 'chocobar', case_xy, 76.0)


if __name__ == '__main__':
    unittest.main()
