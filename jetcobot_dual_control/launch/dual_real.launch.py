"""Measured-state dual arms with optional coordinated execution."""
from pathlib import Path
import math
import time
import xml.etree.ElementTree as ET

import yaml
from ament_index_python.packages import get_package_share_directory as share
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from jetcobot_dual_control.model import build_model


def add_trial_goal(model, side, retain_both_arms=False):
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node as RosNode
    from sensor_msgs.msg import JointState
    from rclpy.qos import qos_profile_sensor_data
    context = Context()
    rclpy.init(context=context, args=[])
    node = RosNode('prepare_dual_measured_trial', context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(node)
    received = []
    node.create_subscription(JointState, f'/{side}/real_joint_states', received.append, qos_profile_sensor_data)
    try:
        deadline = time.monotonic()+8
        while not received and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.1)
        if not received:
            raise ValueError(f'No measured {side} state; execution startup canceled')
        msg = received[-1]
        values = dict(zip(msg.name, msg.position))
        names = [f'{i}_Joint' for i in range(1, 7)]
        age = (node.get_clock().now().nanoseconds-
               (msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec))/1e9
        if not -.25 <= age <= 1.5 or not all(name in values and math.isfinite(values[name]) for name in names):
            raise ValueError('Invalid or stale measured trial start')
        if values['1_Joint']+math.radians(3) > math.radians(168):
            raise ValueError('Joint 1 too near upper bound for +3deg trial')
        semantic = ET.fromstring(model['robot_description_semantic'])
        state = ET.SubElement(semantic, 'group_state', name='trial_j1_plus_3deg', group=f'arm_{side}')
        for name in names:
            value = values[name]+(math.radians(3) if name == '1_Joint' else 0)
            ET.SubElement(state, 'joint', name=side+'_'+name, value=str(value))
        if not retain_both_arms:
            # A combined group needs one atomic controller, not two independent
            # MoveIt controller dispatches that can leave one arm moving alone.
            for group in list(semantic.findall("group[@name='both_arms']")):
                semantic.remove(group)
        model['robot_description_semantic'] = ET.tostring(semantic, encoding='unicode')
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown(context=context)


