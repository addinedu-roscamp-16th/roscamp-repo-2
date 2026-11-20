"""PC standard bring-up (2026-11-19 final values) in one command:

  ros2 launch jetcobot_dual_control system.launch.py

Any dual_real.launch.py argument can still be overridden, e.g. rviz:=false.
hybrid_drag_step_deg / hybrid_drag_corner_trim_deg MUST equal the Pi
drag_step_deg / drag_corner_trim_deg (jetcobot_dual_bridge/config/bridge.yaml).
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

STANDARD = {
    'execution_arm': 'both',
    'trajectory_backend': 'sdk_drag_hybrid',
    'coordinated_execution': 'true',
    'collision_padding_m': '0.005',
    'gripper_padding_m': '0.010',
    'tin_case_enabled': 'false',
    'rviz': 'true',
    'hybrid_drag_step_deg': '8.0',
    'hybrid_drag_corner_trim_deg': '0.0',
    'state_gap_grace_s': '4.0',
}


def generate_launch_description():
    base = os.path.join(get_package_share_directory('jetcobot_dual_control'),
                        'launch', 'dual_real.launch.py')
    return LaunchDescription(
        [DeclareLaunchArgument(k, default_value=v) for k, v in STANDARD.items()] +
        [IncludeLaunchDescription(PythonLaunchDescriptionSource(base),
                                  launch_arguments={k: LaunchConfiguration(k)
                                                    for k in STANDARD}.items())])
