"""Check the proposed drag conversion against both measured robots."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'jetcobot_dual_bridge' / 'scripts'))
from drag_trajectory import (angles_to_encoders, check_stationary_mapping,
                             trajectory_to_drag, velocities_to_servo_speeds,
                             segment_drag_command)


@pytest.mark.parametrize('angles,encoders', [
    ([-22.76, -14.15, -41.22, -13.88, 1.66, 7.91],
     [2307, 2209, 1579, 2206, 2029, 1958]),  # left
    ([-0.26, -0.96, -0.87, -1.75, 0.7, -0.52],
     [2051, 2059, 2038, 2068, 2040, 2054]),  # right
])
def test_read_only_mapping_matches_both_arms(angles, encoders):
    assert angles_to_encoders(tuple(map(math.radians, angles))) == tuple(encoders)
    assert check_stationary_mapping(angles, encoders) == (0,)*6


def test_joint_velocity_converts_to_encoder_steps_per_second():
    velocities = (math.radians(1), -math.radians(2), 0, 0, 0, 0)
    assert velocities_to_servo_speeds(velocities) == (11, 23, 0, 0, 0, 0)


def test_out_of_range_joint_is_rejected_before_drag_encoding():
    with pytest.raises(ValueError, match='firmware angle limit'):
        angles_to_encoders((math.radians(169), 0, 0, 0, 0, 0))


def test_timed_trajectory_conversion_preserves_times_and_derives_speed():
    points = [(0.0, (0,)*6, ()),
              (1.0, (math.radians(2), 0, 0, 0, 0, 0), ()),
              (2.0, (math.radians(4), 0, 0, 0, 0, 0), ())]
    samples = trajectory_to_drag(points)
    assert [row[0] for row in samples] == [0.0, 1.0, 2.0]
    assert [row[1][0] for row in samples] == [2048, 2025, 2002]
    assert [row[2][0] for row in samples] == [23, 23, 23]


def test_multijoint_segment_retains_relative_motion():
    start = (0,)*6
    target = tuple(map(math.radians, (5, -2.5, 0, 0, 0, 0)))
    encoders, speeds = segment_drag_command(start, target, 60)
    assert encoders[:2] == (1991, 2076)
    assert speeds == (60, 30, 0, 0, 0, 0)
