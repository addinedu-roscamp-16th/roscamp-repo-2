"""Joint trajectory sampling and motion cost utilities for both arms."""
import copy
import math
import numpy as np

SIDES=('left','right')
JOINTS={side:[f'{side}_{i}_Joint' for i in range(1,7)] for side in SIDES}
ALL_JOINTS=JOINTS['left']+JOINTS['right']


def trajectory_cost(trajectory,current,active_arms):
    names=list(trajectory.joint_names)
    if len(names)!=12 or set(names)!=set(ALL_JOINTS) or not trajectory.points:
        raise ValueError('Expected a nonempty combined 12-joint arm trajectory')
    previous=np.array([current[name] for name in names],dtype=float)
    travel=np.zeros(12)
    for point in trajectory.points:
        values=np.asarray(point.positions,dtype=float)
        if values.shape!=(12,) or not np.isfinite(values).all():raise ValueError('Invalid trajectory values')
        travel+=np.abs(values-previous);previous=values
    degrees={n:float(math.degrees(v)) for n,v in zip(names,travel)}
    totals={s:sum(degrees[n]*w for n,w in zip(JOINTS[s],(1,1,1,2,2,5))) for s in SIDES}
    weighted=sum(cost*(1 if s in active_arms else 3) for s,cost in totals.items())
    return {'weighted_cost_deg':weighted,'weighted_travel_by_arm_deg':totals,'joint_travel_deg':degrees}


def split_trajectory(trajectory):
    """Split after common retiming, preserving identical timestamps and derivatives."""
    if len(trajectory.joint_names)!=12 or set(trajectory.joint_names)!=set(ALL_JOINTS):
        raise ValueError('Combined trajectory must contain both complete arms exactly once')
    out={}
    for side in SIDES:
        indices=[trajectory.joint_names.index(n) for n in JOINTS[side]]
        result=copy.deepcopy(trajectory);result.joint_names=JOINTS[side].copy()
        for point in result.points:
            for field in ('positions','velocities','accelerations','effort'):
                values=list(getattr(point,field))
                if values and len(values)!=12:raise ValueError(f'Invalid {field} size')
                setattr(point,field,[values[i] for i in indices] if values else [])
        out[side]=result
    return out


def interpolate_positions(first,last,fraction,duration):
    """Match the controller's linear/cubic/quintic interpolation semantics."""
    q0=np.asarray(first.positions,dtype=float);q1=np.asarray(last.positions,dtype=float)
    u=float(fraction)
    if duration<=0:return q1.copy()
    if len(first.velocities)==len(q0) and len(last.velocities)==len(q0):
        v0=np.asarray(first.velocities)*duration;v1=np.asarray(last.velocities)*duration
        if len(first.accelerations)==len(q0) and len(last.accelerations)==len(q0):
            a0=np.asarray(first.accelerations)*duration**2
            a1=np.asarray(last.accelerations)*duration**2
            c0=q0;c1=v0;c2=a0/2
            d=q1-c0-c1-c2;v=v1-c1-2*c2;a=a1-2*c2
            c3=10*d-4*v+a/2;c4=-15*d+7*v-a;c5=6*d-3*v+a/2
            return c0+c1*u+c2*u**2+c3*u**3+c4*u**4+c5*u**5
        return (2*u**3-3*u**2+1)*q0+(u**3-2*u**2+u)*v0+(-2*u**3+3*u**2)*q1+(u**3-u**2)*v1
    return q0+(q1-q0)*u


def sampled_positions(trajectory,max_joint_step=.035,max_time_step=.10):
    """Sample the timed controller curve, including every original waypoint."""
    if not trajectory.points:raise ValueError('Empty trajectory')
    def seconds(p):return p.time_from_start.sec+p.time_from_start.nanosec/1e9
    previous=trajectory.points[0]
    yield np.asarray(previous.positions,dtype=float)
    for point in trajectory.points[1:]:
        q0=np.asarray(previous.positions);q1=np.asarray(point.positions)
        dt=seconds(point)-seconds(previous)
        if dt<0:raise ValueError('Nonmonotonic trajectory timestamps')
        count=max(1,math.ceil(float(np.max(np.abs(q1-q0)))/max_joint_step),math.ceil(dt/max_time_step))
        for index in range(1,count+1):yield interpolate_positions(previous,point,index/count,dt)
        previous=point


def trajectory_motion_cost(trajectory, current, arm):
    """Include measured start and every waypoint; do not wrap bounded joints.

    Weights: shoulder/elbow 1, wrist joints 4/5 2, tool roll (6) 5.
    Raw differences preserve long turns and backtracking in the actual path.
    """
    names = list(trajectory.joint_names)
    required = {f'{arm}_{i}_Joint' for i in range(1, 7)}
    if len(names) != 6 or set(names) != required or not trajectory.points:
        raise ValueError('Expected a nonempty selected-arm six-joint trajectory')
    previous = [current[name] for name in names]
    if not all(math.isfinite(q) for q in previous):
        raise ValueError('Nonfinite current joint positions')
    travel = [0.0] * 6
    for point in trajectory.points:
        values = list(point.positions)
        if len(values) != 6 or not all(math.isfinite(q) for q in values):
            raise ValueError('Invalid trajectory joint positions')
        for i, (q, old) in enumerate(zip(values, previous)):
            travel[i] += abs(q - old)
        previous = values
    degrees = {name: math.degrees(delta) for name, delta in zip(names, travel)}
    weights = {f'{arm}_{i}_Joint': weight
               for i, weight in enumerate((1, 1, 1, 2, 2, 5), 1)}
    return {
        'weighted_travel_deg': sum(degrees[n] * weights[n] for n in names),
        'total_travel_deg': sum(degrees.values()),
        'tool_roll_travel_deg': degrees[f'{arm}_6_Joint'],
        'joint_travel_deg': degrees,
    }


def sdk_goal_motion_cost(trajectory, current, arm):
    """Score the final joint target actually sent by the SDK bridge.

    Bounded joints are not angle-wrapped: a large commanded turn remains large.
    The gripper/tool rotation (joint 6) is the primary choice criterion.
    """
    names = list(trajectory.joint_names)
    required = {f'{arm}_{i}_Joint' for i in range(1, 7)}
    if len(names) != 6 or set(names) != required or not trajectory.points:
        raise ValueError('Expected a nonempty selected-arm six-joint trajectory')
    endpoint = list(trajectory.points[-1].positions)
    if len(endpoint) != 6 or not all(math.isfinite(q) for q in endpoint):
        raise ValueError('Invalid SDK goal endpoint')
    deltas = {name: abs(math.degrees(q - current[name]))
              for name, q in zip(names, endpoint)}
    if not all(math.isfinite(v) for v in deltas.values()):
        raise ValueError('Invalid current joint position')
    weights = {f'{arm}_{i}_Joint': weight
               for i, weight in enumerate((1, 1, 1, 2, 2, 5), 1)}
    return {
        'tool_roll_endpoint_deg': deltas[f'{arm}_6_Joint'],
        'weighted_endpoint_deg': sum(deltas[n] * weights[n] for n in names),
        'joint_endpoint_delta_deg': deltas,
    }
