# Validation — 2026-11-06

## Scope

PC workspace: `/home/ane/pinky_ws`, ROS 2 Jazzy. Existing single-arm tools and
Gazebo model files were left unchanged by this package implementation.
The physical layout specified by the user is first base `(0,0,0)`, second base
`(0,+0.30,0)` metres, same orientation. `left` and `right` denote first and second.

## Performed checks

- `colcon build --symlink-install --packages-select jetcobot_dual_real` succeeded.
- ROS ament package discovery confirmed with `ros2 pkg prefix jetcobot_dual_real`.
- Four pytest tests passed: model/limits/mimic references, inter-arm collision
  checks retained, measured-state aggregation/freshness, invalid states and
  wrong-arm trajectory names rejected.
- Both namespaced SDK bridge variants were instantiated against a **fake SDK**.
  Publisher and action names were distinct, valid prefixed targets parsed,
  opposite-arm targets rejected, and default motion-disabled goals rejected.
  No actual serial port was opened and no SDK setter or motion method was called.
- Synthetic runtime check forced ROS domain **219** and localhost discovery,
  separate from physical robots on domain 42. Launched the actual new aggregator,
  robot_state_publisher and move_group with `rviz:=false` and generated two
  artificial native joint-state streams.
- Runtime verified 12 correctly prefixed measured joints and right base TF at
  `(0,+0.30,0)`.
- Synthetic home state passed MoveIt collision validity at this layout.
- Real MoveIt planning service produced trajectories for `arm_left`, `arm_right`
  and `both_arms` with synthetic 3-degree joint-1 goals. No execution requests sent.
- No trajectory-execution action or hardware SDK action endpoints were present
  in the isolated PC planning test.
- Stopping the right synthetic state stream stopped combined state publication
  after the stale timeout; old measurements were not given a fresh timestamp.

