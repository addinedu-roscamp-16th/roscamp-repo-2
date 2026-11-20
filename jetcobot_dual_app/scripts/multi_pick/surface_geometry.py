"""Estimate a rectangular horizontal surface from sufficiently dense 3D matches."""
import cv2
import numpy as np


def planar_points_rectangle(points, min_points=20, max_height_spread=.005):
    points=np.asarray(points,dtype=np.float64)
    if points.ndim!=2 or points.shape[1]!=3 or len(points)<min_points or not np.isfinite(points).all():
        return None
    if np.ptp(points[:,2])>max_height_spread:
        return None
    rectangle=cv2.minAreaRect(points[:,:2].astype(np.float32))
    corners=cv2.boxPoints(rectangle).astype(np.float64)
    edges=[corners[1]-corners[0],corners[2]-corners[1]]
    edges.sort(key=np.linalg.norm,reverse=True)
    length,width=map(lambda edge:float(np.linalg.norm(edge)),edges)
    if width<.005 or length<.01 or length/width<1.3:return None
    yaw=(np.rad2deg(np.arctan2(edges[0][1],edges[0][0]))+90)%180-90
    return dict(center_xy=np.array(rectangle[0]),yaw_deg=float(yaw),aspect=length/width,
                corners_xy=corners,length_m=length,width_m=width)
