"""Build direct joint goals from named poses or a single IK solution."""
import math


JOINT_GOAL_ACTION = '/jetcobot_dual/guarded_joint_goal/follow_joint_trajectory'


def pose_joint_goal(node, wait, state, arm, position, quaternion):
    from builtin_interfaces.msg import Duration
    from moveit_msgs.srv import GetPositionIK

    client = node.create_client(GetPositionIK, '/jetcobot_dual/compute_ik')
    try:
        if not client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('MoveIt IK service unavailable')
        request = GetPositionIK.Request()
        ik = request.ik_request
        ik.group_name = f'arm_{arm}'
        ik.ik_link_name = f'{arm}_TCP'
        ik.avoid_collisions = True
        ik.timeout = Duration(sec=1)
        ik.robot_state.is_diff = True
        ik.robot_state.joint_state.name = list(state)
        ik.robot_state.joint_state.position = [float(q) for q in state.values()]
        ik.pose_stamped.header.frame_id = 'right_base_link'
        pose = ik.pose_stamped.pose
        pose.position.x, pose.position.y, pose.position.z = map(float, position)
        (pose.orientation.x, pose.orientation.y, pose.orientation.z,
         pose.orientation.w) = map(float, quaternion)
        reply = wait(client.call_async(request), 5.0)
    finally:
        node.destroy_client(client)
    if reply.error_code.val != 1:
        print(f'{arm.upper()} GOAL IK: code={reply.error_code.val}', flush=True)
        return None
    solution = dict(zip(reply.solution.joint_state.name, reply.solution.joint_state.position))
    names = [f'{arm}_{i}_Joint' for i in range(1, 7)]
    if any(n not in solution or not math.isfinite(solution[n]) for n in names):
        raise RuntimeError(f'{arm} IK returned incomplete joint positions')
    return {n: solution[n] for n in names}


def joint_goal_result(state, goal):
    """Untimed start and target for preview, costs and geometric checks."""
    from moveit_msgs.msg import MotionPlanResponse
    from trajectory_msgs.msg import JointTrajectoryPoint

    result = MotionPlanResponse()
    result.trajectory_start.is_diff = True
    result.trajectory_start.joint_state.name = list(state)
    result.trajectory_start.joint_state.position = [float(q) for q in state.values()]
    trajectory = result.trajectory.joint_trajectory
    trajectory.joint_names = list(goal)
    trajectory.points = [
        JointTrajectoryPoint(positions=[float(state[n]) for n in goal]),
        JointTrajectoryPoint(positions=[float(q) for q in goal.values()]),
    ]
    return result


def endpoint_request(goal):
    from control_msgs.action import FollowJointTrajectory
    from trajectory_msgs.msg import JointTrajectoryPoint

    request = FollowJointTrajectory.Goal()
    request.trajectory.joint_names = list(goal)
    request.trajectory.points = [JointTrajectoryPoint(positions=[float(q) for q in goal.values()])]
    return request
