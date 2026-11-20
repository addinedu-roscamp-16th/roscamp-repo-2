"""Register a camera-observed pickup object in MoveIt, or attach it after G."""
import json
import math
import time
from pathlib import Path

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import Pose
from moveit_msgs.msg import AttachedCollisionObject, CollisionObject, PlanningScene
from moveit_msgs.srv import ApplyPlanningScene
from rclpy.node import Node
from rclpy.time import Time
from shape_msgs.msg import SolidPrimitive
from tf2_ros import Buffer, TransformListener
import yaml


OBJECT_ID = 'pickup_object'


def multiply_quat(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return np.array((w1*x2+x1*w2+y1*z2-z1*y2,
                     w1*y2-x1*z2+y1*w2+z1*x2,
                     w1*z2+x1*y2-y1*x2+z1*w2,
                     w1*w2-x1*x2-y1*y2-z1*z2), dtype=float)


def rotation_matrix(q):
    x, y, z, w = q
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                     (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))), dtype=float)


def make_pose(xyz_m, quat):
    pose = Pose()
    pose.position.x, pose.position.y, pose.position.z = map(float, xyz_m)
    pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w = map(float, quat)
    return pose


def build_world_object(target, shape):
    if target.get('frame') != 'right_base_link':
        raise ValueError('Camera target must use right_base_link')
    top = np.asarray(target['center_xyz_mm'], dtype=float)
    yaw = math.radians(float(target['object_long_yaw_deg']))
    if top.shape != (3,) or not np.isfinite(top).all() or not math.isfinite(yaw):
        raise ValueError('Invalid camera object pose')
    c, s = math.cos(yaw), math.sin(yaw)
    yaw_quat = np.array((0.0, 0.0, math.sin(yaw/2), math.cos(yaw/2)))
    obj = CollisionObject()
    obj.id = OBJECT_ID
    obj.header.frame_id = 'right_base_link'
    obj.operation = CollisionObject.ADD

    def append(primitive, xy_offset_mm, height_mm, orientation):
        dx, dy = xy_offset_mm
        center = (top + np.array((c*dx-s*dy, s*dx+c*dy, -height_mm/2))) / 1000.0
        obj.primitives.append(primitive)
        obj.primitive_poses.append(make_pose(center, orientation))

    if shape['kind'] == 'box':
        size = np.asarray(shape['size_mm'], dtype=float)
        if size.shape != (3,) or np.any(size <= 0) or not np.isfinite(size).all():
            raise ValueError('Invalid box size')
        primitive = SolidPrimitive(type=SolidPrimitive.BOX,
                                   dimensions=(size/1000).tolist())
        append(primitive, (0.0, 0.0), size[2], yaw_quat)
    elif shape['kind'] == 'two_cylinders':
        length = float(shape['cylinder_length_mm'])
        diameter = float(shape['cylinder_diameter_mm'])
        centers = np.asarray(shape['centers_xy_mm'], dtype=float)
        if (not math.isfinite(length) or not math.isfinite(diameter) or
                length <= 0 or diameter <= 0 or centers.shape != (2, 2) or
                not np.isfinite(centers).all()):
            raise ValueError('Invalid paired-cylinder dimensions')
        # MoveIt cylinder axis is local Z; rotate it onto the object's long X.
        axis_quat = np.array((0.0, math.sin(math.pi/4), 0.0, math.cos(math.pi/4)))
        orientation = multiply_quat(yaw_quat, axis_quat)
        for center in centers:
            primitive = SolidPrimitive(type=SolidPrimitive.CYLINDER,
                                       dimensions=[length/1000, diameter/2000])
            append(primitive, center, diameter, orientation)
    else:
        raise ValueError(f'Unknown object shape: {shape["kind"]}')
    return obj


