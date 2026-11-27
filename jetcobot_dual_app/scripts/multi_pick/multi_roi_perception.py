"""Multi-ROI stereo perception for several identical objects (real robot copy).

Copied from the Gazebo package jetcobot_mult_geazbo/scripts/multi_roi_perception.py
(2026-11-19) and adapted: helper functions come from the real pick UI module
moveit_pick/sam_dual_yaw_moveit.py; footprint and spread limits are settings
(the Gazebo box was 40x20 mm, the chocobar is ~90x35 mm). Top-surface height
is a prior: floor_z_m + object_height_mm.
"""
import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cv2
import numpy as np
import torch
from multi_roi_geometry import associate,epipolar_errors,validate_rois,plane_transfer,rays_on_plane,pair_color
from rotation_matching import rotate_crop,restore_points
from surface_geometry import planar_points_rectangle


class PerceptionCancelled(Exception):pass


class MultiROIPerception:
    def __init__(self,predictor,extractor,matcher,device,settings):
        import sam_dual_yaw_moveit as sam
        self.sam=sam;self.predictor=predictor;self.extractor=extractor
        self.matcher=matcher;self.device=device;self.args=settings

    def native_points(self,variant,points):
        if 'plane_patch' not in variant:return restore_points(points,variant['inverse'],variant['origin'])
        origin,step,z,camera=variant['plane_patch']
        xy=np.asarray(points,dtype=float)*step+origin
        world=np.column_stack([xy,np.full(len(xy),z)])
        return self.sam.project_points(world,camera)[0]

    def rectified_pair(self,images,masks,cameras):
        """Bring both top surfaces to a common metric plane before feature matching."""
        z=self.args.floor_z_m+self.args.object_height_mm/1000.;bounds=[]
        for mask,camera in zip(masks,cameras):
            contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            if not contours:return None
            pixels=np.concatenate(contours).reshape(-1,2)
            world,valid=rays_on_plane(pixels,camera,self.sam.K,self.sam.DIST,z);world=world[valid]
            if len(world)<4:return None
            bounds.append((world[:,:2].min(axis=0),world[:,:2].max(axis=0)))
        low=np.maximum(bounds[0][0],bounds[1][0]);high=np.minimum(bounds[0][1],bounds[1][1])
        extent=high-low;step=.00015
        if np.min(extent)<.005 or np.max(extent)>.15:return None
        width,height=np.ceil(extent/step).astype(int)+1
        u,v=np.meshgrid(np.arange(width),np.arange(height))
        xy=np.column_stack([u.ravel(),v.ravel()])*step+low
        world=np.column_stack([xy,np.full(len(xy),z)]);patches=[];warped_masks=[]
        for image,mask,camera in zip(images,masks,cameras):
            pixels,depth=self.sam.project_points(world,camera)
            mapx=pixels[:,0].reshape(height,width).astype(np.float32)
            mapy=pixels[:,1].reshape(height,width).astype(np.float32)
            patches.append(cv2.remap(image,mapx,mapy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT))
            warped_masks.append(cv2.remap(mask.astype(np.uint8),mapx,mapy,cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT))
        common=(warped_masks[0]&warped_masks[1]).astype(np.uint8)
        if common.sum()*step**2<.00012:return None
        variants=[]
        for patch,camera in zip(patches,cameras):
            try:
                features,_,kept=self.sam.mask_filtered_features(self.extractor,patch,common,(0,0,width,height),
                    self.device,self.args.mask_erode,self.args.max_keypoints)
            except RuntimeError:return None
            variants.append({'features':features,'angle':0,'mode':'plane_rectified','kept':kept,
                             'plane_patch':(low,step,z,camera)})
        return tuple(variants)

    def run(self,images,rois,cameras,folder,metadata,forced=None,cancel=None,progress=print,masks_in=None,classes=None):
        """masks_in/classes: per-side lists from YOLO-seg (2026-11-24); None = SAM on each ROI."""
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        rois=[validate_rois(r,im.shape) for r,im in zip(rois,images)]
        if len(images)!=2 or len(rois)!=2:raise ValueError('Two frozen camera images are required')
        def check():
            if cancel is not None and cancel.is_set():raise PerceptionCancelled()
        def note(message):
            check();progress(message)
        def subset(variant,other_mask):
            uv,valid=variant['other_pixels'];h,w=other_mask.shape
            xy=np.rint(np.nan_to_num(uv,nan=-1000,posinf=-1000,neginf=-1000)).astype(int)
            valid&=(xy[:,0]>=0)&(xy[:,0]<w)&(xy[:,1]>=0)&(xy[:,1]<h)
            expanded=cv2.dilate(other_mask.astype(np.uint8),np.ones((17,17),np.uint8))
            keep=np.zeros(len(valid),bool);keep[valid]=expanded[xy[valid,1],xy[valid,0]]>0
            indices=torch.as_tensor(np.flatnonzero(keep),device=variant['features']['keypoints'].device)
            value={}
            count=len(keep)
            for key,v in variant['features'].items():
                value[key]=v.index_select(1,indices) if key!='image_size' and torch.is_tensor(v) and v.ndim>=2 and v.shape[1]==count else v
            return value,len(indices)
        for side,image in zip(('left','right'),images):cv2.imwrite(str(folder/f'{side}.png'),image)
        masks=[[],[]];features=[[],[]];roi_errors=[]
        for side in range(2):
            for index,roi in enumerate(rois[side]):
                note(f'{"YOLO" if masks_in is not None else "SAM"} {("LEFT","RIGHT")[side]} ROI {index+1}/{len(rois[side])}')
                try:
                    if masks_in is not None:mask=masks_in[side][index]
                    else:mask,sam_score=self.sam.segment_with_sam(self.predictor,images[side],roi)
                except RuntimeError as exc:
                    masks[side].append(np.zeros(images[side].shape[:2],np.uint8));features[side].append([])
                    roi_errors.append({'side':('left','right')[side],'roi':index+1,'error':str(exc)});continue
                masks[side].append(mask)
                cv2.imwrite(str(folder/f'{("left","right")[side]}_{index+1}_mask.png'),mask.astype(np.uint8)*255)
                variants=[]
                angles=(0,) if side==0 else (0,90,180,270)
                for angle in angles:
                    check()
                    x1,y1,x2,y2=roi
                    rotated,rotmask,inverse=rotate_crop(images[side][y1:y2,x1:x2],mask[y1:y2,x1:x2],angle)
                    h,w=rotated.shape[:2]
                    try:
                        value,_,kept=self.sam.mask_filtered_features(self.extractor,rotated,rotmask,(0,0,w,h),
                            self.device,self.args.mask_erode,self.args.max_keypoints)
                        pixels=restore_points(value['keypoints'][0].detach().cpu().numpy(),inverse,(x1,y1))
                        mapped=plane_transfer(pixels,cameras[side],cameras[1-side],self.sam.K,self.sam.DIST,
                            self.args.floor_z_m+self.args.object_height_mm/1000.)
                        variants.append({'features':value,'inverse':inverse,'origin':(x1,y1),'angle':angle,'kept':kept,'other_pixels':mapped})
                    except RuntimeError as exc:progress(f'ROI {side}/{index+1} angle {angle}: {exc}')
                features[side].append(variants)
        scores=np.full((len(rois[0]),len(rois[1])),np.nan);candidates={};pair_reports=[]
        for i,left_variants in enumerate(features[0]):
            for j,right_variants in enumerate(features[1]):
                note(f'MATCH L{i+1} - R{j+1} ({i*len(rois[1])+j+1}/{scores.size})')
                if classes is not None and classes[0][i]!=classes[1][j]:
                    pair_reports.append({'left_index':i,'right_index':j,'matches':0,'valid_points':0,
                        'eligible':False,'reason':f'class mismatch {classes[0][i]} vs {classes[1][j]}'})
                    continue
                trials=[]
                variants=[(l,r) for l in left_variants for r in right_variants]
                rectified=self.rectified_pair(images,[masks[0][i],masks[1][j]],cameras)
                if rectified:variants.insert(0,rectified)
                for left,right in variants:
                    check()
                    from lightglue.utils import rbd
                    # Intersect stereo silhouettes on the known top plane before matching.
                    # This suppresses repeated side-wall patterns and impossible ROI pairs.
                    if 'plane_patch' in left:
                        left_features=left['features'];right_features=right['features'];nl=left['kept'];nr=right['kept']
                    else:
                        left_features,nl=subset(left,masks[1][j]);right_features,nr=subset(right,masks[0][i])
                    if min(nl,nr)<self.args.min_valid_points:
                        pair_reports.append({'left_index':i,'right_index':j,'rotation_deg':right['angle'],
                            'matches':0,'valid_points':0,'eligible':False,'reason':'insufficient shared top-plane features'})
                        continue
                    with torch.inference_mode():output=self.matcher({'image0':left_features,'image1':right_features})
                    f0,f1,matched=map(rbd,(left_features,right_features,output));pairs=matched['matches']
                    p0=self.native_points(left,f0['keypoints'][pairs[:,0]].cpu().numpy())
                    p1=self.native_points(right,f1['keypoints'][pairs[:,1]].cpu().numpy())
                    tri=self.sam.triangulate_matches(p0,p1,cameras[0],cameras[1],self.args.reproj_px,self.args.min_angle_deg)
                    epi=epipolar_errors(p0,p1,cameras,self.sam.K,self.sam.DIST)
                    top=self.args.floor_z_m+self.args.object_height_mm/1000.
                    geometric=tri['valid']&(epi<=self.args.epipolar_px)
                    # 2026-11-24: right-arm return scatters 1-2 deg; the camera pose then
                    # biases triangulated height by 15-35 mm. Center the height window on
                    # this pair's measured stereo height when it stays near the prior.
                    max_offset=getattr(self.args,'max_height_offset_mm',0.)/1000.
                    if max_offset>0 and geometric.sum()>=self.args.min_valid_points:
                        measured=float(np.median(tri['xyz_m'][geometric,2]))
                        if abs(measured-top)<=max_offset:top=measured
                    valid=geometric&(np.abs(tri['xyz_m'][:,2]-top)<=self.args.height_tolerance_mm/1000.)
                    tri['valid']=valid;count=int(valid.sum());ratio=int(geometric.sum())/max(1,len(p0))
                    trial={'left_index':i,'right_index':j,'rotation_deg':right['angle'],'matches':len(p0),
                           'top_height_used_mm':top*1000,
                           'matching_mode':right.get('mode','image_rotation'),
                           'valid_points':count,'valid_ratio':ratio,'eligible':False,'reason':'too few geometric top-surface matches'}
                    if count>=self.args.min_valid_points and ratio>=self.args.min_valid_ratio:
                        points=tri['xyz_m'][valid]
                        span=np.ptp(points[:,:2],axis=0)
                        if np.linalg.norm(span)>self.args.max_spread_m:
                            trial['reason']='3D point spread exceeds box dimensions'
                        else:
                            # Use only points on the configured top plane, never the background/side wall.
                            # Whole-body silhouettes projected at top height exaggerate the
                            # footprint. Fit the stereo-confirmed horizontal top instead.
                            median_z=float(np.median(points[:,2]))
                            refined=valid&(np.abs(tri['xyz_m'][:,2]-median_z)<=.0025)
                            selected=tri['xyz_m'][refined].copy()
                            trial['pose_fit_points']=len(selected)
                            if len(selected):selected[:,2]=median_z
                            surface=planar_points_rectangle(selected,min_points=self.args.min_valid_points)
                            preview=None;reason='No stable horizontal top rectangle'
                            if surface is not None:
                                preview={'center_xyz_m':np.r_[surface['center_xy'],median_z],
                                    'object_long_yaw_deg':surface['yaw_deg'],
                                    'closing_axis_yaw_deg':self.sam.normalize_yaw_deg(surface['yaw_deg']+90.),
                                    'length_m':surface['length_m'],'width_m':surface['width_m'],
                                    'warnings':['Known upright height/floor prior used; sparse top features may bias the center']}
                            if preview is not None:
                                length=preview.get('length_m');width=preview.get('width_m')
                                trial['footprint_mm']=[length*1000,width*1000]
                                if (length is None or width is None or
                                        not self.args.length_range_m[0]<=length<=self.args.length_range_m[1] or
                                        not self.args.width_range_m[0]<=width<=self.args.width_range_m[1]):
                                    trial['reason']='unstable or implausible footprint dimensions'
                                else:
                                    error=float(np.median(np.maximum(tri['reproj_left'][valid],tri['reproj_right'][valid])))
                                    score=float(count*ratio/(1+error))
                                    trial.update(eligible=True,reason='passed',score=score,median_reprojection_px=error,
                                                 median_epipolar_px=float(np.median(epi[valid])))
                                    trials.append((score,trial,preview,p0,p1,tri))
                            else:trial['reason']=reason
                    pair_reports.append(trial)
                if trials:
                    best=max(trials,key=lambda x:x[0]);scores[i,j]=best[0];candidates[(i,j)]=best
        check()
        associations=associate(scores,forced,self.args.assignment_margin)
        report={'schema':'jetcobot_multi_roi_v1','frame':'right_base_link','capture':metadata,
                'rois':{'left':rois[0],'right':rois[1]},'segmentation':'yolo' if masks_in is not None else 'sam',
                'classes':classes,'known_priors':{
                    'object_height_mm':self.args.object_height_mm,
                    'floor_z_m':self.args.floor_z_m,'top_height_tolerance_mm':self.args.height_tolerance_mm},
                'association':associations,'pair_trials':pair_reports,'roi_errors':roi_errors,'objects':[],
                'motion_executed':False}
        overlay=np.hstack([im.copy() for im in images]);w=images[0].shape[1]
        for side in range(2):
            for index,roi in enumerate(rois[side]):
                if any(p['left_index' if side==0 else 'right_index']==index for p in associations['pairs']):continue
                x1,y1,x2,y2=roi;x1+=side*w;x2+=side*w
                cv2.rectangle(overlay,(x1,y1),(x2,y2),(180,180,180),1)
                cv2.putText(overlay,f'{("L","R")[side]}{index+1}',(x1,max(16,y1)),cv2.FONT_HERSHEY_SIMPLEX,.55,(180,180,180),2)
        for association in associations['pairs']:
            check();i=association['left_index'];j=association['right_index'];_,trial,preview,p0,p1,tri=candidates[i,j]
            accepted=association['status']!='ambiguous';color=pair_color(i) if accepted else (0,150,255)
            centers=[((rois[side][idx][0]+rois[side][idx][2])//2+side*w,
                      (rois[side][idx][1]+rois[side][idx][3])//2) for side,idx in ((0,i),(1,j))]
            for side,index in ((0,i),(1,j)):
                contours,_=cv2.findContours(masks[side][index].astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:contour[:,0,0]+=side*w
                tint=overlay.copy();cv2.drawContours(tint,contours,-1,color,-1)
                overlay[:]=cv2.addWeighted(overlay,.75,tint,.25,0)
                cv2.drawContours(overlay,contours,-1,color,2)
            cv2.line(overlay,centers[0],centers[1],color,2)
            label=f'#{i+1} L{i+1}-R{j+1}' if accepted else f'? L{i+1}-R{j+1}'
            for side,index in ((0,i),(1,j)):
                x1,y1,x2,y2=rois[side][index];x1+=side*w;x2+=side*w
                cv2.rectangle(overlay,(x1,y1),(x2,y2),color,2)
                cv2.putText(overlay,label,(x1,max(16,y1)),cv2.FONT_HERSHEY_SIMPLEX,.55,color,2)
            if not accepted:continue
            # Center/yaw like the single-object UI: SAM mask projected onto the
            # stereo median height. The sparse-feature rectangle above only
            # covered the textured part (66-72 x 28-32 mm for a 90 x 35 mm
            # bar), biasing the center and yaw (2026-11-19).
            # Same triangulation as the single-object UI (only the matching
            # differs): all LightGlue matches of this pair, reprojection /
            # ray-angle limits of the single UI, no epipolar/top-height filter.
            tri=self.sam.triangulate_matches(p0,p1,cameras[0],cameras[1],
                                             self.args.single_reproj_px,self.args.single_min_angle_deg)
            single,single_msg=self.sam.estimate_grasp_preview([masks[0][i],masks[1][j]],cameras,tri,
                                                             self.args.single_min_valid_points,
                                                             footprint_ranges=(self.args.length_range_m,
                                                                               self.args.width_range_m))
            yaw_by_side=None;yaw_camera=None
            if single is not None:
                preview=dict(preview,center_xyz_m=np.asarray(single['center_xyz_m'],float),
                             object_long_yaw_deg=float(single['object_long_yaw_deg']),
                             closing_axis_yaw_deg=float(single['closing_axis_yaw_deg']),
                             length_m=single['length_m'] or preview['length_m'],
                             width_m=single['width_m'] or preview['width_m'],
                             warnings=list(single['warnings']))
                yaw_by_side=single['yaw_by_side'];yaw_camera=single['selected_yaw_camera']
                progress(f'#{i+1}: mask footprint {single["length_m"]*1000:.0f} x {single["width_m"]*1000:.0f} mm '
                         f'({single["source"]})')
            else:
                progress(f'#{i+1}: mask footprint unavailable ({single_msg}); using feature rectangle')
            if getattr(self.args,'center_source','stereo')=='left':
                # 2026-11-24: the right camera pose drifts with the right-arm return
                # (1-2 deg, wrong hand-eye) and biases stereo height/XY by 10-20 mm.
                # Use the LEFT mask on the known top plane for the center; stereo
                # is kept for association and yaw.
                top=self.args.floor_z_m+self.args.object_height_mm/1000.
                contours,_=cv2.findContours(masks[0][i].astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
                if contours:
                    pixels=max(contours,key=cv2.contourArea).reshape(-1,2).astype(float)
                    world,ok=rays_on_plane(pixels,cameras[0],self.sam.K,self.sam.DIST,top)
                    if int(np.sum(ok))>=20:
                        (cx,cy),(rw,rh),_=cv2.minAreaRect((world[ok][:,:2]*1000).astype(np.float32))
                        length_mm, width_mm = max(rw, rh), min(rw, rh)
                        min_length, max_length = self.args.length_range_m
                        min_width, max_width = self.args.width_range_m
                        if (min_length <= length_mm / 1000 <= max_length
                                and min_width <= width_mm / 1000 <= max_width):
                            stereo=np.asarray(preview['center_xyz_m'],float)
                            mono=np.array([cx/1000.,cy/1000.,top])
                            shift=(mono-stereo)*1000
                            progress(f'#{i+1}: LEFT-plane center {np.round(mono*1000,1).tolist()} mm '
                                     f'(stereo {np.round(stereo*1000,1).tolist()}, shift {np.round(shift,1).tolist()}), '
                                     f'footprint {length_mm:.0f} x {width_mm:.0f} mm')
                            preview=dict(preview,center_xyz_m=mono,
                                         warnings=list(preview['warnings'])+[
                                             f'center from left mask on z={top*1000:.0f} mm plane; '
                                             f'stereo center differed by {np.round(shift,1).tolist()} mm'])
                        else:
                            warning = (f'left mask footprint {length_mm:.0f} x {width_mm:.0f} mm '
                                       'outside configured dimensions; kept validated center')
                            progress(f'#{i+1}: {warning}')
                            preview = dict(preview, warnings=[*preview['warnings'], warning])
            obj={'id':f'object_{i+1}','left_roi':i+1,'right_roi':j+1,
                 'class_name':classes[0][i] if classes is not None else None,
                 'yaw_by_side':yaw_by_side,'selected_yaw_camera':yaw_camera,
                 'center_xyz_mm':(preview['center_xyz_m']*1000).tolist(),
                 'object_long_yaw_deg':float(preview['object_long_yaw_deg']),
                 'closing_axis_yaw_deg':float(preview['closing_axis_yaw_deg']),
                 'estimated_footprint_mm':[preview['length_m']*1000,preview['width_m']*1000],
                 'valid_points':trial['valid_points'],'confidence':copy.deepcopy(association),
                 'matching_mode':trial['matching_mode'],
                 'pose_fit_points':trial['pose_fit_points'],
                 'warnings':list(preview['warnings'])}
            report['objects'].append(obj)
            destination=folder/obj['id'];destination.mkdir(exist_ok=True)
            target={'frame':'right_base_link','center_xyz_mm':obj['center_xyz_mm'],
                    'closing_axis_yaw_deg':obj['closing_axis_yaw_deg'],'valid_points':obj['valid_points'],
                    'capture_time_unix':metadata['capture_time_unix'],'joint_positions_rad':metadata['joint_positions_rad'],
                    'source':str(folder),'warnings':obj['warnings']}
            (destination/'moveit_target.json').write_text(json.dumps(target,indent=2))
            self.sam.save_xyz_csv(destination/'triangulation.csv',p0,p1,tri)
            for side in range(2):
                panel=overlay[:,side*w:(side+1)*w]
                self.sam.draw_grasp_axes(panel,cameras[side],preview['center_xyz_m'],preview['object_long_yaw_deg'],preview['length_m'],preview['width_m'])
        report['all_rois_resolved']=not associations['unresolved_left'] and not associations['unresolved_right']
        check();cv2.imwrite(str(folder/'multi_roi_matches.png'),overlay)
        np.savez_compressed(folder/'stereo_capture.npz',K=self.sam.K,DIST=self.sam.DIST,cameras=np.asarray(cameras))
        (folder/'multi_roi_scene.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
        progress(f'RESULT {len(report["objects"])} confirmed objects; unresolved LEFT {associations["unresolved_left"]}, RIGHT {associations["unresolved_right"]}')
        return report,overlay
