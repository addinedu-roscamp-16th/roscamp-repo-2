import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_share_directory as share
import pytest

from jetcobot_dual_control.model import build_model, LIMITS_DEG, LEFT_LIMITS_DEG
from jetcobot_dual_control.state_aggregator import StateCache, ARM_NAMES

ROOT = Path(__file__).resolve().parents[1]


def test_model_layout_limits_and_simulation_removal():
    source = Path(share('jetcobot_gazebo')) / 'urdf/dual/jetcobot_dual.urdf'
    before = source.read_bytes()
    model = build_model(share('jetcobot_gazebo'), ROOT / 'config/layout.yaml', share)
    robot = ET.fromstring(model['robot_description'])
    semantic = ET.fromstring(model['robot_description_semantic'])
    assert not robot.findall('gazebo') and not robot.findall('ros2_control')
    assert source.read_bytes() == before
    assert robot.find("joint[@name='left_world_to_dummy']/origin").get('xyz') == '0.0 0.3 0.0'
    assert robot.find("joint[@name='right_world_to_dummy']/origin").get('xyz') == '0.0 0.0 0.0'
    names = [j.get('name') for j in robot.findall('joint')]
    assert len(names) == len(set(names))
    links = {link.get('name') for link in robot.findall('link')}
    for joint in robot.findall('joint'):
        assert joint.find('parent').get('link') in links
        assert joint.find('child').get('link') in links
        if joint.find('mimic') is not None:
            assert joint.find('mimic').get('joint') in names
    for side in ('left', 'right'):
        for i, (low, high) in enumerate(LEFT_LIMITS_DEG if side == 'left' else LIMITS_DEG, 1):
            name = f'{side}_{i}_Joint'
            limit = robot.find(f"joint[@name='{name}']/limit")
            assert float(limit.get('lower')) == math.radians(low)
            assert float(limit.get('upper')) == math.radians(high)
            assert model['robot_description_planning']['joint_limits'][name]['max_position'] == math.radians(high)
        assert semantic.find(f"group[@name='arm_{side}']") is not None
        assert semantic.find(f"end_effector[@name='gripper_{side}']").get('parent_link') == f'{side}_TCP'
    assert semantic.find("group[@name='both_arms']") is not None
    for mesh in robot.iter('mesh'):
        assert mesh.get('filename').startswith('file:///')
    # Preserve inter-arm collision checking; no blanket cross-arm exemptions.
    for element in semantic.findall('disable_collisions'):
        a, b = element.get('link1'), element.get('link2')
        assert not ((a.startswith('left_') and b.startswith('right_'))
                    or (a.startswith('right_') and b.startswith('left_')))


def test_aggregation_never_merges_missing_or_stale_arm():
    cache = StateCache()
    now, ros = 100., 100_000_000_000
    cache.arm('left', list(reversed(ARM_NAMES)), [6., 5., 4., 3., 2., 1.], ros-200_000_000, now-.2)
    assert cache.snapshot(now, ros)[0] is None
    cache.arm('right', ARM_NAMES, [0.]*6, ros-300_000_000, now-.3)
    cache.gripper('left', 100, now)
    result, status = cache.snapshot(now, ros)
    stamp, names, positions, display_names, display_positions = result
    assert stamp == ros-300_000_000
    assert names == ['left_'+n for n in ARM_NAMES] + ['right_'+n for n in ARM_NAMES]
    assert positions[:6] == [1, 2, 3, 4, 5, 6]
    assert len(display_names) == 13 and len(display_positions) == 13
    assert 'right_gripper_controller' not in display_names
    assert status['left']['gripper_display'] == 'sdk_estimate'
    assert status['right']['gripper_display'] == 'unavailable'
    assert cache.snapshot(now+2, ros+2_000_000_000)[0] is None
    # A stopped source cannot be made fresh by repeating the last packet.
    with pytest.raises(ValueError):
        cache.arm('right', ARM_NAMES, [0.]*6, ros-300_000_000, now+2)
    assert cache.snapshot(now+2, ros+2_000_000_000)[0] is None


def test_bad_names_nonfinite_data_and_clock_skew_are_not_published():
    for names, values in [(list(ARM_NAMES[:-1])+['1_Joint'], [0.]*6),
                          (ARM_NAMES, [float('nan')]+[0.]*5)]:
        with pytest.raises(ValueError):
            StateCache().arm('left', names, values, 1, 0)
    cache = StateCache()
    for side in ('left', 'right'):
        cache.arm(side, ARM_NAMES, [0.]*6, 101_000_000_000, 100)
    assert cache.snapshot(100, 100_000_000_000)[0] is None


def test_missing_invalid_or_stale_gripper_never_becomes_measured_open():
    cache = StateCache()
    for side in ('left', 'right'):
        cache.arm(side, ARM_NAMES, [0.]*6, 100_000_000_000, 100.)
    result, status = cache.snapshot(100., 100_000_000_000)
    assert len(result[3]) == 12
    assert all(s['gripper_display'] == 'unavailable' for s in status.values())
    with pytest.raises(ValueError):
        cache.gripper('left', 181, 100.)
    cache.gripper('left', 0, 100.)
    result, _ = cache.snapshot(100., 100_000_000_000)
    assert result[4][result[3].index('left_gripper_controller')] == -.83
    for side in ('left', 'right'):
        cache.arm(side, ARM_NAMES, [0.]*6, 106_000_000_000, 106.)
    result, status = cache.snapshot(106., 106_000_000_000)
    assert len(result[3]) == 12
    assert status['left']['gripper_display'] == 'unavailable'


def test_namespaced_sdk_goal_validation_rejects_other_arm():
    sys.path.insert(0, str(ROOT.parent / 'jetcobot_dual_bridge' / 'scripts'))
    try:
        import trajectory_guard as guard
        left = tuple('left_'+name for name in ARM_NAMES)
        right = tuple('right_'+name for name in ARM_NAMES)
        points = [(0, [0.]*6), (2, [.05]+[0.]*5)]
        assert guard.LIMITS_DEG == LIMITS_DEG
        assert guard.validate(left, points, [0.]*6, arm_names=left)
        with pytest.raises(ValueError):
            guard.validate(right, points, [0.]*6, arm_names=left)
        with pytest.raises(ValueError):
            guard.validate(ARM_NAMES, points, [0.]*6, arm_names=left)
    finally:
        sys.path.pop(0)
