"""Match roll-separated views, restoring image coordinates before triangulation."""
import cv2
import numpy as np
import torch


def rotate_crop(image, mask, angle):
    height,width=image.shape[:2]
    matrix=cv2.getRotationMatrix2D((width/2,height/2),angle,1.)
    cosine,sine=abs(matrix[0,0]),abs(matrix[0,1])
    out_width=round(height*sine+width*cosine)
    out_height=round(height*cosine+width*sine)
    matrix[:,2]+=[out_width/2-width/2,out_height/2-height/2]
    return (cv2.warpAffine(image,matrix,(out_width,out_height)),
            cv2.warpAffine(mask.astype(np.uint8),matrix,(out_width,out_height),flags=cv2.INTER_NEAREST),
            cv2.invertAffineTransform(matrix))


def restore_points(points, inverse, origin):
    points=np.asarray(points,dtype=np.float64).reshape(-1,2)
    if not len(points):return np.empty((0,2),dtype=np.float64)
    return cv2.transform(points.reshape(-1,1,2),inverse).reshape(-1,2)+np.asarray(origin)


def match_rotated_rois(extractor,matcher,images,masks,rois,device,erode,max_points,
                       extract,rbd,triangulate,angles=(0,90,180,270)):
    left,all_left,kept_left=extract(extractor,images[0],masks[0],rois[0],device,erode,max_points)
    print(f'LEFT SuperPoint: {all_left} total, {kept_left} inside mask',flush=True)
    x1,y1,x2,y2=rois[1]
    crop=images[1][y1:y2,x1:x2]
    mask=masks[1][y1:y2,x1:x2]
    candidates=[]
    for angle in angles:
        rotated,mask_rotated,inverse=rotate_crop(crop,mask,angle)
        h,w=rotated.shape[:2]
        try:
            right,all_right,kept_right=extract(extractor,rotated,mask_rotated,(0,0,w,h),device,erode,max_points)
            with torch.inference_mode():matched=matcher({'image0':left,'image1':right})
            f0,f1,match=map(rbd,(left,right,matched))
            pairs=match['matches']
            pts_left=f0['keypoints'][pairs[:,0]].cpu().numpy().astype(np.float64)+np.asarray(rois[0][:2])
            pts_right=restore_points(f1['keypoints'][pairs[:,1]].cpu().numpy(),inverse,(x1,y1))
            tri=triangulate(pts_left,pts_right)
            valid=int(tri['valid'].sum())
            print(f'RIGHT rotation {angle}: {kept_right}/{all_right} features, {len(pairs)} matches, {valid} valid 3D',flush=True)
            # Geometry decides which rotation is useful; never triangulate rotated pixels.
            error=(float(np.median(tri['reproj_left'][tri['valid']])) if valid else float('inf'))
            candidates.append(((valid,-error,len(pairs)),pts_left,pts_right,tri,angle))
        except RuntimeError as exc:
            print(f'Rotation {angle} skipped: {exc}',flush=True)
    if not candidates:raise RuntimeError('No usable rotation matching candidates')
    _,left_points,right_points,tri,angle=max(candidates,key=lambda c:c[0])
    print(f'Selected feature-view rotation: {angle} deg (image processing only)',flush=True)
    return left_points,right_points,tri,angle
