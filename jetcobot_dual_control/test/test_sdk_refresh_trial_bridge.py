"""Pure trial policy checks; no serial device is opened."""
import math
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'scripts'))
from sdk_refresh_trial_bridge import SDKRefreshTrialBridge


def test_refresh_trial_requires_measured_progress_for_small_goal():
    bridge = SimpleNamespace(goal_tolerance_deg=5.0)
    initial = (0.0,) * 6
    target = (math.radians(3.0),) + (0.0,) * 5
    tolerance = SDKRefreshTrialBridge.goal_tolerance_for(bridge, initial, target)
    assert math.degrees(tolerance) == pytest.approx(1.5)
    assert abs(initial[0] - target[0]) > tolerance
