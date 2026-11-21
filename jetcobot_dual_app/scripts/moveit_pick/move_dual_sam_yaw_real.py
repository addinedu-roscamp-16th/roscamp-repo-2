#!/usr/bin/env python3
"""Plan both real JetCobot arms; keep peer still when possible, otherwise yield then approach.

MoveIt plans each motion with the other arm in the collision scene. The real
sdk_drag_hybrid gate chooses endpoint send_goal for a clear direct joint line,
or drag along the checked MoveIt detour when that line collides.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from dual_arm_coordinator_real import DualArmCoordinator, StopRequested, load_target


def target_digest(data):
    fields=('frame','center_xyz_mm','closing_axis_yaw_deg','yaw_by_side',
            'selected_yaw_camera','capture_time_unix','observation_above_mm','approach_mode',
            'multi_object')
    payload={key:data.get(key) for key in fields}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target-file',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path)
    ap.add_argument('--above-mm',type=float,default=40.)
    ap.add_argument('--yaw-camera',choices=('auto','left','right'),default='auto')
    ap.add_argument('--tcp-closing-axis',choices=('x','y'),default='x')
    ap.add_argument('--yaw-offset-deg',type=float,default=0.)
    ap.add_argument('--allow-gripper-tilt',action='store_true',
                    help='Relax the default vertical TCP orientation tolerance from 2 to 8 degrees')
    ap.add_argument('--peer-mode',choices=('auto','hold','yield'),default='auto')
    ap.add_argument('--velocity-scale',type=float,default=.10)
    ap.add_argument('--acceleration-scale',type=float,default=.10)
    ap.add_argument('--plan-time',type=float,default=3.)
    ap.add_argument('--max-plans',type=int,default=2)
    ap.add_argument('--planning-budget-sec',type=float,default=25.)
    ap.add_argument('--max-capture-age',type=float,default=120.)
    ap.add_argument('--max-state-change-deg',type=float,default=1.)
    ap.add_argument('--execute-saved-plan',action='store_true')
    args=ap.parse_args()
    if not 10<=args.above_mm<=100 or not 0<args.velocity_scale<=.2 or not 0<args.acceleration_scale<=.2:
        ap.error('Invalid approach distance or motion scale')
    if not 1<=args.max_plans<=2 or not .5<=args.plan_time<=10 or not 5<=args.planning_budget_sec<=120:
        ap.error('Invalid planning budget')
    target=load_target(args.target_file,args.above_mm,args.max_capture_age,args.yaw_camera)
    data=target['data'];folder=args.out_dir or args.target_file.parent
    folder.mkdir(parents=True,exist_ok=True)
    urdf=Path(data['urdf'])
    if not urdf.is_file():raise RuntimeError(f'Captured URDF missing: {urdf}')
    digest=target_digest(data)
    lock_path=Path('/tmp')/f'jetcobot_dual_auto_{os.environ.get("ROS_DOMAIN_ID","0")}.lock'
    with lock_path.open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another dual auto worker is active')
        coordinator=DualArmCoordinator(urdf,args.velocity_scale,args.acceleration_scale,
                                       args.plan_time,args.max_plans,args.planning_budget_sec)
        try:
            from rclpy.serialization import serialize_message,deserialize_message
            from moveit_msgs.msg import MotionPlanResponse
            summary_path=folder/'moveit_plan_summary.json'
            if args.execute_saved_plan:
                summary=json.loads(summary_path.read_text())
                if (not summary.get('dual_auto_real') or summary.get('phase')!='approach' or
                        summary.get('target_digest')!=digest or
                        abs(summary.get('above_mm',-1)-args.above_mm)>1e-6 or
                        bool(summary.get('allow_gripper_tilt',False)) != args.allow_gripper_tilt):
                    raise RuntimeError('Saved dual-arm plan does not match this capture; plan again')
                phases=[]
                for row in summary['phases']:
                    result=deserialize_message((folder/row['file']).read_bytes(),MotionPlanResponse)
                    phases.append({'side':row['side'],'result':result,'start':row['start'],
                                   'route':row['route']})
                chosen={'phases':phases,'selected_arm':summary['selected_arm'],
                        'yield_strategy':summary['yield_strategy'],'cost_deg':summary['path_cost_deg']}
                current=coordinator.ready()
                drift=max(abs(math.degrees(current[n]-phases[0]['start'][n]))
                          for n in phases[0]['start'])
                if drift>args.max_state_change_deg:
                    raise RuntimeError(f'Both arms changed {drift:.1f} deg since preview; plan again')
                if (folder/'approach_complete.json').exists():
                    raise RuntimeError('This capture already completed approach; recapture')
                print(f'EXECUTE SAVED: {chosen["selected_arm"].upper()} arm; '
                      f'peer={chosen["yield_strategy"]}; routes={[p["route"] for p in phases]}',flush=True)
                reached=coordinator.execute(chosen)
                side=chosen['selected_arm']
                endpoint={f'{side}_{i}_Joint':reached[f'{side}_{i}_Joint'] for i in range(1,7)}
                (folder/'approach_complete.json').write_text(json.dumps({
                    'arm':side,'endpoint_rad':endpoint,'peer_strategy':chosen['yield_strategy']}))
                summary['executed']=True
                summary_path.write_text(json.dumps(summary,indent=2))
                print(f'APPROACH COMPLETE: {side.upper()}',flush=True)
            else:
                current=coordinator.ready()
                captured=data.get('joint_positions_rad',{})
                # Background planning can specify the projected return state.
                # Saved-plan execution checks it against both measured arms.
                assumed=data.get('multi_object', {}).get('assumed_start_rad') or {}
                if captured:
                    drift=max((abs(math.degrees(current[n]-float(captured[n])))
                               for n in current if n not in assumed),default=0.)
                    if drift>args.max_state_change_deg:
                        raise RuntimeError(f'Arms changed {drift:.1f} deg since camera capture; recapture')
                if assumed:
                    print(f'PLAN FROM ASSUMED START: {len(assumed)} projected return joints', flush=True)
                required_arm=data.get('multi_object', {}).get('assigned_arm')
                peer_mode = ('hold' if data.get('multi_object', {}).get('peer_holding')
                             else args.peer_mode)
                chosen=coordinator.choose(target,args.tcp_closing_axis,args.yaw_offset_deg,
                                          peer_mode,args.allow_gripper_tilt,
                                          required_arm=required_arm,start_override=assumed)
                rows=[]
                for index,phase in enumerate(chosen['phases']):
                    name=f'dual_phase_{index+1}.cdr'
                    (folder/name).write_bytes(serialize_message(phase['result']))
                    rows.append({'side':phase['side'],'route':phase['route'],
                                 'start':phase['start'],'file':name,
                                 'points':len(phase['result'].trajectory.joint_trajectory.points)})
                summary={'dual_auto_real':True,'phase':'approach','selected_arm':chosen['selected_arm'],
                         'yield_strategy':chosen['yield_strategy'],'path_cost_deg':chosen['cost_deg'],
                         'distance_m':chosen['distance_m'],'yaw_deg':chosen['yaw_deg'],
                         'target_digest':digest,'above_mm':args.above_mm,
                          'allow_gripper_tilt':args.allow_gripper_tilt,'phases':rows,
                         'execution_allowed':True,'execution_block_reasons':[],
                         'executed':False}
                summary_path.write_text(json.dumps(summary,indent=2))
                coordinator.display(chosen)
                print(f'SELECTED {chosen["selected_arm"].upper()}: peer={chosen["yield_strategy"]}, '
                      f'route={[p["route"] for p in chosen["phases"]]}, '
                      f'cost={chosen["cost_deg"]:.1f} deg. PLAN ONLY.',flush=True)
        finally:coordinator.close()
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except StopRequested:
        print('DUAL AUTO STOP REQUESTED',flush=True)
        sys.exit(130)
    except Exception as exc:
        print(f'DUAL AUTO ERROR {type(exc).__name__}: {exc}',flush=True)
        sys.exit(1)
