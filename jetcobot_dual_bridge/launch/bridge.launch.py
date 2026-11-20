"""One Pi, one arm: ros2 launch jetcobot_dual_bridge bridge.launch.py side:=right

The bridge is run with the interpreter that has pymycobot (`python`, default
~/venv/mycobot/bin/python), not as a console script, so the Pi's existing
venv keeps working.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration


def _bridge(context):
    share = get_package_share_directory('jetcobot_dual_bridge')
    side = LaunchConfiguration('side').perform(context)
    if side not in ('left', 'right'):
        raise RuntimeError("side must be 'left' or 'right'")
    python = os.path.expanduser(LaunchConfiguration('python').perform(context))
    script = os.path.join(share, 'scripts', LaunchConfiguration('bridge').perform(context) + '.py')
    params = LaunchConfiguration('params_file').perform(context) or os.path.join(share, 'config', 'bridge.yaml')
    for path in (python, script, params):
        if not os.path.exists(path):
            raise RuntimeError(f'Not found: {path}')
    return [ExecuteProcess(
        cmd=[python, script, '--ros-args', '-r', f'__ns:=/{side}',
             '--params-file', params, '-p', f'robot_side:={side}'],
        output='screen', emulate_tty=True, name=f'{side}_bridge')]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('side', description='left or right'),
        DeclareLaunchArgument('python', default_value='~/venv/mycobot/bin/python',
                              description='interpreter with pymycobot + rclpy'),
        DeclareLaunchArgument('bridge', default_value='sdk_drag_hybrid_bridge',
                              description='script name in share/scripts without .py'),
        DeclareLaunchArgument('params_file', default_value='',
                              description='default: share/config/bridge.yaml'),
        OpaqueFunction(function=_bridge),
    ])
