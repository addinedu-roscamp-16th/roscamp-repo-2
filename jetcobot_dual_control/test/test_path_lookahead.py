"""Geometric behavior of the optional refresh trial; no robot access."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'scripts'))
from path_lookahead import path_geometry, position_at, project_forward


def degrees(values):
    return tuple(math.radians(value) for value in values)


def test_target_advances_through_a_bend_without_waiting_for_vertex():
    points = [(0, degrees((0, 0))), (1, degrees((10, 0))),
              (2, degrees((10, 10)))]
    positions, cumulative = path_geometry(points)
    progress, deviation = project_forward(positions, cumulative, degrees((8, 0)))
    target = position_at(positions, cumulative, progress + math.radians(5))
    assert math.degrees(progress) == pytest.approx(8)
    assert math.degrees(deviation) == pytest.approx(0)
    assert tuple(map(math.degrees, target)) == pytest.approx((10, 3))


def test_projection_reports_corridor_error_and_never_rewinds():
    points = [(0, degrees((0, 0))), (1, degrees((10, 0))),
              (2, degrees((10, 10)))]
    positions, cumulative = path_geometry(points)
    progress, error = project_forward(positions, cumulative, degrees((7, 3)))
    assert math.degrees(error) == pytest.approx(3)
    later_progress, _ = project_forward(positions, cumulative, degrees((6.5, 0)), progress)
    assert later_progress == pytest.approx(progress)


def test_self_crossing_does_not_jump_far_ahead():
    points = [(0, degrees((0, 0))), (1, degrees((10, 0))),
              (2, degrees((10, 10))), (3, degrees((0, 10))),
              (4, degrees((0, 0)))]
    positions, cumulative = path_geometry(points)
    progress, _ = project_forward(positions, cumulative, degrees((0, 0)))
    assert progress == pytest.approx(0)
