"""Reobserved image ROI to bounded base-frame XY correction for a wrist camera."""
import numpy as np
import cv2


def target_pixel_for_world_xy(camera_transform, matrix, distortion, object_z, target_xy):
    """Image location of the current TCP XY at the object's height."""
    point_world = np.array([*target_xy, float(object_z)], dtype=float)
    point_camera = (point_world - camera_transform[:3, 3]) @ camera_transform[:3, :3]
    if not np.isfinite(point_camera).all() or point_camera[2] <= 0:
        raise ValueError('TCP target plane is behind the camera')
    uv, _ = cv2.projectPoints(point_camera, np.zeros(3), np.zeros(3), matrix, distortion)
    return uv.reshape(2)


def roi_translation(roi, axis, camera_transform, matrix, distortion, object_z,
                    image_size=(640, 480), max_distance=.03, deadband_px=4,
                    target_pixel_xy=None):
    x1, y1, x2, y2 = map(int, roi)
    width, height = image_size
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError('Invalid ROI')
    if axis not in ('x', 'y'):
        raise ValueError('Axis must be x or y')
    actual = np.array([(x1 + x2) / 2, y1 if axis == 'y' else (y1 + y2) / 2], dtype=float)
    desired = actual.copy()
    index = 0 if axis == 'x' else 1
    desired[index] = (float(matrix[index, 2]) if target_pixel_xy is None
                      else float(target_pixel_xy[index]))
    if abs(actual[index] - desired[index]) <= deadband_px:
        return np.zeros(3)
    uv = np.array([actual, desired], dtype=float).reshape(-1, 1, 2)
    rays = cv2.undistortPoints(uv, matrix, distortion).reshape(-1, 2)
    rays = np.column_stack((rays, np.ones(2))) @ camera_transform[:3, :3].T
    origin = camera_transform[:3, 3]
    if np.any(np.abs(rays[:, 2]) < 1e-4):
        raise ValueError('Camera ray nearly parallel to object plane')
    distance = (float(object_z) - origin[2]) / rays[:, 2]
    if not np.isfinite(distance).all() or np.any(distance <= 0):
        raise ValueError('Object plane behind camera')
    points = origin + distance[:, None] * rays
    delta = points[0] - points[1]
    delta[2] = 0.
    length = np.linalg.norm(delta)
    if not np.isfinite(length):
        raise ValueError('Nonfinite correction')
    if length > max_distance:
        delta *= max_distance / length
    return delta


def update_direction_sign(sign, old_error_px, new_error_px, moved_deg, margin_px=4.):
    """Reverse a capture's correction sign only after observed divergence."""
    if moved_deg >= .5 and old_error_px * new_error_px > 0 and (
            abs(new_error_px) > abs(old_error_px) + margin_px):
        return -sign
    return sign
