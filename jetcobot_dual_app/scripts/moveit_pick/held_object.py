"""Attach/remove the grasped object in MoveIt, fixed in the gripper TCP frame.

The camera estimate is taken before fine alignment and descent, so after the
grasp the object is modeled relative to the closed gripper instead:
- object local X (long side, pick_objects.yaml) -> TCP Y   (user, 2026-11-19)
- object local Y (short, gripped side)          -> TCP X
- object local Z (height, up)                   -> TCP -Z  (TCP Z points down)
- object bottom = closed fingertip + 3 mm (grasp target: tips 3 mm above floor)
Collision checks (MoveIt planning, gate preflight and live monitor) then
include the object. This is a geometric model, not a sensor of the real grasp.
"""
import math
from pathlib import Path


def _pkg_share(name, fallback):
    """Installed ROS package share dir; old source path when not built."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory(name))
    except Exception:
        return Path(fallback).expanduser()


import yaml

def cached_client(node, srv_type, name):
    """Reuse one service client per (node, name). Creating a new client on
    every call (thousands of FK calls during a place) slowed every later ROS
    wait in the same process until the return planning looked frozen."""
    cache = node.__dict__.setdefault('_cached_clients', {})
    if name not in cache:
        cache[name] = node.create_client(srv_type, name)
    return cache[name]


def cached_action(node, action_type, name):
    from rclpy.action import ActionClient
    cache = node.__dict__.setdefault('_cached_actions', {})
    if name not in cache:
        cache[name] = ActionClient(node, action_type, name)
    return cache[name]


CATALOG = _pkg_share('jetcobot_dual_control', '~/pinky_ws/src/jetcobot_dual/jetcobot_dual_control') / 'config' / 'pick_objects.yaml'
OBJECT_ID = 'pickup_object'  # temporary camera target / legacy single-arm ID


def held_id(arm):
    if arm not in ('left', 'right'):
        raise ValueError(f'Invalid arm {arm!r}')
    return f'{OBJECT_ID}_{arm}'
TIP_OFFSET_MM = {'left': 40.0, 'right': 25.0}   # closed tip below TCP (GRASP_CLOSED_TIP_OFFSET_MM)
TIP_ABOVE_BOTTOM_MM = 3.0                         # GRASP_CLOSED_TIP_ABOVE_FLOOR_MM
# Gripper joint value whose fingertip-box inner faces are the object width
# apart (offline URDF calculation, 2026-11-19). -0.83 = model fully closed
# (fingers overlap by 5 mm), 0.535 = fully open (45 mm). The real command
# still closes fully; the object stops the fingers at about this width.
GRIP_PLANNING_VALUE = {'chocobar': -0.0334, 'mentos_pair': -0.1169}
# 180 deg about (1,1,0)/sqrt2: maps local X->TCP Y, Y->TCP X, Z->TCP -Z.
_Q_OBJECT = (math.sqrt(.5), math.sqrt(.5), 0.0, 0.0)
# MoveIt cylinder axis is local Z; turn it onto the object's long X.
_Q_CYL_AXIS = (0.0, math.sin(math.pi/4), 0.0, math.cos(math.pi/4))


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz)


def _object_to_tcp(x_mm, y_mm, z_from_bottom_mm, arm):
    """Object-local point (X long, Y short, Z up from bottom) in TCP metres."""
    bottom_z = TIP_OFFSET_MM[arm] + TIP_ABOVE_BOTTOM_MM
    return (y_mm/1000.0, x_mm/1000.0, (bottom_z - z_from_bottom_mm)/1000.0)


def attached_object(object_type, arm, inflate_mm=0.0, inflate_long_mm=None):
    """inflate_long_mm: per-side growth along the long side (default inflate_mm)."""
    long_mm = inflate_mm if inflate_long_mm is None else inflate_long_mm
    from geometry_msgs.msg import Pose
    from moveit_msgs.msg import AttachedCollisionObject, CollisionObject
    from shape_msgs.msg import SolidPrimitive
    catalog = yaml.safe_load(CATALOG.read_text())
    if object_type not in catalog:
        raise ValueError(f'Unknown object_type {object_type!r}; choose {list(catalog)}')
    if arm not in TIP_OFFSET_MM:
        raise ValueError(f'Invalid arm {arm!r}')
    shape = catalog[object_type]
    obj = CollisionObject()
    obj.id = held_id(arm)
    obj.header.frame_id = f'{arm}_TCP'
    obj.operation = CollisionObject.ADD
    obj.type.key = object_type  # read back by held_type()

    def add(primitive, xyz, quat):
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = xyz
        (pose.orientation.x, pose.orientation.y,
         pose.orientation.z, pose.orientation.w) = quat
        obj.primitives.append(primitive)
        obj.primitive_poses.append(pose)

    if shape['kind'] == 'box':
        length, width, height = (float(v) for v in shape['size_mm'])
        add(SolidPrimitive(type=SolidPrimitive.BOX,
                           dimensions=[(length+2*long_mm)/1000, (width+2*inflate_mm)/1000,
                                       (height+2*inflate_mm)/1000]),
            _object_to_tcp(0.0, 0.0, height/2, arm), _Q_OBJECT)
    elif shape['kind'] == 'two_cylinders':
        length = float(shape['cylinder_length_mm'])
        diameter = float(shape['cylinder_diameter_mm'])
        for cx, cy in shape['centers_xy_mm']:
            add(SolidPrimitive(type=SolidPrimitive.CYLINDER,
                               dimensions=[(length+2*long_mm)/1000,
                                           (diameter/2+inflate_mm)/1000]),
                _object_to_tcp(float(cx), float(cy), diameter/2, arm),
                _qmul(_Q_OBJECT, _Q_CYL_AXIS))
    else:
        raise ValueError(f'Unknown object shape {shape["kind"]!r}')
    attached = AttachedCollisionObject()
    attached.link_name = f'{arm}_TCP'
    # Everything rigidly carried by the wrist moves with the object, so their
    # mutual contact is not a real collision.
    attached.touch_links = [f'{arm}_TCP', f'{arm}_gripper_base', f'{arm}_6_Link',
                            f'{arm}_jetcocam'] + [
        f'{arm}_gripper_{side}{index}' for side in ('left', 'right') for index in (1, 2, 3)]
    attached.object = obj
    return attached


def _current(node, wait):
    """(world object ids, {object id: attached link}) from the live scene."""
    from moveit_msgs.msg import PlanningSceneComponents
    from moveit_msgs.srv import GetPlanningScene
    client = cached_client(node, GetPlanningScene, '/jetcobot_dual/get_planning_scene')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt get_planning_scene unavailable')
    request = GetPlanningScene.Request()
    request.components.components = (PlanningSceneComponents.WORLD_OBJECT_NAMES |
                                      PlanningSceneComponents.ROBOT_STATE_ATTACHED_OBJECTS)
    scene = wait(client.call_async(request), 8.0).scene
    world = {item.id for item in scene.world.collision_objects}
    attached = {item.object.id: item.link_name
                for item in scene.robot_state.attached_collision_objects}
    return world, attached


def scene_request(world, attached, object_type=None, arm=None, remove=False,
                  inflate_mm=0.0, inflate_long_mm=None,
                  remove_legacy_world=False):
    """Attach or remove the held object. MoveIt rejects the whole diff if it
    is asked to remove something that does not exist, so only existing
    entries are removed."""
    from moveit_msgs.msg import AttachedCollisionObject, CollisionObject, PlanningScene
    from moveit_msgs.srv import ApplyPlanningScene
    request = ApplyPlanningScene.Request()
    request.scene = PlanningScene()
    request.scene.is_diff = True
    request.scene.robot_state.is_diff = True
    target = held_id(arm)
    # The camera target uses the old unsuffixed ID. Remove that world object
    # when attaching, but never remove the peer arm's attached object.
    world_ids = ({target, OBJECT_ID} if not remove or remove_legacy_world
                 else {target}) & world
    for object_name in world_ids:
        world_remove = CollisionObject()
        world_remove.id = object_name
        world_remove.header.frame_id = 'right_base_link'
        world_remove.operation = CollisionObject.REMOVE
        request.scene.world.collision_objects.append(world_remove)
    for object_name in (target, OBJECT_ID):
        link = attached.get(object_name)
        if link is None:
            continue
        if object_name == OBJECT_ID and link != f'{arm}_TCP':
            continue
        if remove or object_name != target or link != f'{arm}_TCP':
            detach = AttachedCollisionObject()
            detach.link_name = link
            detach.object.id = object_name
            detach.object.operation = CollisionObject.REMOVE
            request.scene.robot_state.attached_collision_objects.append(detach)
    if not remove:
        request.scene.robot_state.attached_collision_objects.append(
            attached_object(object_type, arm, inflate_mm, inflate_long_mm))
    return request


def apply(node, wait, object_type=None, arm=None, remove=False, inflate_mm=0.0,
          inflate_long_mm=None):
    """wait(future, timeout) -> result; raises if MoveIt refuses."""
    from moveit_msgs.srv import ApplyPlanningScene
    world, attached = _current(node, wait)
    target = held_id(arm)
    legacy_here = attached.get(OBJECT_ID) == f'{arm}_TCP'
    if remove and target not in world and target not in attached and not legacy_here:
        print('HELD OBJECT: nothing to remove.', flush=True)
        return
    client = cached_client(node, ApplyPlanningScene, '/jetcobot_dual/apply_planning_scene')
    if not client.wait_for_service(timeout_sec=6.0):
        raise RuntimeError('MoveIt apply_planning_scene unavailable')
    reply = wait(client.call_async(scene_request(world, attached, object_type, arm, remove,
                                                   inflate_mm, inflate_long_mm)), 8.0)
    if not reply.success:
        raise RuntimeError('MoveIt rejected held-object scene change; see move_group log')
    if remove:
        # MoveIt turns a detached object into a world object at its last pose
        # (2026-11-19: the released chocobar stayed floating and blocked the
        # return). Delete that leftover in a second request.
        world, attached = _current(node, wait)
        leftovers = ({target, OBJECT_ID} if legacy_here else {target}) & world
        if leftovers:
            reply = wait(client.call_async(scene_request(
                world, {}, arm=arm, remove=True,
                remove_legacy_world=legacy_here)), 8.0)
            if not reply.success:
                raise RuntimeError('MoveIt kept the released object in the world scene')
        world, attached = _current(node, wait)
        if (target in world or target in attached or
                (legacy_here and OBJECT_ID in world) or
                attached.get(OBJECT_ID) == f'{arm}_TCP'):
            raise RuntimeError('Released object still present in MoveIt scene')
    print(('HELD OBJECT removed from MoveIt.' if remove else
           f'HELD OBJECT {object_type} attached to {arm}_TCP '
           f'(long side along TCP Y, +{inflate_mm if inflate_long_mm is None else inflate_long_mm:.0f} mm '
           f'per long side, +{inflate_mm:.0f} mm other sides).'), flush=True)


def attached_messages(node, wait):
    """Attached objects in the live scene (for RViz trajectory display)."""
    from moveit_msgs.msg import PlanningSceneComponents
    from moveit_msgs.srv import GetPlanningScene
    client = cached_client(node, GetPlanningScene, '/jetcobot_dual/get_planning_scene')
    if not client.wait_for_service(timeout_sec=6.0):
        return []
    request = GetPlanningScene.Request()
    request.components.components = PlanningSceneComponents.ROBOT_STATE_ATTACHED_OBJECTS
    # A lost DDS response ('failed to send response ... timeout' in move_group,
    # 2026-11-20) made the place worker exit; one fresh request recovers it.
    for attempt in range(2):
        try:
            return list(wait(client.call_async(request), 8.0 if attempt == 0 else 5.0)
                        .scene.robot_state.attached_collision_objects)
        except RuntimeError as exc:
            if 'timed out' not in str(exc) or attempt:
                raise
            print('get_planning_scene response lost; retrying once.', flush=True)


def held_type(node, wait, arm):
    """object_type attached to arm_TCP, or None."""
    for item in attached_messages(node, wait):
        if item.object.id in (held_id(arm), OBJECT_ID) and item.link_name == f'{arm}_TCP':
            return item.object.type.key or None
    return None


def planning_grip_value(node, wait, arm, default=-0.83):
    """Gripper joint value to plan with: object width if one is held."""
    kind = held_type(node, wait, arm)
    value = GRIP_PLANNING_VALUE.get(kind, default)
    if kind:
        print(f'HELD OBJECT {kind}: planning with {arm} gripper at {value:+.3f} '
              '(fingers at object width).', flush=True)
    return value
