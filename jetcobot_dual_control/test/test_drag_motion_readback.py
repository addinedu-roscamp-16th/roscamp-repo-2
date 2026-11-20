"""Angle and encoder queries are sequential, so mid-motion pairs may differ."""
import pytest

from trial_drag_joint import read_position


class SequentialReadback:
    def get_angles(self):
        return [8.78, -0.96, -0.87, -5.0, 0.87, -0.26]

    def get_encoders(self):
        # J1 encoder was sampled later as the arm continued moving.
        return [1943, 2059, 2038, 2105, 2038, 2051]


def test_moving_readback_accepts_sequential_samples():
    angles, encoders = read_position(SequentialReadback(), verify_mapping=False)
    assert angles[0] == 8.78
    assert encoders[0] == 1943


def test_stationary_readback_still_checks_mapping():
    with pytest.raises(ValueError, match='mapping mismatch'):
        read_position(SequentialReadback())
