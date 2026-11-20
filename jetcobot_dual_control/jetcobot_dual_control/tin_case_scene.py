"""Add an open-top tin case to the live MoveIt planning scene."""
import math
from pathlib import Path

import rclpy
from geometry_msgs.msg import Pose
from moveit_msgs.msg import (CollisionObject, LinkPadding, PlanningScene,
                             PlanningSceneComponents)
from moveit_msgs.srv import ApplyPlanningScene, GetPlanningScene
from rclpy.node import Node
from shape_msgs.msg import SolidPrimitive
import yaml


OBJECT_ID = 'open_tin_case'
GRIPPER_LINKS = tuple(
    f'{side}_gripper_{part}'
    for side in ('left', 'right')
    for part in ('base', 'left1', 'left2', 'left3', 'right1', 'right2', 'right3')
)
CAMERA_LINKS = ('left_jetcocam', 'right_jetcocam')
# Dimensions [mm]. Four walls surround a 95 x 71 x 57 mm empty interior.
PARTS_MM = (
    ((98.0, 74.0, 3.0), (0.0, 0.0, 1.5)),       # floor
    ((1.5, 71.0, 57.0), (-48.25, 0.0, 31.5)),  # left wall
    ((1.5, 71.0, 57.0), (48.25, 0.0, 31.5)),   # right wall
    ((98.0, 1.5, 57.0), (0.0, -36.25, 31.5)),  # near wall
    ((98.0, 1.5, 57.0), (0.0, 36.25, 31.5)),   # far wall
)


def build_collision_object(config):
    frame = config['frame_id']
    if frame not in ('right_base_link', 'left_base_link'):
        raise ValueError('frame_id must be right_base_link or left_base_link')
    xyz = config['bottom_center_xyz_mm']
    if not isinstance(xyz, list) or len(xyz) != 3:
        raise ValueError('bottom_center_xyz_mm must be a list of three numbers')
    x, y, z = (float(value) for value in xyz)
    yaw_deg = float(config.get('yaw_deg', 0.0))
    if not all(math.isfinite(value) for value in (x, y, z, yaw_deg)):
        raise ValueError('Tin case position/yaw must be finite')
    yaw = math.radians(yaw_deg)
    c, s = math.cos(yaw), math.sin(yaw)

    obj = CollisionObject()
    obj.id = OBJECT_ID
    obj.header.frame_id = frame
    obj.operation = CollisionObject.ADD  # same ID replaces the previous pose
    for dimensions, (dx, dy, dz) in PARTS_MM:
        primitive = SolidPrimitive()
        primitive.type = SolidPrimitive.BOX
        primitive.dimensions = [value / 1000.0 for value in dimensions]
        pose = Pose()
        pose.position.x = (x + c * dx - s * dy) / 1000.0
        pose.position.y = (y + s * dx + c * dy) / 1000.0
        pose.position.z = (z + dz) / 1000.0
        pose.orientation.z = math.sin(yaw / 2.0)
        pose.orientation.w = math.cos(yaw / 2.0)
        obj.primitives.append(primitive)
        obj.primitive_poses.append(pose)
    return obj


def main():
    rclpy.init()
    node = Node('tin_case_scene')
    node.declare_parameter('config_file', '')
    node.declare_parameter('tin_case_enabled', True)
    node.declare_parameter('gripper_padding_m', 0.005)
    node.declare_parameter('camera_padding_m', 0.015)
    try:
        case_enabled = bool(node.get_parameter('tin_case_enabled').value)
        gripper_padding = float(node.get_parameter('gripper_padding_m').value)
        camera_padding = float(node.get_parameter('camera_padding_m').value)
        if not math.isfinite(gripper_padding) or not 0.0 <= gripper_padding <= 0.03:
            raise ValueError('gripper_padding_m must be 0..0.03 m')
        if not math.isfinite(camera_padding) or not 0.0 <= camera_padding <= 0.03:
            raise ValueError('camera_padding_m must be 0..0.03 m')
        service = node.create_client(ApplyPlanningScene, '/jetcobot_dual/apply_planning_scene')
        while rclpy.ok() and not service.wait_for_service(timeout_sec=1.0):
            node.get_logger().info('Waiting for MoveIt apply_planning_scene service')
        if not rclpy.ok():
            return
        existing_case = False
        if not case_enabled:
            scene_client = node.create_client(
                GetPlanningScene, '/jetcobot_dual/get_planning_scene')
            if not scene_client.wait_for_service(timeout_sec=5.0):
                raise RuntimeError('MoveIt get_planning_scene service unavailable')
            query = GetPlanningScene.Request()
            query.components.components = PlanningSceneComponents.WORLD_OBJECT_GEOMETRY
            scene_future = scene_client.call_async(query)
            rclpy.spin_until_future_complete(node, scene_future, timeout_sec=5.0)
            if not scene_future.done() or scene_future.result() is None:
                raise RuntimeError('Failed to read current MoveIt case scene')
            existing_case = any(item.id == OBJECT_ID for item in
                                scene_future.result().scene.world.collision_objects)
        request = ApplyPlanningScene.Request()
        request.scene = PlanningScene()
        request.scene.is_diff = True
        request.scene.robot_state.is_diff = True
        request.scene.link_padding = [
            LinkPadding(link_name=name, padding=gripper_padding)
            for name in GRIPPER_LINKS
        ] + [LinkPadding(link_name=name, padding=camera_padding)
             for name in CAMERA_LINKS]
        if case_enabled:
            config_file = Path(node.get_parameter('config_file').value).expanduser()
            config = yaml.safe_load(config_file.read_text())
            obj = build_collision_object(config)
            request.scene.world.collision_objects.append(obj)
        elif existing_case:
            obj = CollisionObject()
            obj.id = OBJECT_ID
            obj.operation = CollisionObject.REMOVE
            request.scene.world.collision_objects.append(obj)
        future = service.call_async(request)
        rclpy.spin_until_future_complete(node, future)
        if future.exception() is not None:
            raise RuntimeError('Failed to apply tin case planning scene') from future.exception()
        if not future.result().success:
            raise RuntimeError('MoveIt rejected tin case planning scene')
        if case_enabled:
            node.get_logger().info(
                f'Added {OBJECT_ID}: bottom center {config["bottom_center_xyz_mm"]} mm '
                f'in {obj.header.frame_id}, yaw {config.get("yaw_deg", 0.0)} deg')
        elif existing_case:
            node.get_logger().info(f'Removed {OBJECT_ID} from MoveIt scene')
        else:
            node.get_logger().info(f'{OBJECT_ID} already absent from MoveIt scene')
        node.get_logger().info(
            f'Set {len(GRIPPER_LINKS)} gripper links to {gripper_padding:.3f} m padding')
        node.get_logger().info(
            f'Set {len(CAMERA_LINKS)} camera links to {camera_padding:.3f} m padding')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
