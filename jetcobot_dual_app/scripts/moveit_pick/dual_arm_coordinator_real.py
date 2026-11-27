"""Centralized 12-DOF coordination for the integrated JetCobot Gazebo model.

Single target: nearest feasible arm plus minimum-cost peer yielding.
Two targets: evaluate both task assignments and plan both arms together.
Objects are not added to the collision scene; robot self/inter-arm checks stay on.
"""
import copy,itertools,json,math,os,signal,time
from pathlib import Path
import numpy as np
from motion_math import ALL_JOINTS,JOINTS,SIDES,sampled_positions
from move_right_sam_yaw import closing_axis_to_tcp_orientation, mat_to_quat, r_z


# 2026-11-25: NOT USED for the approach any more (user decision). Both arms now
# aim the gripper straight down (closing_axis_to_tcp_orientation); auto fine
# levelling (FINE LEVEL) squares it afterwards. Kept for possible later use:
# right_manual_tcp_orientation() below = manual pose direction (3.2 deg tilt)
# x RIGHT_ARRIVAL_COMPENSATION (about 5.5 deg in total), see
# docs/worklog/오른팔_이슈_공유정리_2026-11-17.md.
#
# Right gripper direction from the user's manually squared pose (2026-11-17).
# SDK joints [49.57, -37.44, -69.69, 16.78, -3.25, -35.24] degrees were
# converted to right_TCP rotation with the captured JetCobot dual URDF.
RIGHT_MANUAL_TCP_ROTATION = np.array([
    [-0.9951138342968744, 0.09056665353903699, 0.03932096206494967],
    [0.08896515091844104, 0.9952015411661743, -0.040731982305443185],
    [-0.04282124137658978, -0.03703476376611552, -0.9983960975283086],
])

# Base-frame orientation precompensation from right-arm arrivals in captures
# 20261117_151830_925861 and 20261117_154125_422792. Their measured TCP
# directions needed approximately (+2.46, -2.91) and (+3.22, -3.46) degrees
# about right_base_link X/Y to match the manual gripper direction. Do not
# compensate Z: the object's closing yaw is intentional and varied between runs.
RIGHT_ARRIVAL_COMPENSATION = np.array([
    [0.9984508089075662, -0.0013792171481228238, -0.05562445462101145],
    [-0.0013792171481228238, 0.9987721076173453, -0.049521458032499216],
    [0.05562445462101145, 0.049521458032499216, 0.9972229165249115],
])


def right_manual_tcp_orientation(closing_yaw_deg, closing_axis, yaw_offset_deg):
    """Keep the manual TCP tilt while turning its jaws to the object yaw."""
    axis = 0 if closing_axis == 'x' else 1
    reference_axis_yaw = math.atan2(RIGHT_MANUAL_TCP_ROTATION[1, axis],
                                    RIGHT_MANUAL_TCP_ROTATION[0, axis])
    desired_axis_yaw = math.radians(closing_yaw_deg + yaw_offset_deg +
                                    (180. if closing_axis == 'x' else 0.))
    rotation = (RIGHT_ARRIVAL_COMPENSATION @
                r_z(desired_axis_yaw - reference_axis_yaw) @ RIGHT_MANUAL_TCP_ROTATION)
    return mat_to_quat(rotation)


class StopRequested(Exception):pass


class GoalRejectedNoMotion(RuntimeError):
    """The gate refused the first phase; no arm command was forwarded."""


# Same value as the Pi bridge (hardware_bridge.py max_move_deg default, trajectory_guard.py):
# the Pi rejects a goal whose point moves a joint more than this from the current pose.
PI_MAX_MOVE_DEG = 180.0


def pi_move_limit_error(trajectory, start):
    """Name of the first joint the Pi would reject (> PI_MAX_MOVE_DEG from start), else None."""
    names = list(trajectory.joint_names)
    for point in trajectory.points:
        for name, value in zip(names, point.positions):
            if abs(float(value) - start[name]) > math.radians(PI_MAX_MOVE_DEG):
                return name
    return None