def setup(context):
    own = Path(share('jetcobot_dual_control'))
    layout = Path(LaunchConfiguration('layout').perform(context)).expanduser()
    model = build_model(share('jetcobot_gazebo'), layout, share)
    collision_padding_m = float(LaunchConfiguration('collision_padding_m').perform(context))
    if not math.isfinite(collision_padding_m) or not 0.0 <= collision_padding_m <= 0.10:
        raise ValueError('collision_padding_m must be 0..0.10 m')
    model['robot_description_planning']['default_robot_padding'] = collision_padding_m
    side = LaunchConfiguration('execution_arm').perform(context)
    trajectory_backend = LaunchConfiguration('trajectory_backend').perform(context)
    coordinated = LaunchConfiguration('coordinated_execution').perform(context).lower() == 'true'
    if trajectory_backend not in ('sdk_goal', 'sdk_path', 'sdk_refresh_trial', 'sdk_hybrid', 'sdk_drag', 'sdk_drag_hybrid'):
        raise ValueError('trajectory_backend must be sdk_goal, sdk_path, sdk_hybrid, sdk_refresh_trial, sdk_drag or sdk_drag_hybrid')
    if side not in ('none', 'left', 'right', 'both'):
        raise ValueError('execution_arm must be none, left, right or both')
    if coordinated and (side != 'both' or trajectory_backend != 'sdk_drag_hybrid'):
        raise ValueError('coordinated_execution requires both arms and sdk_drag_hybrid')
    execution = side != 'none'
    execution_sides = ('left', 'right') if side == 'both' else (side,)
    if execution:
        for selected in execution_sides:
            add_trial_goal(model, selected, retain_both_arms=coordinated)
    defaults = Path(share('moveit_configs_utils')) / 'default_configs/ompl_planning.yaml'
    ompl = yaml.safe_load(defaults.read_text())
    ompl['planner_configs'] = {'RRTConnectkConfigDefault': {'type': 'geometric::RRTConnect', 'range': 0.0}}
    for group in ('arm_left', 'arm_right', 'both_arms', 'gripper_left', 'gripper_right'):
        ompl[group] = {'planner_configs': ['RRTConnectkConfigDefault'],
                       'longest_valid_segment_fraction': .005}
    moveit = {
        'planning_pipelines': ['ompl'], 'default_planning_pipeline': 'ompl', 'ompl': ompl,
        'allow_trajectory_execution': execution,
        'disable_capabilities': '' if execution else 'move_group/MoveGroupExecuteTrajectoryAction',
        'publish_robot_description': True, 'publish_robot_description_semantic': True,
        'publish_planning_scene': True, 'publish_geometry_updates': True,
        'publish_state_updates': True, 'publish_transforms_updates': True,
    }
    if execution:
        controllers = {'controller_names': (['guarded_both_controller'] if coordinated else
                                          [f'guarded_{selected}_controller' for selected in execution_sides])}
        if coordinated:
            controllers['guarded_both_controller'] = {
                'type': 'FollowJointTrajectory', 'action_ns': 'follow_joint_trajectory',
                'default': True,
                'joints': [f'{selected}_{i}_Joint' for selected in ('left', 'right')
                           for i in range(1, 7)]}
        for selected in execution_sides:
            if not coordinated:
                controllers[f'guarded_{selected}_controller'] = {
                    'type': 'FollowJointTrajectory', 'action_ns': 'follow_joint_trajectory',
                    'default': True, 'joints': [f'{selected}_{i}_Joint' for i in range(1, 7)]}
            grip_controller = f'guarded_{selected}_gripper_controller'
            controllers['controller_names'].append(grip_controller)
            controllers[grip_controller] = {
                'type': 'FollowJointTrajectory', 'action_ns': 'follow_joint_trajectory',
                'default': True, 'joints': [f'{selected}_gripper_controller']}
        moveit.update({
            'moveit_manage_controllers': False,
            'moveit_controller_manager': 'moveit_simple_controller_manager/MoveItSimpleControllerManager',
            'moveit_simple_controller_manager': controllers,
            'trajectory_execution.allowed_start_tolerance': (math.radians(5.0)
                                                             if coordinated else .02),
            'trajectory_execution.allowed_execution_duration_scaling': 3.,
            'trajectory_execution.allowed_goal_duration_margin': 20.,
        })
    print(f'Collision padding: {collision_padding_m:.3f} m per robot link')
    print(f'DUAL REAL: {side.upper()+" SDK (multi-joint targets; Pi max_move_deg is independent)" if execution else "PLAN ONLY"}. Layout: {layout}.')
    print('Gripper display is an approximate SDK estimate/default, not calibrated finger feedback.')
    nodes = [
        Node(package='jetcobot_dual_control', executable='state_aggregator', namespace='jetcobot_dual',
             parameters=[{'use_sim_time': False, 'stale_seconds': 1.5}], output='screen'),
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             namespace='jetcobot_dual', name='state_publisher', parameters=[model],
             remappings=[('joint_states', '/jetcobot_dual/display_joint_states')], output='screen'),
        Node(package='moveit_ros_move_group', executable='move_group', namespace='jetcobot_dual',
             parameters=[model, moveit],
             remappings=[('joint_states', '/jetcobot_dual/display_joint_states')], output='screen'),
        Node(package='jetcobot_dual_control', executable='tin_case_scene', namespace='jetcobot_dual',
             parameters=[{'config_file': LaunchConfiguration('tin_case_config'),
                          'tin_case_enabled': ParameterValue(
                              LaunchConfiguration('tin_case_enabled'), value_type=bool),
                          'gripper_padding_m': ParameterValue(
                              LaunchConfiguration('gripper_padding_m'), value_type=float),
                          'camera_padding_m': ParameterValue(
                              LaunchConfiguration('camera_padding_m'), value_type=float)}],
             output='screen'),
        Node(package='rviz2', executable='rviz2', name='jetcobot_dual_real_view',
             arguments=['-d', str(own / 'config/dual_real.rviz')],
             parameters=[model, {'planning_pipelines': ['ompl'], 'ompl': ompl}],
             condition=IfCondition(LaunchConfiguration('rviz')), output='screen'),
    ]
    if execution:
        gate_parameters = {'execution_arm': side, 'trajectory_backend': trajectory_backend,
                           'coordinated_execution': coordinated,
                           'use_sim_time': False,
                           'robot_description': model['robot_description']}
        if trajectory_backend == 'sdk_drag_hybrid':
            gate_parameters.update({
                'hybrid_check_step_deg': float(LaunchConfiguration('hybrid_check_step_deg').perform(context)),
                'hybrid_drag_step_deg': float(LaunchConfiguration('hybrid_drag_step_deg').perform(context)),
                'hybrid_drag_deviation_deg': float(LaunchConfiguration('hybrid_drag_deviation_deg').perform(context)),
                'hybrid_drag_corner_trim_deg': float(LaunchConfiguration('hybrid_drag_corner_trim_deg').perform(context)),
                'merge_endpoint_routes': LaunchConfiguration('merge_endpoint_routes').perform(context).lower() == 'true',
                'endpoint_as_drag': LaunchConfiguration('endpoint_as_drag').perform(context).lower() == 'true',
                'state_gap_grace_s': float(LaunchConfiguration('state_gap_grace_s').perform(context)),
                'coordinated_check_step_deg': float(LaunchConfiguration('coordinated_check_step_deg').perform(context)),
            })
        nodes.append(Node(package='jetcobot_dual_control', executable='execution_gate', namespace='jetcobot_dual',
                          parameters=[gate_parameters], output='screen'))
    return nodes


