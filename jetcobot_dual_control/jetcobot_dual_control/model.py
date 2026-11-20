"""Adapt the existing Gazebo dual URDF/SRDF without editing their source files."""
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

SIDES = ('left', 'right')
LIMITS_DEG = ((-168, 168), (-135, 135), (-150, 150),
              (-145, 145), (-165, 165), (-180, 180))
# The left Pi SDK reports J5 as -155..160; the right Pi has -165..165.
LEFT_LIMITS_DEG = LIMITS_DEG[:4] + ((-155, 160),) + LIMITS_DEG[5:]


def add_fingertip_collision_extensions(robot):
    """Add the measured 20 mm beyond the distal finger meshes, along local +Y."""
    # The STL tip bounds plus their existing mesh origins end at Y=0.0532 m
    # (left finger) and Y=0.0536 m (right finger) in each link frame.
    for side in SIDES:
        for finger, center in (('left1', (0.0078, 0.0627, -0.0082)),
                               ('right1', (-0.0091, 0.0631, -0.0082))):
            link = robot.find(f"link[@name='{side}_gripper_{finger}']")
            if link is None:
                raise ValueError(f'Missing distal gripper link: {side} {finger}')
            collision = ET.SubElement(link, 'collision',
                                      name=f'{side}_{finger}_tip_extension')
            ET.SubElement(collision, 'origin',
                          xyz=' '.join(map(str, center)), rpy='0 0 0')
            geometry = ET.SubElement(collision, 'geometry')
            ET.SubElement(geometry, 'box', size='0.025 0.021 0.021')


def vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f'{label}: expected three numbers')
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f'{label}: nonfinite value')
    return result


def build_model(gazebo_share, layout_path, package_resolver):
    source = Path(gazebo_share)
    layout = yaml.safe_load(Path(layout_path).read_text())
    robot = ET.parse(source / 'urdf/dual/jetcobot_dual.urdf').getroot()
    semantic = ET.parse(source / 'config/dual/jetcobot_dual.srdf').getroot()
    planning = {'joint_limits': {}, 'default_velocity_scaling_factor': .1,
                'default_acceleration_scaling_factor': .1}
    for tag in ('gazebo', 'ros2_control', 'transmission'):
        for element in list(robot.findall(tag)):
            robot.remove(element)
    for side in SIDES:
        entry = layout[side]
        xyz = vector(entry['xyz'], f'{side}.xyz')
        rpy = [math.radians(v) for v in vector(entry['rpy_deg'], f'{side}.rpy_deg')]
        anchor = robot.find(f"joint[@name='{side}_world_to_dummy']/origin")
        if anchor is None:
            raise ValueError(f'Missing {side} base anchor')
        anchor.set('xyz', ' '.join(map(str, xyz)))
        anchor.set('rpy', ' '.join(map(str, rpy)))
        for index, (low, high) in enumerate(LEFT_LIMITS_DEG if side == 'left' else LIMITS_DEG, 1):
            name = f'{side}_{index}_Joint'
            limit = robot.find(f"joint[@name='{name}']/limit")
            if limit is None:
                raise ValueError(f'Missing limit: {name}')
            limit.set('lower', str(math.radians(low)))
            limit.set('upper', str(math.radians(high)))
            planning['joint_limits'][name] = {
                'has_position_limits': True, 'min_position': math.radians(low),
                'max_position': math.radians(high),
                'has_velocity_limits': True, 'max_velocity': .5,
                'has_acceleration_limits': True, 'max_acceleration': .5,
            }
        adjustment = float(entry.get('wrist_roll_adjust_deg', 0))
        if not math.isfinite(adjustment):
            raise ValueError('Nonfinite wrist adjustment')
        wrist = robot.find(f"joint[@name='{side}_6_Joint']/origin")
        roll, pitch, yaw = map(float, wrist.get('rpy', '0 0 0').split())
        wrist.set('rpy', f'{roll + math.radians(adjustment)} {pitch} {yaw}')
        planning['joint_limits'][f'{side}_gripper_controller'] = {
            'has_velocity_limits': True, 'max_velocity': .2,
            'has_acceleration_limits': True, 'max_acceleration': .5,
        }
        effector = semantic.find(f"end_effector[@name='gripper_{side}']")
        if effector is None:
            raise ValueError(f'Missing {side} end effector')
        effector.set('parent_link', f'{side}_TCP')
        effector.set('parent_group', f'arm_{side}')
    add_fingertip_collision_extensions(robot)
    # Measured physical observation poses supplied by the user (degrees).
    for state in list(semantic.findall('group_state')):
        if state.get('group') in ('arm_left', 'arm_right', 'both_arms'):
            semantic.remove(state)
    see_center_deg = {
        'left': (-31.64, 20.74, -41.39, -47.54, 2.10, -17.75),
        'right': (28.30, 18.19, -39.46, -46.75, 13.97, -42.36),
    }
    see_center_rad = {side: tuple(map(math.radians, angles))
                      for side, angles in see_center_deg.items()}
    for side in SIDES:
        home = ET.SubElement(semantic, 'group_state', name='home', group=f'arm_{side}')
        for index in range(1, 7):
            ET.SubElement(home, 'joint', name=f'{side}_{index}_Joint', value='0')
        see_center = ET.SubElement(semantic, 'group_state', name='see_center', group=f'arm_{side}')
        for index, radians in enumerate(see_center_rad[side], 1):
            ET.SubElement(see_center, 'joint', name=f'{side}_{index}_Joint',
                           value=str(radians))
    for mesh in robot.iter('mesh'):
        uri = mesh.get('filename', '')
        if uri.startswith('package://'):
            package, relative = uri[len('package://'):].split('/', 1)
            path = Path(package_resolver(package)) / relative
            if not path.is_file():
                raise FileNotFoundError(path)
            mesh.set('filename', path.resolve().as_uri())
    kinematics = yaml.safe_load((source / 'config/dual/kinematics.yaml').read_text())
    for values in kinematics.values():
        values['kinematics_solver_timeout'] = .05
    return {'robot_description': ET.tostring(robot, encoding='unicode'),
            'robot_description_semantic': ET.tostring(semantic, encoding='unicode'),
            'robot_description_kinematics': kinematics,
            'robot_description_planning': planning, 'use_sim_time': False}