def load_target(path,above_mm=40.,max_age=180.,yaw_camera='auto'):
    data=json.loads(Path(path).read_text())
    if data.get('frame')!='right_base_link':raise ValueError('Target must use right_base_link')
    xyz=np.asarray(data['center_xyz_mm'],dtype=float)/1000.
    if xyz.shape!=(3,) or not np.isfinite(xyz).all():raise ValueError('Invalid target XYZ')
    if data.get('capture_time_unix') is not None and time.time()-data['capture_time_unix']>max_age:
        raise ValueError('Target capture expired; recapture or supply a current known-3D target')
    side=data.get('selected_yaw_camera','right') if yaw_camera=='auto' else yaw_camera
    yaw=data.get('yaw_by_side',{}).get(side,{}).get('closing_axis_yaw_deg',data.get('closing_axis_yaw_deg'))
    if yaw is None or not math.isfinite(float(yaw)):raise ValueError('Missing or invalid closing yaw')
    if data.get('approach_mode') != 'tcp_above_target':
        raise ValueError('Dual auto approach requires TCP-above-target capture')
    if abs(float(data.get('observation_above_mm',-1))-above_mm)>1e-6:
        raise ValueError('Approach height differs from captured target')
    goal=xyz+np.array([0.,0.,above_mm/1000.])
    if not .02<=goal[2]<=.50 or np.linalg.norm(goal)>.55:raise ValueError('Target outside configured workspace')
    return {'data':data,'position':goal,'yaw':float(yaw),'file':str(path),'yaw_camera':side}


