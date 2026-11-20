# Right arm case arrival trial

This separate trial uses the fixed case center from
`/home/ane/pinky_ws/src/jetcobot_dual_real/config/tin_case.yaml`:
X=-230 mm, Y=155 mm in `right_base_link`. It does not change the camera pick
program. The right arm starts at its MoveIt `see_center` state and closes its
gripper as a stand-in for an already held object. The left arm should be at
its own `see_center` state.

The goal is a joint pose estimated offline from the current URDF. Its nominal
TCP is near [-230, 155, 105] mm: the closed finger tips are about 30 mm above
the case rim, the TCP points down, and the TCP Y axis is parallel to the base
Y axis (either sign). The program checks the live scene, MoveIt plan, and
planned TCP geometry before it can send an arm command. If a route clips the
other arm, it searches again up to six times without a fixed waypoint. It
stops at the arrival pose with the gripper closed. There is no descent or
opening.

```bash
/home/ane/camera_code/.venv/bin/python /home/ane/camera_code/right_place_trial/place_right_in_case.py
```

This is plan only. After inspecting the real clearance and planned result,
add `--execute` to close the right gripper and move to the arrival pose.
The offline goal has not been validated on the physical robot. MoveIt may
reject it if the live model reports a collision, including a collision caused
by the stationary left arm.
