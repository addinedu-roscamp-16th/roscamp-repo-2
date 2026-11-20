import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'scripts'))
from trajectory_waypoints import (select_waypoints, segment_deviation,
                                  effective_goal_tolerance_deg)


def pt(t, *deg):
    return (float(t), tuple(math.radians(v) for v in deg))


def test_sparse_selection_preserves_a_curve_and_bounds_step():
    path = [pt(0, 0, 0, 0, 0, 0, 0),
            pt(1, 1, 0, 0, 0, 0, 0),
            pt(2, 2, 0, 0, 0, 0, 0),
            pt(3, 3, 1.5, 0, 0, 0, 0),
            pt(4, 4, 3, 0, 0, 0, 0),
            pt(5, 5, 3, 0, 0, 0, 0)]
    selected = select_waypoints(path, max_step_deg=3, max_deviation_deg=.5)
    assert selected[0] == path[0] and selected[-1] == path[-1]
    assert len(selected) >= 3  # The bend cannot collapse to a single segment.
    assert all(max(abs(a-b) for a, b in zip(x[1], y[1])) <= math.radians(3)+1e-12
               for x, y in zip(selected, selected[1:]))


def test_segment_deviation_flags_off_path_joint_feedback():
    a = pt(0, 0, 0, 0, 0, 0, 0)[1]
    b = pt(1, 4, 0, 0, 0, 0, 0)[1]
    inside = pt(.5, 2, 0, 0, 0, 0, 0)[1]
    outside = pt(.5, 2, 7, 0, 0, 0, 0)[1]
    assert segment_deviation(inside, a, b) < 1e-12
    assert segment_deviation(outside, a, b) >= math.radians(7)-1e-12


def test_bad_spacing_rejected():
    with pytest.raises(ValueError):
        select_waypoints([pt(0, 0, 0, 0, 0, 0, 0)], 4, .5)
    with pytest.raises(ValueError):
        select_waypoints([pt(0, 0, 0, 0, 0, 0, 0), pt(1, 1, 0, 0, 0, 0, 0)], 1, 1)


def test_time_warped_straight_joint_path_is_one_command():
    # MoveIt retiming changes velocity, not joint-space geometry.
    path = [pt(0, 0, 0, 0, 0, 0, 0),
            pt(.1, .02, 0, 0, 0, 0, 0),
            pt(.5, .25, 0, 0, 0, 0, 0),
            pt(1, 1.0, 0, 0, 0, 0, 0),
            pt(2, 3.0, 0, 0, 0, 0, 0)]
    assert select_waypoints(path, 4, .5) == [path[0], path[-1]]

def test_reversal_is_kept_even_when_endpoints_match():
    path = [pt(0, 0, 0, 0, 0, 0, 0),
            pt(1, 2, 0, 0, 0, 0, 0),
            pt(2, 0, 0, 0, 0, 0, 0)]
    assert select_waypoints(path, 4, .5) == path


def test_small_goal_cannot_succeed_without_measured_progress():
    start = pt(0, 0, 0, 0, 0, 0, 0)[1]
    goal = pt(1, 2, 0, 0, 0, 0, 0)[1]
    assert effective_goal_tolerance_deg(start, goal, 5) == pytest.approx(1)
    far = pt(1, 20, 0, 0, 0, 0, 0)[1]
    assert effective_goal_tolerance_deg(start, far, 5) == pytest.approx(5)
