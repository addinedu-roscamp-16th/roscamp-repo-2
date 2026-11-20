"""Pick UI: ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left

Runs scripts/<script>.py with the perception venv (`python`, default
~/camera_code/.venv/bin/python: torch, SAM2, LightGlue). The UI is an
OpenCV window; keys are read from that window.
Other tools: script:=moveit_pick/compare_board_captures python:=python3 args:='-n 4'
"""
import os
import shlex

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration


def _app(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    share = get_package_share_directory('jetcobot_dual_app')
    python = os.path.expanduser(get('python'))
    script = os.path.realpath(os.path.join(share, 'scripts', get('script') + '.py'))
    if not os.path.isfile(script):
        raise RuntimeError(f'Not found: {script}')
    cmd = [python, script] + shlex.split(get('args'))
    if get('script') == 'multi_pick/sam_multi_real':
        cmd += ['--first-arm', get('first_arm')]
    # realpath: with --symlink-install the script resolves to the source tree,
    # so captures land in scripts/sam_yaw_output (git-ignored).
    root = os.path.dirname(os.path.realpath(os.path.join(share, 'scripts', 'left_detail.py')))
    return [ExecuteProcess(cmd=cmd, cwd=root, output='screen',
                           emulate_tty=True, name='pick_app')]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('python', default_value='~/camera_code/.venv/bin/python'),
        DeclareLaunchArgument('script', default_value='multi_pick/sam_multi_real'),
        DeclareLaunchArgument('first_arm', default_value='left', description='left or right'),
        DeclareLaunchArgument('args', default_value='--stereo-fixed-see-center',
                              description='extra arguments, one string'),
        OpaqueFunction(function=_app),
    ])
