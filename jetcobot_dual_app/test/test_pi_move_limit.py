"""11-27: PC skips approach candidates the Pi would reject (> 180 deg per joint in one goal)."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'moveit_pick'))


class PiMoveLimitTests(unittest.TestCase):
    def setUp(self):
        try:
            import dual_arm_coordinator_real as coordinator
        except ImportError as exc:  # needs rclpy / moveit messages
            self.skipTest(f'coordinator import failed: {exc}')
        self.c = coordinator

    def trajectory(self, *j3_deg):
        return SimpleNamespace(joint_names=['left_3_Joint', 'left_1_Joint'],
                               points=[SimpleNamespace(positions=[math.radians(v), 0.0]) for v in j3_deg])

    def test_limit_matches_pi(self):
        self.assertEqual(self.c.PI_MAX_MOVE_DEG, 180.0)
        start = {'left_3_Joint': math.radians(-72.9), 'left_1_Joint': 0.0}
        # 11-27 11:41 run: J3 -72.9 deg -> beyond +107 deg rejected by the Pi
        self.assertEqual(self.c.pi_move_limit_error(self.trajectory(-50, 110), start), 'left_3_Joint')
        self.assertIsNone(self.c.pi_move_limit_error(self.trajectory(-50, 100), start))

    def test_no_motion_marker_class(self):
        self.assertTrue(issubclass(self.c.GoalRejectedNoMotion, RuntimeError))


if __name__ == '__main__':
    unittest.main()
