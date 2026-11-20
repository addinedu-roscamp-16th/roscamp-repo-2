"""Geometry and conservative one-to-one association, independent of ML/ROS."""
import math
from functools import lru_cache
import cv2
import numpy as np


PAIR_COLORS=((255,220,40),(220,100,255),(80,230,80),(255,130,70),
             (60,230,255),(180,210,255),(230,190,130),(130,100,255),
             (180,255,100),(255,120,200),(110,240,200),(200,150,100))


def pair_color(left_index):
    """Stable OpenCV BGR color shared by both ROIs of a matched object."""
    return PAIR_COLORS[left_index%len(PAIR_COLORS)]


def epipolar_errors(left,right,cameras,K,dist):
    if len(left)==0:return np.empty(0)
    transform=np.linalg.inv(cameras[1])@cameras[0];t=transform[:3,3]
    skew=np.array([[0,-t[2],t[1]],[t[2],0,-t[0]],[-t[1],t[0],0.]])
    inverse=np.linalg.inv(K);F=inverse.T@skew@transform[:3,:3]@inverse
    points=[]
    for p in (left,right):
        uv=cv2.undistortPoints(np.asarray(p,dtype=float).reshape(-1,1,2),K,dist,P=K).reshape(-1,2)
        points.append(np.column_stack([uv,np.ones(len(uv))]))
    x,y=points;lines_right=x@F.T;lines_left=y@F
    residual=np.abs(np.sum(y*lines_right,axis=1))
    return np.maximum(residual/np.maximum(np.linalg.norm(lines_right[:,:2],axis=1),1e-12),
                      residual/np.maximum(np.linalg.norm(lines_left[:,:2],axis=1),1e-12))


def rays_on_plane(pixels,source,K,dist,z):
    if not len(pixels):return np.empty((0,3)),np.empty(0,dtype=bool)
    normalized=cv2.undistortPoints(np.asarray(pixels,dtype=float).reshape(-1,1,2),K,dist).reshape(-1,2)
    rays=np.column_stack([normalized,np.ones(len(normalized))])@source[:3,:3].T
    scale=np.divide(z-source[2,3],rays[:,2],out=np.full(len(rays),np.nan),where=np.abs(rays[:,2])>1e-8)
    world=source[:3,3]+scale[:,None]*rays
    return world,np.isfinite(world).all(axis=1)&(scale>0)


def plane_transfer(pixels,source,destination,K,dist,z):
    """Project rays through a known horizontal plane into the other camera."""
    world,valid=rays_on_plane(pixels,source,K,dist,z)
    if not len(world):return np.empty((0,2)),valid
    cam=(world-destination[:3,3])@destination[:3,:3]
    valid&=np.isfinite(cam).all(axis=1)&(cam[:,2]>.01)
    uv=np.full((len(cam),2),np.nan)
    if valid.any():uv[valid]=cv2.projectPoints(cam[valid],np.zeros(3),np.zeros(3),K,dist)[0].reshape(-1,2)
    return uv,valid


def best_assignment(scores,forced=None,forbidden=None):
    """Exact maximum-weight partial matching with unmatched ROI alternatives."""
    scores=np.asarray(scores,dtype=float)
    if scores.ndim!=2 or max(scores.shape,default=0)>12:raise ValueError('At most 12 ROIs per view')
    forced=dict(forced or {});forbidden=set(forbidden or ())
    rows,cols=scores.shape
    if len(set(forced.values()))!=len(forced):raise ValueError('A right ROI cannot be paired twice')
    for i,j in forced.items():
        if not 0<=i<rows or not 0<=j<cols or not np.isfinite(scores[i,j]) or scores[i,j]<=0 or (i,j) in forbidden:
            raise ValueError('Manual pair failed geometric/feature checks')
    occupied=set(forced.values());remaining=[i for i in range(rows) if i not in forced]
    @lru_cache(None)
    def solve(index,mask):
        if index==len(remaining):return 0.,()
        i=remaining[index];best=solve(index+1,mask)
        for j in range(cols):
            value=scores[i,j]
            if j in occupied or mask&(1<<j) or (i,j) in forbidden or not np.isfinite(value) or value<=0:continue
            tail,pairs=solve(index+1,mask|(1<<j));candidate=(float(value)+tail,((i,j),)+pairs)
            if candidate[0]>best[0]:best=candidate
        return best
    score,pairs=solve(0,0)
    return score+sum(float(scores[i,j]) for i,j in forced.items()),sorted(list(pairs)+list(forced.items()))


def associate(scores,forced=None,min_margin=.20):
    """Withhold edges when a competing global assignment is almost as good."""
    matrix=np.asarray(scores,dtype=float);forced=dict(forced or {})
    total,pairs=best_assignment(matrix,forced)
    result=[]
    for i,j in pairs:
        if i in forced:margin=None;status='manual_confirmed'
        else:
            alternative,_=best_assignment(matrix,forced,{(i,j)})
            margin=max(0.,(total-alternative)/float(matrix[i,j]))
            status='accepted' if margin>=min_margin else 'ambiguous'
        result.append({'left_index':i,'right_index':j,'score':float(matrix[i,j]),'assignment_margin':margin,'status':status})
    accepted=[p for p in result if p['status']!='ambiguous']
    return {'total_score':total,'pairs':result,
            'unresolved_left':[i for i in range(matrix.shape[0]) if not any(p['left_index']==i for p in accepted)],
            'unresolved_right':[j for j in range(matrix.shape[1]) if not any(p['right_index']==j for p in accepted)]}


def validate_rois(rois,image_shape):
    h,w=image_shape[:2]
    if not 1<=len(rois)<=12:raise ValueError('Select 1 to 12 object ROIs in each image')
    out=[]
    for box in rois:
        if len(box)!=4 or any(not isinstance(v,(int,np.integer)) for v in box):raise ValueError('ROI needs four integer pixels')
        x1,y1,x2,y2=box
        if not 0<=x1<x2<=w or not 0<=y1<y2<=h or min(x2-x1,y2-y1)<16:raise ValueError('Invalid or too small ROI')
        for old in out:
            ax,ay,bx,by=old;overlap=max(0,min(x2,bx)-max(x1,ax))*max(0,min(y2,by)-max(y1,ay))
            union=(x2-x1)*(y2-y1)+(bx-ax)*(by-ay)-overlap
            if overlap/union>.8:raise ValueError('ROIs are almost duplicates; select each box once')
        out.append(tuple(map(int,box)))
    return out


def stationarity(joints_a,joints_b,tolerance_deg=.15):
    a=np.asarray(joints_a,dtype=float);b=np.asarray(joints_b,dtype=float)
    return a.shape==(2,6) and b.shape==a.shape and np.isfinite(a).all() and np.isfinite(b).all() and float(np.max(np.abs(a-b)))<=math.radians(tolerance_deg)
