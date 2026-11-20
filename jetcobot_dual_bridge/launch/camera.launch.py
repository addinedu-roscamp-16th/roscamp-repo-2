"""Pi camera -> PC H.264 RTP stream (the pick UI receives it with udpsrc).

  ros2 launch jetcobot_dual_bridge camera.launch.py side:=left host:=<PC IP>

host  = IP of the PC that runs the pick UI (check on the PC: hostname -I).
port  = left 5000 / right 5001 by default; must match --left-port/--right-port.
Same pipeline as the hand-typed gst-launch-1.0 command used until 2026-11-20.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration

PORTS = {'left': '5000', 'right': '5001'}


def _camera(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    side, host = get('side'), get('host')
    if side not in PORTS:
        raise RuntimeError("side must be 'left' or 'right'")
    if not host:
        raise RuntimeError('host:=<PC IP> is required (the PC running the pick UI)')
    port = get('port') or PORTS[side]
    pipeline = (
        f"v4l2src device={get('device')} ! "
        f"video/x-raw,format=YUY2,width={get('width')},height={get('height')},"
        f"framerate={get('fps')}/1 ! videoconvert ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={get('bitrate')} "
        f"key-int-max=30 ! rtph264pay config-interval=1 pt=96 mtu=1200 ! "
        f"udpsink host={host} port={port} sync=false")
    return [ExecuteProcess(cmd=['gst-launch-1.0', '-v'] + pipeline.split(),
                           output='screen', name=f'{side}_camera')]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('side', description='left or right'),
        DeclareLaunchArgument('host', default_value='',
                              description='PC IP that runs the pick UI (REQUIRED)'),
        DeclareLaunchArgument('port', default_value='', description='default left 5000 / right 5001'),
        DeclareLaunchArgument('device', default_value='/dev/jetcocam0'),
        DeclareLaunchArgument('width', default_value='640'),
        DeclareLaunchArgument('height', default_value='480'),
        DeclareLaunchArgument('fps', default_value='30'),
        DeclareLaunchArgument('bitrate', default_value='1500', description='kbit/s'),
        OpaqueFunction(function=_camera),
    ])
