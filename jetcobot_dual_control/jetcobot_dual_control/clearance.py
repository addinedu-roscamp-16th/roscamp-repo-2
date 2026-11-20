"""Conservative inter-arm separation test for endpoint routing.

Collision meshes are enclosed in link-centered spheres. Unknown geometry fails
closed. This is a routing heuristic, not a continuous-motion safety proof.
"""
import math
from pathlib import Path
import struct
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

import numpy as np


def _vector(text, default):
    return np.array([float(v) for v in (text or default).split()], dtype=float)


def _transform(origin):
    xyz = _vector(origin.get('xyz') if origin is not None else None, '0 0 0')
    r, p, y = _vector(origin.get('rpy') if origin is not None else None, '0 0 0')
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    out = np.eye(4)
    out[:3, :3] = np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                            [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                            [-sp, cp*sr, cp*cr]])
    out[:3, 3] = xyz
    return out


def _rotation(axis, angle):
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    out = np.eye(4)
    out[:3, :3] = np.array([
        [c+x*x*(1-c), x*y*(1-c)-z*s, x*z*(1-c)+y*s],
        [y*x*(1-c)+z*s, c+y*y*(1-c), y*z*(1-c)-x*s],
        [z*x*(1-c)-y*s, z*y*(1-c)+x*s, c+z*z*(1-c)]])
    return out


def _mesh_radius(uri, scale):
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        raise ValueError('Collision mesh must resolve to a local file')
    data = Path(unquote(parsed.path)).read_bytes()
    # STL vertex coordinates are in metres in this robot model.
    if len(data) >= 84 and 84 + 50*struct.unpack_from('<I', data, 80)[0] == len(data):
        count = struct.unpack_from('<I', data, 80)[0]
        vertices = (struct.unpack_from('<3f', data, 84+50*i+12+12*j)
                    for i in range(count) for j in range(3))
    else:
        vertices = (tuple(float(v) for v in line.split()[1:]) for line in data.decode('utf8').splitlines()
                    if line.strip().startswith('vertex '))
    points = np.array([[v*s for v, s in zip(vertex, scale)] for vertex in vertices], dtype=float)
    if points.size == 0 or not np.isfinite(points).all():
        raise ValueError('Invalid or empty collision mesh')
    center = (points.min(axis=0)+points.max(axis=0))/2
    radius = float(np.linalg.norm(points-center, axis=1).max())
    return center, radius


class ArmClearance:
    def __init__(self, robot_description, margin_m=0.10):
        if not 0.05 <= margin_m <= 0.30:
            raise ValueError('clearance margin must be 0.05..0.30 m')
        self.margin = margin_m
        robot = ET.fromstring(robot_description)
        self.joints = {}
        self.spheres = {}
        cache = {}
        for joint in robot.findall('joint'):
            parent, child = joint.find('parent'), joint.find('child')
            if parent is None or child is None:
                continue
            self.joints[child.get('link')] = (parent.get('link'), joint.get('name'), joint.get('type'),
                _transform(joint.find('origin')),
                _vector(joint.find('axis').get('xyz') if joint.find('axis') is not None else None, '0 0 1'))
        for link in robot.findall('link'):
            name = link.get('name')
            if not name.startswith(('left_', 'right_')):
                continue
            spheres = []
            for collision in link.findall('collision'):
                geometry = collision.find('geometry')
                mesh = geometry.find('mesh') if geometry is not None else None
                if mesh is None:
                    raise ValueError(f'{name}: unsupported collision geometry')
                uri = mesh.get('filename')
                scale = _vector(mesh.get('scale'), '1 1 1')
                key = (uri, tuple(scale))
                if key not in cache:
                    cache[key] = _mesh_radius(uri, scale)
                # Gripper angle is not in the measured 12-joint state. Bound its
                # unknown articulation with an additional radius around each link.
                extra = 0.08 if 'gripper' in name else 0.0
                center, radius = cache[key]
                origin = _transform(collision.find('origin'))
                spheres.append((origin[:3,:3] @ center + origin[:3,3], radius+extra))
            if spheres:
                self.spheres[name] = spheres
        if not all(any(name.startswith(side+'_') for name in self.spheres) for side in ('left','right')):
            raise ValueError('Missing inter-arm collision geometry')

    def _arm_spheres(self, side, values):
        joint_values = {f'{side}_{i}_Joint': q for i, q in enumerate(values, 1)}
        poses = {'world': np.eye(4)}
        visiting = set()
        def pose(link):
            if link in poses:
                return poses[link]
            if link in visiting or link not in self.joints:
                raise ValueError(f'Invalid URDF chain at {link}')
            visiting.add(link)
            parent, name, kind, origin, axis = self.joints[link]
            result = pose(parent) @ origin
            if kind in ('revolute', 'continuous'):
                result = result @ _rotation(axis, joint_values.get(name, 0.0))
            elif kind != 'fixed':
                raise ValueError(f'Unsupported joint: {name}')
            poses[link] = result
            visiting.remove(link)
            return result
        result = []
        for link, spheres in self.spheres.items():
            if link.startswith(side+'_'):
                transform = pose(link)
                for center, radius in spheres:
                    result.append((transform[:3,:3] @ center + transform[:3,3], radius))
        return result

    def minimum_separation(self, side, planned, peer):
        other = 'right' if side == 'left' else 'left'
        fixed = self._arm_spheres(other, peer)
        minimum = math.inf
        previous = None
        for target in planned:
            if previous is None:
                samples = [target]
            else:
                n = max(1, math.ceil(max(abs(a-b) for a,b in zip(previous,target))/math.radians(2)))
                samples = [[a+(b-a)*k/n for a,b in zip(previous,target)] for k in range(1,n+1)]
            for sample in samples:
                for center, radius in self._arm_spheres(side, sample):
                    for peer_center, peer_radius in fixed:
                        minimum = min(minimum, float(np.linalg.norm(center-peer_center))-radius-peer_radius)
            previous = target
        return minimum

    def endpoint_allowed(self, side, planned, peer):
        return self.minimum_separation(side, planned, peer) >= self.margin