Commands:

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
/usr/bin/python3 -m pytest -q ~/pinky_ws/src/jetcobot_dual_real/test
/usr/bin/python3 ~/pinky_ws/src/jetcobot_dual_real/test/runtime_smoke.py
```

The runtime script itself forces domain 219 / localhost and never opens serial
or starts either Pi bridge. Detailed latest log:
`/tmp/jetcobot_dual_real_runtime.log`.

## Limits and observed warnings

- Neither Pi was deployed to or operated as part of this implementation.
- Actual dual physical-state display, GUI interactions, robot motion, controller
  tracking, cross-arm execution coordination and physical collision safety remain
  unverified. All successful planning evidence above is synthetic-state evidence.
- Model accuracy, per-robot zero/wrist calibration and base mounting positions
  must be checked against the physical setup. Gripper geometry is based on an
  approximate SDK mapping/default, not calibrated finger measurements.
- Runtime startup reports no octomap sensor plugin because no depth source is
  configured; physical tables and objects are not automatically added to the scene.
- The installed `move_group` was observed to segfault during shutdown, after
  completing functional checks. The stack trace ends in rclcpp callback-group /
  node destruction. This is recorded separately from runtime planning success;
  this package does not claim clean MoveIt shutdown or fix upstream libraries.
- SDK execution remains one native endpoint goal, with no verified replay of
  MoveIt's path/timing. The PC launch defaults to no execution; selected-arm
  small-motion tests are explicitly opt-in as described below.
- Two-camera reception and hand-eye transforms are separate follow-up work.

## Selected-arm execution addition — same date

- Added `execution_arm:=none|left|right`, default `none`. Only the selected arm's
  guarded controller is registered; the combined execution group is removed.
- Added a measured `trial_j1_plus_3deg` named target at opt-in launch startup.
  This is a target definition only, not a startup movement.
- Initial trial gate accepts one moving joint, at most 5deg per joint, and rejects
  stale starts, foreign-arm joints, excessive trial velocity and preview detours.
- The gate requires both measured states; peer movement or loss of feedback
  during execution requests cancellation and latches a fault. Cancellation does
  not verify actual stopping; restarting the gate is not a physical safety reset.
- Seven pytest cases passed after the extension.
- `test/execution_smoke.py` forces domain 219 / localhost and provides synthetic
  joint states and a fake Pi FollowJointTrajectory server. It verified wrong-arm
  rejection, one-at-a-time forwarding, duplicate rejection, result propagation,
  peer-motion cancellation and a fault blocking the next request.
- Full MoveIt Plan-and-Execute -> gate -> fake SDK -> MoveIt success was verified.
  No physical robot action server was contacted and no actual movement was sent.
- The observed MoveIt shutdown segmentation fault remains separate from successful
  planning/execution-proxy functional checks.

```bash
/usr/bin/python3 ~/pinky_ws/src/jetcobot_dual_real/test/execution_smoke.py
```

Physical single-joint tests, actual stopping and shared-space collision safety
remain unverified by these synthetic checks. This is not multi-arm trajectory tracking.

## Near-home return adjustment

The user confirmed the first selected-arm trial and a manual joint-1 return worked.
The all-zero HOME target was rejected by the gate because it also corrects several
small nonzero measured joint offsets. The gate now permits multiple small joint
corrections only when every target is within 0.1deg of zero. The 5deg per-joint
movement envelope, velocity, freshness and no-detour checks remain enforced.
An additional regression test accepts a near-home return with six small offsets
and rejects a greater-than-5deg return. No real motion was sent by this change.

## Both SDK connections in one launch

Added `execution_arm:=both`: two MoveIt controllers and two Pi action clients
share one execution gate and one busy/fault lock. The combined `both_arms` group
remains absent in execution mode, so only arm_left / arm_right can be executed.
No simultaneous-arm execution is enabled.

Build and all eight pytest checks passed. An isolated synthetic test verified
left and right action connections, rejection of a competing right request during
left execution, and full MoveIt Plan-and-Execute for left and then right through
the SAME MoveIt process. It also verified peer-movement cancellation and fault
latching. Requests reached only the matching fake Pi servers. No physical robot
commands were sent during these tests.

```bash
/usr/bin/python3 ~/pinky_ws/src/jetcobot_dual_real/test/execution_smoke.py --both
```

## Robot label correction

User clarified left = new robot 192.168.0.33 and right = original robot
192.168.0.22. Preserve the original (.22) base as world origin: right=(0,0,0),
left=(0,+0.30,0). Updated layout, instructions and validation assertions. Earlier
validation observations used the old labels; the robot-independent geometry and
SDK logic are unchanged. Each Pi must be restarted with its corrected robot_side
and namespace before physical execution.

## Dual gripper execution connection

Added MoveIt guarded left/right gripper trajectory controllers and PC action
proxies to the existing Pi gripper_group_controller endpoints. No Pi bridge files
were changed. The arm/gripper requests share the existing global busy/fault lock.
Gripper requests validate the corresponding single joint, all finite bounded
points and immediate monotonic trajectories; both arm states must remain fresh
and both arms are monitored for movement during gripper operation. Arm 5deg
limits are unchanged.

Build and ten pytest cases passed. The isolated --both execution smoke test
verified full MoveIt planning and execution of left close/open and right close/open
through matching fake Pi servers, plus rejection of a gripper goal while an arm
was active. Existing left/right arm routing and peer-cancellation checks also
passed. These are synthetic action-server tests; no actual gripper command was
sent, and physical open/close or loaded grasp success is not yet verified.

## Invalid gripper feedback / false satisfied-goal correction

Live domain-42 read-only diagnosis observed both robots repeatedly logging
`Invalid gripper reading: 181`, no valid gripper_value samples, and the PC
publishing 0.535 for both from configured defaults. This explains MoveIt skipping
OPEN as already satisfied. No physical getter was called from a second serial
process; diagnostics used existing ROS status and rosout topics.

Removed unmeasured gripper defaults from combined display/planning states.
Invalid feedback clears prior estimates; missing/stale gripper values are omitted
and marked unavailable. The execution gate requires fresh valid feedback before
accepting a gripper goal. The Pi bridge now defaults gripper_type to 0, meaning
use argument-less get_gripper_value(), consistent with the existing argument-less
setter/project source. Explicit types remain configurable. Invalid reads publish
a -1 sentinel; valid reads during gripper execution are also published.

Software regression checks cover omission of missing/stale/181 feedback and
argument-less versus explicit getter selection. The full synthetic left/right
gripper Plan-and-Execute smoke test passes with valid fake feedback. No gripper
motion or persistent device setting was changed on real hardware by the agent.
Both Pi files need redeployment, and real argument-less getter readings still
require verification after restart. 181 is not clamped or assumed to be OPEN.

## Brief arm feedback gap during gripper commands

User reported physical opening worked but subsequent closing failed. PC logs
showed the open operation crossed the 1.5s arm-state age limit; aggregator status
recovered roughly 0.3s later. The fault latch then rejected subsequent close
requests before SDK dispatch. A shared serial-query delay is a plausible cause;
no direct serial probing was performed alongside the active bridges.

Only in-flight gripper monitoring now tolerates up to 3s arm-state age, while
requiring both arms stationary. Goal acceptance and arm execution retain 1.5s.
Aggregator behavior is unchanged and does not refresh stale source timestamps.
Outages beyond 3s still trigger cancellation/fault handling. No hardware-safety
certification or verified physical stopping is implied by this bounded tolerance.

Thirteen pytest cases passed. Full isolated MoveIt/fake-Pi smoke checks passed,
including an injected 1.8s peer feedback gap during gripper operation, close/open
for both sides, cross-arm rejection and cancellation/fault handling. The physical
close operation after this adjustment still requires user verification.

## Notebook-only per-request angle limit removal

At the user's explicit request, removed the PC gate's 5deg displacement check.
Hardware joint bounds, selected-side naming, start-state match, one-moving-joint
restriction (near-zero HOME exception), velocity/path validation, freshness checks
and shared execution/fault lock remain. Pi scripts/parameters were not changed;
max_move_deg:=5.0 on a Pi still limits that robot. The previously exported Git
snapshot is a separate historical copy and was not changed by this local update.

Regression validation accepts a 30deg single-joint request with sufficiently slow
timing and still rejects a joint-1 target beyond 168deg. No real robot commands
were sent.

## Notebook multi-joint restriction removal

At the user's explicit request, removed the PC gate's one-moving-joint check and
its special near-zero HOME exception. Multiple joint targets are now accepted
under the same hardware bounds, start-state, velocity, no-detour, feedback and
shared execution-lock checks. Pi max_move_deg is unchanged. A regression test
accepts six-axis straight joint-space motion and rejects an intermediate detour.
No physical motion was sent, and the prior uploaded Git snapshot is unchanged.

## 2026-11-12 hybrid dual-arm routing (local only)

- Real dual URDF collision meshes loaded; conservative sphere separation calculated for both arms. A far straight synthetic plan routed to endpoint, while near or curved plans routed to waypoints.
- `pytest -q test`: 26 passed with the ROS and workspace overlays sourced.
- Isolated `colcon build --packages-select jetcobot_dual_real`: passed in `/tmp/jetcobot_hybrid_install`.
- `execution_smoke.py --hybrid` and `--hybrid --both`: fake Pi actions passed for both path and endpoint routes, sequential left/right MoveIt plan/execute, shared lock, peer drift cancellation, and gripper routing.
- No Pi files were copied and no physical motion was commanded. Continuous SDK path, clearance, timing, and smoothness remain unverified on hardware.

## 2026-11-12 queued lookahead waypoint progress fix

- Reproduced the previous logic in a fake feedback case: the arm reaches the queued next segment before a feedback sample near the current waypoint. New logic accepts only measured progress inside the planned next joint segment corridor.
- A lateral miss with forward projection remains outside the tolerance.
- `pytest -q test`: 28 passed; isolated package build passed. No Pi deployment or real-arm motion in this session.

## 2026-11-12 path send timing diagnostics

- Added `PATH SEND` timestamps and inter-command gaps to the existing sparse path bridge without changing its control decisions.
- Full package unit suite: 28 passed. No Pi deployment or hardware run by Codex.

## 2026-11-12 optional two-point SDK lookahead

- The user log showed waypoint 2 at 83% of the next segment before command 4 was sent, and waypoint 5 beyond the next target before command 7 was sent. This supports a possible queue starvation gap; it does not prove firmware blending behavior.
- Added opt-in `queue_lookahead_depth:=2`; depth 1 remains default. Stop requests `clear_queue()` after SDK stop in depth 2.
- Synthetic queue prefill, rapid measured progress, and stop/clear order tests passed; full package suite 30 passed and isolated build passed. No Pi deployment or hardware execution by Codex.

## 2026-11-12 right-arm close-path abort and planning padding

- User hardware log: right SDK path aborted at waypoint 2/42 with `next_deviation_deg=2.002107...` and a 2.00° corridor. The user reported a very close path and possible physical contact. Actual contact point and robot geometry have not been verified.
- Stopped the PC execution-capable MoveIt launch. No robot motion was commanded by Codex. Pi bridges were left running.
- Added `collision_padding_m` launch argument, default 0.05 m, applied to MoveIt `default_robot_padding` for each collision link. Range is 0..0.10 m.
- Plan-only MoveIt scene inspection with explicit 0.05 m showed all 30 links padded and current left/right start states valid. This does not validate the failed path or guarantee physical clearance.
- Isolated build and actual workspace package build passed. Physical rerun is deferred until actual contact, base placement, and replanned closest approach are checked.

## 2026-11-12 right bridge still lacks two-point lookahead deployment

- Right Pi live ROS parameter list has `queue_lookahead=true` but no `queue_lookahead_depth`; `path_step_deg=4.0`, `min_command_interval_s=0.75`, `waypoint_tolerance_deg=2.0`. Thus the running right bridge predates the local two-point queue implementation.
- The waypoint 3/25 failure had `next_progress=0.716` and `next_deviation_deg=2.211`, above the 2.00-degree measured path corridor. Additional queued commands alone do not establish that this deviation or a reported possible contact is fixed.
- SSH to right Pi at 192.168.0.22 was denied (publickey,password), so no Pi file was copied or restarted by Codex. The operator must deploy and explicitly pass `queue_lookahead_depth:=2`; verify the parameter and PATH START log before any open-space test.

## 2026-11-12 configurable SDK path speed

- Added `path_sdk_speed` (1..100; default 10) to the path bridge, used only by waypoint `send_angles`; logs now include the configured speed.
- Synthetic test verified speed 15 reaches each waypoint command. Full package tests: 31 passed. No Pi deployment or physical run by Codex.
- Proposed right separated-arm trial is speed 15, path step 6°, send interval 0.5 s, lookahead depth 2, waypoint tolerance 2°. The prior 34-command path and its deviation do not establish that faster speed will eliminate vibration.

## 2026-11-12 right two-point path abort and queue-clear failure

- User log at right waypoint 2/20 showed `next_progress=1.889`, `after_progress=0.889`, and `after_deviation_deg=2.821` against a 2° intermediate corridor. This confirms that the arm passed the second waypoint and approached the fourth; it does not show an empty SDK queue. The reported -12.62° J4 target error compares against waypoint 2 after that progress, so it is not a direct measure of gravity sag.
- `clear_queue()` returned -1 after `stop()`. The vendor documents 1 as a successful clear response; the live log therefore does not confirm removal of queued commands. Three read-only right joint-state samples after the abort were identical.
- The local path bridge now latches any non-1 clear reply or exception and rejects subsequent arm goals until restart/inspection. Full package suite: 32 passed. This change has not been deployed to the Pi or physically tested.
- Do not increase SDK speed or corridor tolerance based on this log alone. The visible stop-and-dip behavior needs hardware and timed-feedback diagnosis.