class DualArmCoordinator:
    def __init__(self,urdf_path,velocity=.10,acceleration=.10,plan_time=3.,max_plans=2,planning_budget=25.):
        import rclpy
        from rclpy.parameter import Parameter
        from rclpy.signals import SignalHandlerOptions
        from rclpy.action import ActionClient
        from sensor_msgs.msg import JointState
        from control_msgs.action import FollowJointTrajectory
        from moveit_msgs.srv import GetMotionPlan, GetPositionFK, GetStateValidity
        self.ros=rclpy;self.stop=False
        signal.signal(signal.SIGINT,lambda *_:setattr(self,'stop',True))
        signal.signal(signal.SIGTERM,lambda *_:setattr(self,'stop',True))
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.node=rclpy.create_node(os.environ.get('JETCOBOT_COORD_NODE','dual_arm_coordinator'),parameter_overrides=[Parameter('use_sim_time',value=False)])
        self.measured=None;self.received=0.;self.velocity=velocity;self.acceleration=acceleration
        self.plan_time=plan_time;self.max_plans=max_plans;self.planning_budget=planning_budget
        self.sub=self.node.create_subscription(JointState,'/jetcobot_dual/joint_states',self.receive,10)
        self.fk = self.node.create_client(GetPositionFK, '/jetcobot_dual/compute_fk')
        self.planner=self.node.create_client(GetMotionPlan,'/jetcobot_dual/plan_kinematic_path')
        self.validity=self.node.create_client(GetStateValidity,'/jetcobot_dual/check_state_validity')
        self.clients={s:ActionClient(self.node,FollowJointTrajectory,f'/jetcobot_dual/guarded_{s}_controller/follow_joint_trajectory') for s in SIDES}
        self.handles={};self.diagnostics=[]
        import xml.etree.ElementTree as ET
        root=ET.parse(urdf_path).getroot()
        self.limits={n:(float(root.find(f"joint[@name='{n}']/limit").get('lower'))+.01,
                        float(root.find(f"joint[@name='{n}']/limit").get('upper'))-.01) for n in ALL_JOINTS}

    def receive(self,msg):
        if len(msg.name)!=len(msg.position):return
        values=dict(zip(msg.name,msg.position))
        if not all(n in values and math.isfinite(values[n]) for n in ALL_JOINTS):return
        self.measured=values;self.received=time.monotonic()

    def spin(self):self.ros.spin_once(self.node,timeout_sec=.02)

    def wait(self,future,timeout=15,stoppable=True):
        end=time.monotonic()+timeout
        while not future.done() and time.monotonic()<end:
            self.spin()
            if stoppable and self.stop:raise StopRequested()
        if not future.done():raise RuntimeError('ROS service/action timed out')
        result=future.result()
        if result is None:raise RuntimeError('ROS request returned no result')
        return result

    def state(self,timeout=5):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.spin()
            if self.stop:raise StopRequested()
            if self.measured is not None and time.monotonic()-self.received<1.:return self.measured.copy()
        raise RuntimeError('Missing fresh /jetcobot_dual/joint_states')

    def robot_state(self,values):
        from moveit_msgs.msg import RobotState
        result=RobotState();result.is_diff=True
        result.joint_state.name=list(values);result.joint_state.position=list(values.values())
        return result

    def ready(self):
        for service in (self.planner,self.validity):
            if not service.wait_for_service(timeout_sec=20):raise RuntimeError(f'Missing service {service.srv_name}')
        return self.state()

    def valid(self,values):
        from moveit_msgs.srv import GetStateValidity
        req=GetStateValidity.Request();req.robot_state=self.robot_state(values)
        return self.wait(self.validity.call_async(req),timeout=5).valid

    def tcp_distances(self, current, position):
        from moveit_msgs.msg import MoveItErrorCodes
        from moveit_msgs.srv import GetPositionFK

        if not self.fk.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('Missing service /jetcobot_dual/compute_fk')
        request = GetPositionFK.Request()
        request.header.frame_id = 'right_base_link'
        request.fk_link_names = [f'{side}_TCP' for side in SIDES]
        request.robot_state = self.robot_state(current)
        result = self.wait(self.fk.call_async(request), timeout=5)
        poses = dict(zip(result.fk_link_names, result.pose_stamped))
        if (result.error_code.val != MoveItErrorCodes.SUCCESS
                or any(link not in poses for link in request.fk_link_names)):
            raise RuntimeError(f'MoveIt could not compute both TCP poses (code={result.error_code.val})')
        distances = {}
        for side in SIDES:
            p = poses[f'{side}_TCP'].pose.position
            distances[side] = float(np.linalg.norm(position - np.array([p.x, p.y, p.z])))
        return distances

    def yield_candidates(self,side,current):
        result=[('hold',{n:current[n] for n in JOINTS[side]})]
        direction=-1 if side=='right' else 1
        for name,size in [('small_yield',.25),('wide_yield',.5)]:
            goal={n:current[n] for n in JOINTS[side]}
            for index,delta in ((1,direction*size),(2,-size*.6),(3,size*.7)):
                joint=f'{side}_{index}_Joint';lo,hi=self.limits[joint]
                goal[joint]=min(hi,max(lo,goal[joint]+delta))
            result.append((name,goal))
        # Last-resort candidate; its actual travel cost competes with smaller yields.
        result.append(('home_fallback',{n:0. for n in JOINTS[side]}))
        return result

    def plan_joint_goal(self,side,current,goal):
        from moveit_msgs.srv import GetMotionPlan
        from moveit_msgs.msg import Constraints,JointConstraint
        req=GetMotionPlan.Request();r=req.motion_plan_request
        r.group_name=f'arm_{side}';r.pipeline_id='ompl';r.planner_id='RRTConnectkConfigDefault'
        r.start_state=self.robot_state(current);r.num_planning_attempts=2;r.allowed_planning_time=self.plan_time
        r.max_velocity_scaling_factor=self.velocity;r.max_acceleration_scaling_factor=self.acceleration
        r.goal_constraints=[Constraints(joint_constraints=[JointConstraint(joint_name=n,position=float(goal[n]),
            tolerance_above=.004,tolerance_below=.004,weight=1.) for n in JOINTS[side]])]
        result=self.wait(self.planner.call_async(req),timeout=self.plan_time*2+10).motion_plan_response
        if result.error_code.val!=1:return None
        if set(result.trajectory.joint_trajectory.joint_names)!=set(JOINTS[side]):
            raise RuntimeError('Planner returned wrong arm joints')
        return result

    def plan_pose_goal(self,side,current,position,quaternion,
                       position_tolerance_m=.005,orientation_tolerance_deg=8., *, plan_time=None):
        from moveit_msgs.srv import GetMotionPlan
        from moveit_msgs.msg import Constraints,PositionConstraint,OrientationConstraint
        from geometry_msgs.msg import Pose,Quaternion
        from shape_msgs.msg import SolidPrimitive
        req=GetMotionPlan.Request();r=req.motion_plan_request
        r.group_name=f'arm_{side}';r.pipeline_id='ompl';r.planner_id='RRTConnectkConfigDefault'
        r.start_state=self.robot_state(current);r.num_planning_attempts=2
        allowed_time = self.plan_time if plan_time is None else plan_time
        r.allowed_planning_time = allowed_time
        r.max_velocity_scaling_factor=self.velocity;r.max_acceleration_scaling_factor=self.acceleration
        pc=PositionConstraint();pc.header.frame_id='right_base_link'
        pc.link_name=f'{side}_TCP';pc.weight=1.0
        sphere=SolidPrimitive();sphere.type=SolidPrimitive.SPHERE;sphere.dimensions=[position_tolerance_m]
        pose=Pose();pose.position.x,pose.position.y,pose.position.z=map(float,position)
        pose.orientation.w=1.0
        pc.constraint_region.primitives=[sphere]
        pc.constraint_region.primitive_poses=[pose]
        oc=OrientationConstraint();oc.header.frame_id='right_base_link'
        oc.link_name=f'{side}_TCP'
        oc.orientation=Quaternion(x=float(quaternion[0]),y=float(quaternion[1]),
                                  z=float(quaternion[2]),w=float(quaternion[3]))
        tol=math.radians(orientation_tolerance_deg)
        oc.absolute_x_axis_tolerance=tol;oc.absolute_y_axis_tolerance=tol
        oc.absolute_z_axis_tolerance=tol
        oc.parameterization=OrientationConstraint.ROTATION_VECTOR
        oc.weight=1.0
        r.goal_constraints=[Constraints(position_constraints=[pc],orientation_constraints=[oc])]
        started=time.monotonic()
        result=self.wait(self.planner.call_async(req),timeout=allowed_time*2+10).motion_plan_response
        print(f'POSE PLAN {side}: code={result.error_code.val}, time={time.monotonic()-started:.1f}s',flush=True)
        if result.error_code.val!=1:return None
        if set(result.trajectory.joint_trajectory.joint_names)!=set(JOINTS[side]):
            raise RuntimeError('Pose planner returned wrong arm joints')
        return result

    def check_trajectory(self,trajectory,start):
        count=0
        for values in sampled_positions(trajectory):
            state=start.copy();state.update(zip(trajectory.joint_names,map(float,values)))
            if not self.valid(state):raise RuntimeError('Planned trajectory collides after retiming')
            count+=1
        return count

    def direct_route(self,side,start,trajectory,step_deg=1.):
        names=list(trajectory.joint_names)
        first=np.array([start[n] for n in names])
        last=np.array(trajectory.points[-1].positions)
        steps=max(1,math.ceil(float(np.max(np.abs(last-first)))/math.radians(step_deg)))
        for index in range(steps+1):
            state=start.copy();state.update(zip(names,first+(last-first)*(index/steps)))
            if not self.valid(state):return 'drag'
        return 'endpoint'

    @staticmethod
    def wrist_travel_deg(side, state, planned):
        jt = planned.trajectory.joint_trajectory
        wrist = f'{side}_6_Joint'
        index = list(jt.joint_names).index(wrist)
        positions = [state[wrist], *(point.positions[index] for point in jt.points)]
        return math.degrees(sum(abs(q - p) for p, q in zip(positions, positions[1:])))

    def approach_goals(self, side, state, target, closing_axis, yaw_offset,
                       allow_gripper_tilt, deadline, *, joint_goal=False):
        """Keep the object target fixed. Auto fine may start from a nearby TCP pose."""
        position = target['position']
        yaw = target['yaw']
        tolerance = 8.0 if allow_gripper_tilt else 2.0
        angles = (0.0, 180.0)[:self.max_plans]
        def solve(point, quaternion, *, plan_time=None):
            if not joint_goal:
                return self.plan_pose_goal(side, state, point, quaternion,
                                           orientation_tolerance_deg=tolerance, plan_time=plan_time)
            from joint_goal import joint_goal_result, pose_joint_goal
            goal = pose_joint_goal(self.node, self.wait, state, side, point, quaternion)
            return joint_goal_result(state, goal) if goal is not None else None

        found_nominal = False
        for flip in angles:
            if time.monotonic() >= deadline:
                return
            # 2026-11-25: right arm also straight down. Previous right-arm target:
            #   quaternion = right_manual_tcp_orientation(yaw + flip, closing_axis, yaw_offset)
            quaternion, _ = closing_axis_to_tcp_orientation(yaw + flip, closing_axis, yaw_offset)
            result = solve(position, quaternion)
            if result is not None:
                found_nominal = True
                yield result, yaw + flip, [0.0, 0.0]

        # TEMP (2026-11-25, B6): right arm side-approach fallback. 14:27 right target
        # [-159.6, 80.8, 63.0] mm had no IK at the requested gripper yaw (J1 at +168 deg).
        # Tries TCP 15/20 mm along the tangent; live fine alignment uses the real object.
        # Enabled by multi_object.allow_right_approach_fallback (sam_multi_real, auto fine).
        # Revisit / remove later.
        job = target.get('data', {}).get('multi_object', {})
        azimuth = math.degrees(math.atan2(position[1], position[0]))
        if (found_nominal or side != 'right' or not job.get('allow_right_approach_fallback')
                or not 145 <= azimuth <= 180):
            return
        tangent = np.array([position[1], -position[0], 0.0]) / np.linalg.norm(position[:2])
        for inset_mm in (15.0, 20.0):
            offset = tangent * inset_mm / 1000
            for flip in reversed(angles):
                if time.monotonic() >= deadline:
                    return
                # 2026-11-25: straight down (was right_manual_tcp_orientation).
                orientation, _ = closing_axis_to_tcp_orientation(yaw + flip, closing_axis, yaw_offset)
                result = solve(position + offset, orientation, plan_time=1.5)
                if result is None:
                    continue
                travel = self.wrist_travel_deg(side, state, result)
                offset_mm = (offset[:2] * 1000).tolist()
                print(f'RIGHT APPROACH CANDIDATE: calibrated TCP, '
                      f'XY offset {np.round(offset_mm, 1).tolist()} mm, J6 travel {travel:.1f} deg. '
                      'Live fine alignment uses the original object.', flush=True)
                yield result, yaw + flip, offset_mm
                if travel <= 90:
                    return

    def choose(self,target,closing_axis='x',yaw_offset=0.,peer_mode='auto',allow_gripper_tilt=False,
               required_arm=None,start_override=None, *, joint_goal=False):
        if required_arm not in (None, 'left', 'right'):
            raise ValueError(f'Invalid required arm: {required_arm}')
        current=self.ready()
        if start_override:
            # Plan from a future state (e.g. the placing arm back at see_center).
            current.update({n:float(v) for n,v in start_override.items()})
        if not self.valid(current):raise RuntimeError('Current dual-arm state is in collision')
        distances = self.tcp_distances(current, target['position'])
        deadline=time.monotonic()+self.planning_budget
        strategies=(['hold'] if peer_mode=='hold' else
                    ['small_yield','wide_yield','home_fallback'] if peer_mode=='yield' else
                    ['hold','small_yield','wide_yield','home_fallback'])
        if joint_goal:
            strategies = ['hold']
        for strategy in strategies:
            if time.monotonic()>=deadline:break
            candidates=[]
            for side in sorted(SIDES,key=distances.get):
                if required_arm is not None and side != required_arm:
                    continue
                if time.monotonic()>=deadline:break
                peer='right' if side=='left' else 'left'
                projected=current.copy();phases=[]
                if strategy!='hold':
                    yield_goal=dict(self.yield_candidates(peer,current))[strategy]
                    if not self.valid(dict(projected,**yield_goal)):
                        print(f'{side} {strategy}: peer endpoint collides',flush=True)
                        continue
                    peer_result=self.plan_joint_goal(peer,projected,yield_goal)
                    if peer_result is None:
                        print(f'{side} {strategy}: peer route unavailable',flush=True)
                        continue
                    traj=peer_result.trajectory.joint_trajectory
                    phases.append({'side':peer,'result':peer_result,'start':projected.copy(),'route':'auto'})
                    projected.update(zip(traj.joint_names,traj.points[-1].positions))
                for planned, yaw_candidate, inset in self.approach_goals(
                        side, projected, target, closing_axis, yaw_offset,
                        allow_gripper_tilt, deadline, joint_goal=joint_goal):
                    sequence=phases+[{'side':side,'result':planned,'start':projected.copy(),'route':'auto'}]
                    cost=0.
                    for phase in sequence:
                        names=phase['result'].trajectory.joint_trajectory.joint_names
                        previous=np.array([phase['start'][n] for n in names])
                        for point in phase['result'].trajectory.joint_trajectory.points:
                            values=np.array(point.positions)
                            cost+=float(np.sum(np.abs(values-previous)))*(3 if phase['side']==peer else 1)
                            previous=values
                    wrist_travel = self.wrist_travel_deg(side, projected, planned)
                    candidates.append({'selected_arm':side,'yield_strategy':strategy,'phases':sequence,
                                       'cost_deg':math.degrees(cost),'distance_m':distances[side],
                                       'yaw_deg':yaw_candidate,'approach_offset_xy_mm':inset,
                                       'j6_travel_deg':wrist_travel,'start':current.copy()})
                    print(f'CANDIDATE {side} peer={strategy} cost={math.degrees(cost):.1f} deg, '
                          f'J6 travel={wrist_travel:.1f} deg', flush=True)
            # Prefer the arm nearer to the object when its plan costs at most
            # 20% more than the cheapest. A slightly cheaper far arm reached
            # across the table, tilted 5.7 deg in fine alignment and could not
            # lift (2026-11-19 multi run).
            min_cost=min((item['cost_deg'] for item in candidates),default=0.)
            def preference(item):
                near_band=item['cost_deg']<=1.2*min_cost
                return (0,item['distance_m'],item['cost_deg']) if near_band else (1,0.,item['cost_deg'])
            arm_scores = {}
            for item in candidates:
                side = item['selected_arm']
                score = preference(item)
                arm_scores[side] = min(arm_scores.get(side, score), score)
            # TEMP (2026-11-25, B7): within the preferred arm, fewer J6 degrees first,
            # then smaller side offset (B6), then cost. Before: nearer arm + cost only.
            # Revisit later.
            def path_preference(item):
                return (arm_scores[item['selected_arm']], item['j6_travel_deg'],
                        math.hypot(*item['approach_offset_xy_mm']), item['cost_deg'])
            for chosen in sorted(candidates, key=path_preference):
                # 11-27: skip a candidate the Pi would reject (left_3_Joint > 180 deg in one goal
                # after TEMP B7 preferred less J6 travel over cost). Same limit as the Pi.
                over = next(filter(None, (pi_move_limit_error(
                    phase['result'].trajectory.joint_trajectory, phase['start'])
                    for phase in chosen['phases'])), None)
                if over is not None:
                    print(f'REJECT {chosen["selected_arm"]} {strategy}: {over} would move more than '
                          f'{PI_MAX_MOVE_DEG:g} deg in one goal (Pi limit)', flush=True)
                    continue
                try:
                    for phase in chosen['phases']:
                        self.check_trajectory(phase['result'].trajectory.joint_trajectory,phase['start'])
                except RuntimeError as exc:
                    print(f'REJECT {chosen["selected_arm"]} {strategy}: {exc}',flush=True)
                    continue
                print(f'ARM CHOICE: {chosen["selected_arm"]} (distance {chosen["distance_m"]*1000:.0f} mm, '
                      f'cost {chosen["cost_deg"]:.1f} deg; J6 travel {chosen["j6_travel_deg"]:.1f} deg)', flush=True)
                return chosen
        if joint_goal:
            raise RuntimeError(f'No collision-checked direct IK goal at TCP {target["position"] * 1000} mm')
        raise RuntimeError(f'No MoveIt goal for either arm at TCP {np.round(target["position"]*1000,1).tolist()} mm; '
                           f'checked until {self.planning_budget:g}s budget. See POSE PLAN codes and MoveIt log.')

    def display(self,chosen):
        from moveit_msgs.msg import DisplayTrajectory
        from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
        qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
        pub=self.node.create_publisher(DisplayTrajectory,'/jetcobot_dual/display_planned_path',qos)
        for phase in chosen['phases']:
            msg=DisplayTrajectory();msg.model_id='jetcobot_dual'
            msg.trajectory_start=phase['result'].trajectory_start
            msg.trajectory=[phase['result'].trajectory]
            pub.publish(msg)
            until=time.monotonic()+.2
            while time.monotonic()<until:self.spin()

    def execute(self,chosen,state_tolerance_deg=5.):
        from control_msgs.action import FollowJointTrajectory
        from action_msgs.msg import GoalStatus
        from rcl_interfaces.srv import GetParameters
        config=self.node.create_client(GetParameters,'/jetcobot_dual/selected_arm_execution_gate/get_parameters')
        if not config.wait_for_service(timeout_sec=5):raise RuntimeError('Execution gate unavailable')
        response=self.wait(config.call_async(GetParameters.Request(names=['trajectory_backend','execution_arm','coordinated_execution'])))
        if (response.values[0].string_value!='sdk_drag_hybrid' or
                response.values[1].string_value!='both'):
            raise RuntimeError('Execution requires execution_arm:=both trajectory_backend:=sdk_drag_hybrid')
        # coordinated_execution:=true rejects guarded_left/right goals; send the
        # same single-arm trajectory to the combined controller (the gate holds
        # the other arm at its measured pose). 2026-11-19.
        coordinated=len(response.values)>2 and response.values[2].bool_value
        from rclpy.action import ActionClient
        joint_goal = chosen.get('joint_goal', False)
        if joint_goal:
            if not coordinated:
                raise RuntimeError('Direct joint goals require the coordinated execution gate')
            from joint_goal import JOINT_GOAL_ACTION, endpoint_request
            self.clients['joint_goal'] = ActionClient(self.node, FollowJointTrajectory,
                                                      JOINT_GOAL_ACTION)
        elif coordinated and 'both' not in self.clients:
            self.clients['both']=ActionClient(self.node,FollowJointTrajectory,
                '/jetcobot_dual/guarded_both_controller/follow_joint_trajectory')
        for index,phase in enumerate(chosen['phases'],1):
            side=phase['side'];trajectory=phase['result'].trajectory.joint_trajectory
            current=self.state();names=JOINTS[side]
            # Joint goals: own arm only, and the gate checks this arm's path
            # against the other arm's whole remaining path (it may be moving).
            checked=JOINTS[side] if joint_goal else ALL_JOINTS
            drift=max(abs(math.degrees(current[n]-phase['start'][n])) for n in checked)
            if drift>state_tolerance_deg:raise RuntimeError(f'Start state changed {drift:.1f} deg; replan')
            if not joint_goal:
                self.check_trajectory(trajectory,current)
            if joint_goal:
                goal = dict(zip(trajectory.joint_names, trajectory.points[-1].positions))
                request = endpoint_request(goal)
                actual_route = 'direct joint goal'
                client = self.clients['joint_goal']
            else:
                request = FollowJointTrajectory.Goal(trajectory=trajectory)
                actual_route = 'endpoint or drag (sdk_drag_hybrid selects from live collision scene)'
                client = self.clients['both' if coordinated else side]
            if not client.wait_for_server(timeout_sec=8):raise RuntimeError(f'{side} guarded controller unavailable')
            print(f'EXECUTE {index}/{len(chosen["phases"])} {side} {actual_route}',flush=True)
            # 2026-11-24: the gate rejects (without forwarding anything) when a
            # measured arm is >1.5 s old; the left feedback has ~1.5 s gaps at 2 Hz.
            # A rejection moved nothing, so wait for fresh joints and resend.
            for attempt in range(1,6):
                if self.stop:raise StopRequested()
                try:
                    handle=self.wait(client.send_goal_async(request),
                                     12,stoppable=False)
                except Exception:
                    print(f'{side}: goal acceptance unknown; check robot and gate.',flush=True)
                    raise
                if handle.accepted:break
                print(f'{side} goal rejected by the gate (attempt {attempt}/5, nothing sent); '
                      'waiting for fresh feedback and resending.',flush=True)
                if attempt==5:
                    error=GoalRejectedNoMotion if index==1 else RuntimeError
                    raise error(f'{side} goal rejected 5 times; nothing was sent. Check the gate '
                                'log (fault latch / stale feedback)')
                time.sleep(1.0)
                current=self.state()
                drift=max(abs(math.degrees(current[n]-phase['start'][n])) for n in checked)
                if drift>state_tolerance_deg:raise RuntimeError(f'Start state changed {drift:.1f} deg; replan')
                if not joint_goal:
                    self.check_trajectory(trajectory,current)
            self.handles[side]=handle
            result=handle.get_result_async()
            try:
                if self.stop:raise StopRequested()
                reply=self.wait(result,180)
            except BaseException:
                try:
                    if not result.done():
                        self.wait(handle.cancel_goal_async(),4,stoppable=False)
                    terminal=self.wait(result,5,stoppable=False)
                    print(f'{side}: controller finished with status {terminal.status}.',flush=True)
                except Exception as exc:
                    print(f'{side}: cancellation unconfirmed; check robot and gate ({exc}).',
                          flush=True)
                raise
            finally:self.handles.pop(side,None)
            if reply.status!=GoalStatus.STATUS_SUCCEEDED or reply.result.error_code!=0:
                # 11-27: the gate reports a Pi rejection as '... Pi rejected the joint goal; no
                # motion sent'. Nothing moved, so on the first phase U may re-plan (as above).
                error=(GoalRejectedNoMotion if index==1 and
                       'no motion sent' in str(reply.result.error_string) else RuntimeError)
                raise error(f'{side} failed: status={reply.status}, error={reply.result.error_code}, '
                            f'detail={reply.result.error_string}')
            expected=dict(zip(trajectory.joint_names,trajectory.points[-1].positions))
            reached=self.state()
            error=max(abs(math.degrees(reached[n]-expected[n])) for n in expected)
            if side == 'right':
                print(f'RIGHT measured endpoint joint difference: {error:.2f} deg '
                      '(visual inspection requested; no automatic settling move).',flush=True)
            else:
                deadline=time.monotonic()+5.0
                while error>5.0 and time.monotonic()<deadline:
                    reached=self.state()
                    error=max(abs(math.degrees(reached[n]-expected[n])) for n in expected)
                if error>5.0:
                    raise RuntimeError(f'{side} finished but measured endpoint differs by {error:.1f} deg')
            print(f'DONE {index}/{len(chosen["phases"])} {side}',flush=True)
        return self.state()

    def close(self):
        self.node.destroy_node()
        if self.ros.ok():self.ros.shutdown()