def attach_to_tcp(world_object, transform, arm):
    translation = transform.translation
    rotation = transform.rotation
    origin = np.array((translation.x, translation.y, translation.z), dtype=float)
    tcp_quat = np.array((rotation.x, rotation.y, rotation.z, rotation.w), dtype=float)
    tcp_quat /= np.linalg.norm(tcp_quat)
    inverse_quat = tcp_quat * np.array((-1.0, -1.0, -1.0, 1.0))
    inverse_rotation = rotation_matrix(tcp_quat).T
    attached = AttachedCollisionObject()
    attached.link_name = f'{arm}_TCP'
    attached.touch_links = [attached.link_name, f'{arm}_gripper_base'] + [
        f'{arm}_gripper_{side}{index}' for side in ('left', 'right') for index in (1, 2, 3)]
    attached.object = world_object
    attached.object.header.frame_id = attached.link_name
    attached.object.primitive_poses = [
        make_pose(inverse_rotation @ (np.array((pose.position.x, pose.position.y, pose.position.z)) - origin),
                  multiply_quat(inverse_quat, np.array((pose.orientation.x, pose.orientation.y,
                                                       pose.orientation.z, pose.orientation.w))))
        for pose in world_object.primitive_poses]
    return attached


def main():
    rclpy.init()
    node = Node('pick_object_scene')
    node.declare_parameter('target_file', '')
    node.declare_parameter('object_type', '')
    node.declare_parameter('mode', 'world')
    node.declare_parameter('catalog_file', str(Path(get_package_share_directory('jetcobot_dual_control')) /
                                               'config/pick_objects.yaml'))
    try:
        target_path = Path(str(node.get_parameter('target_file').value)).expanduser()
        target = json.loads(target_path.read_text())
        catalog = yaml.safe_load(Path(str(node.get_parameter('catalog_file').value)).expanduser().read_text())
        object_type = str(node.get_parameter('object_type').value) or target.get('object_type', '')
        if object_type not in catalog:
            raise ValueError(f'Select object_type from {list(catalog)}')
        mode = str(node.get_parameter('mode').value)
        if mode not in ('world', 'attach', 'remove'):
            raise ValueError('mode must be world, attach or remove')
        scene = PlanningScene()
        scene.is_diff = True
        scene.robot_state.is_diff = True
        if mode != 'remove':
            obj = build_world_object(target, catalog[object_type])
        if mode == 'world':
            scene.world.collision_objects.append(obj)
        else:
            close_record = target_path.parent / 'gripper_close_complete.json'
            close = json.loads(close_record.read_text())
            arm = close['arm']
            if arm not in ('left', 'right'):
                raise ValueError('Invalid arm in gripper close record')
            attached = AttachedCollisionObject()
            attached.link_name = f'{arm}_TCP'
            attached.object.id = OBJECT_ID
            attached.object.operation = CollisionObject.REMOVE
            if mode == 'attach':
                buffer = Buffer()
                listener = TransformListener(buffer, node)
                deadline = time.monotonic() + 5.0
                while rclpy.ok() and time.monotonic() < deadline and not buffer.can_transform(
                        'right_base_link', attached.link_name, Time()):
                    rclpy.spin_once(node, timeout_sec=0.1)
                if not buffer.can_transform('right_base_link', attached.link_name, Time()):
                    raise RuntimeError(f'No current TF for {attached.link_name}')
                transform = buffer.lookup_transform('right_base_link', attached.link_name, Time()).transform
                attached = attach_to_tcp(obj, transform, arm)
            else:
                scene.robot_state.attached_collision_objects.append(attached)
            removed = CollisionObject()
            removed.id = OBJECT_ID
            removed.header.frame_id = 'right_base_link'
            removed.operation = CollisionObject.REMOVE
            scene.world.collision_objects.append(removed)
            if mode == 'attach':
                scene.robot_state.attached_collision_objects.append(attached)
        service = node.create_client(ApplyPlanningScene, '/jetcobot_dual/apply_planning_scene')
        while rclpy.ok() and not service.wait_for_service(timeout_sec=1.0):
            node.get_logger().info('Waiting for MoveIt planning scene')
        if not rclpy.ok():
            return
        request = ApplyPlanningScene.Request()
        request.scene = scene
        future = service.call_async(request)
        rclpy.spin_until_future_complete(node, future)
        if future.exception() is not None:
            raise RuntimeError('Failed to update MoveIt pickup scene') from future.exception()
        if not future.result().success:
            raise RuntimeError('MoveIt rejected pickup scene update')
        node.get_logger().info(f'{mode}: {object_type} ({OBJECT_ID}) from {target_path}')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