def generate_launch_description():
    own = Path(share('jetcobot_dual_control'))
    return LaunchDescription([
        DeclareLaunchArgument('layout', default_value=str(own / 'config/layout.yaml')),
        DeclareLaunchArgument('tin_case_config', default_value=str(own / 'config/tin_case.yaml'),
                              description='Open tin case position in either robot base frame'),
        DeclareLaunchArgument('tin_case_enabled', default_value='false',
                              description='Add the tin case collision object to MoveIt when true'),
        DeclareLaunchArgument('rviz', default_value='true'),
        DeclareLaunchArgument('collision_padding_m', default_value='0.005',
                              description='MoveIt collision padding per robot link, metres'),
        DeclareLaunchArgument('gripper_padding_m', default_value='0.010',
                              description='MoveIt padding for both grippers only, metres'),
        DeclareLaunchArgument('camera_padding_m', default_value='0.015',
                              description='MoveIt padding for both camera links only, metres'),
        DeclareLaunchArgument('execution_arm', default_value='none',
                             description='none: plan only; left/right/both: SDK connections, one operation at a time'),
        DeclareLaunchArgument('coordinated_execution', default_value='false',
                              description='Enable one atomic both_arms controller with collision-checked sections'),
        DeclareLaunchArgument('trajectory_backend', default_value='sdk_goal',
                              description='sdk_goal: final target; sdk_path: sparse waypoints; sdk_hybrid: legacy route; sdk_drag_hybrid: collision scene based endpoint/drag routing; sdk_refresh_trial: mode 1 experiment; sdk_drag: encoder path trial'),
        DeclareLaunchArgument('hybrid_check_step_deg', default_value='1.0',
                              description='Maximum joint interpolation step for hybrid collision checks'),
        DeclareLaunchArgument('hybrid_drag_step_deg', default_value='5.0',
                              description='Must match Pi drag_step_deg for sdk_drag_hybrid'),
        DeclareLaunchArgument('hybrid_drag_deviation_deg', default_value='1.5',
                              description='Must match Pi drag_deviation_deg for sdk_drag_hybrid'),
        DeclareLaunchArgument('hybrid_drag_corner_trim_deg', default_value='3.0',
                              description='Must match Pi drag_corner_trim_deg for sdk_drag_hybrid'),
        DeclareLaunchArgument('coordinated_check_step_deg', default_value='1.0',
                              description='Joint step of the two-arm gate preflight grid (0.5..2.0 deg)'),
        DeclareLaunchArgument('state_gap_grace_s', default_value='2.5',
                              description='Seconds a coordinated motion waits through a combined joint-state gap'),
        DeclareLaunchArgument('endpoint_as_drag', default_value='false',
                              description='Send coordinated endpoint routes of 5 deg or more as tracked drag goals'),
        DeclareLaunchArgument('merge_endpoint_routes', default_value='false',
                              description='Join several combined endpoint routes into one checked continuous drag (no stop between them)'),
        OpaqueFunction(function=setup),
    ])
