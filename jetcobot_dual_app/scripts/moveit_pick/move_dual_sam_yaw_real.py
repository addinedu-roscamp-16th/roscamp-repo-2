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
from contextlib import ExitStack
from pathlib import Path

from dual_arm_coordinator_real import DualArmCoordinator, StopRequested, load_target


def target_digest(data):
    fields=('frame','center_xyz_mm','closing_axis_yaw_deg','yaw_by_side',
            'selected_yaw_camera','capture_time_unix','observation_above_mm','approach_mode',
            'multi_object')
    payload={key:data.get(key) for key in fields}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def lock_plan_folder(locks, folder, *, execute):
    def acquire(path, error):
        handle = locks.enter_context(path.open('a'))
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(error) from exc

    if execute:
        domain = os.environ.get('ROS_DOMAIN_ID', '0')
        acquire(Path('/tmp') / f'jetcobot_dual_auto_{domain}.lock',
                'Another dual auto execution worker is active')
    acquire(folder / '.approach_plan.lock', 'Another worker is using this saved plan')


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
    ap.add_argument('--joint-goal', action='store_true')
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
    with ExitStack() as locks:
        # Separate output folders can be planned in parallel. Protect each
        # saved plan from a simultaneous read or write in the same folder.
        lock_plan_folder(locks, folder, execute=args.execute_saved_plan)
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
                        bool(summary.get('joint_goal', False)) != args.joint_goal or
                        abs(summary.get('above_mm',-1)-args.above_mm)>1e-6 or
                        bool(summary.get('allow_gripper_tilt',False)) != args.allow_gripper_tilt):
                    raise RuntimeError('Saved dual-arm plan does not match this capture; plan again')
                phases=[]
                for row in summary['phases']:
                    result=deserialize_message((folder/row['file']).read_bytes(),MotionPlanResponse)
                    phases.append({'side':row['side'],'result':result,'start':row['start'],
                                   'route':row['route']})
                chosen={'phases':phases,'selected_arm':summary['selected_arm'],
                        'yield_strategy':summary['yield_strategy'],'cost_deg':summary['path_cost_deg'],
                        'joint_goal':args.joint_goal}
                current=coordinator.ready()
                # Selected arm only: the other arm may be placing meanwhile;
                # the gate checks the pair (both arms' paths) before motion.
                own=summary['selected_arm']+'_'
                drift=max(abs(math.degrees(current[n]-phases[0]['start'][n]))
                          for n in phases[0]['start'] if n.startswith(own))
                if drift>args.max_state_change_deg:
                    raise RuntimeError(f'{own[:-1]} arm changed {drift:.1f} deg since preview; plan again')
                if (folder/'approach_complete.json').exists():
                    raise RuntimeError('This capture already completed approach; recapture')
                print(f'EXECUTE SAVED: {chosen["selected_arm"].upper()} arm; '
                      f'peer={chosen["yield_strategy"]}; routes={[p["route"] for p in phases]}',flush=True)
                # A reset can discard this queue only before execution is attempted.
                (folder/'approach_execution_started').touch()
                try:
                    reached=coordinator.execute(chosen)
                except Exception as exc:
                    if type(exc).__name__=='GoalRejectedNoMotion':
                        # Nothing moved: let U re-plan this object (2026-11-24).
                        (folder/'approach_execution_started').unlink(missing_ok=True)
                        print('NO MOTION SENT: approach rejected before forwarding; U retries this object.',
                              flush=True)
                    raise
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
                    own=data.get('multi_object', {}).get('assigned_arm')
                    drift=max((abs(math.degrees(current[n]-float(captured[n])))
                               for n in current if n not in assumed
                               and (own is None or n.startswith(own+'_'))),default=0.)
                    if drift>args.max_state_change_deg:
                        raise RuntimeError(f'Arms changed {drift:.1f} deg since camera capture; recapture')
                if assumed:
                    print(f'PLAN FROM ASSUMED START: {len(assumed)} projected return joints', flush=True)
                required_arm=data.get('multi_object', {}).get('assigned_arm')
                peer_mode = ('hold' if data.get('multi_object', {}).get('peer_holding')
                             else args.peer_mode)
                chosen=coordinator.choose(target,args.tcp_closing_axis,args.yaw_offset_deg,
                                          peer_mode,args.allow_gripper_tilt,
                                          required_arm=required_arm,start_override=assumed,
                                          joint_goal=args.joint_goal)
                rows=[]
                for index,phase in enumerate(chosen['phases']):
                    name=f'dual_phase_{index+1}.cdr'
                    (folder/name).write_bytes(serialize_message(phase['result']))
                    rows.append({'side':phase['side'],'route':phase['route'],
                                 'start':phase['start'],'file':name,
                                 'points':len(phase['result'].trajectory.joint_trajectory.points)})
                summary={'dual_auto_real':True,'phase':'approach','selected_arm':chosen['selected_arm'],
                         'yield_strategy':chosen['yield_strategy'],'path_cost_deg':chosen['cost_deg'],
                         'joint_goal':args.joint_goal,
                         'distance_m':chosen['distance_m'],'yaw_deg':chosen['yaw_deg'],
                         'approach_offset_xy_mm':chosen['approach_offset_xy_mm'],
                         'j6_travel_deg':chosen['j6_travel_deg'],
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
