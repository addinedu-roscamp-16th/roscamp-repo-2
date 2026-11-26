# Phody ↔ fleet link, phase 1 report (for AI agents)

- Author: Phody owner. Date: 2026-11-26. Human version: [보고_Phody_fleet연결_1차_2026-11-26_사람용.md](보고_Phody_fleet연결_1차_2026-11-26_사람용.md)
- Related: [로봇팔_오류와_상태_Phody_AI용.md](로봇팔_오류와_상태_Phody_AI용.md) (failure model, proposed reasons), [인터페이스_요청_Phody_AI용.md](인터페이스_요청_Phody_AI용.md) (FIX-1..5, DECIDE-1..10)
- Team repo state used: `origin/dev` `52b6da40427b3a39f63b2437a8be09ef6f899502` (CONTRACT 0.8), `origin/docs/design` `ab7d7b0` (SPEC v0.2, IR). Read only; nothing pushed.
- Robot: Phody = `arm_left`, `arm_right`, station `WORK`. Development `ROS_DOMAIN_ID=83`; switch to 6 (IR-81) for integration.

## Implemented interfaces

| Name | Kind | Direction | Type | QoS / source |
| --- | --- | --- | --- | --- |
| `/workcell/fill_basket` | action server | fleet → Phody | `fleet_interfaces/action/FillBasket` | CONTRACT `fill_basket` |
| `/arm_left/state`, `/arm_right/state` | topic, 5 Hz | Phody → fleet | `fleet_interfaces/msg/ArmState` (dev 4 fields) | CONTRACT `qos_profiles.STATE` (best_effort, volatile, depth 5). Name from SPEC 3.5/4.5; not in CONTRACT yet (FIX-1) → Phody-local `config/fleet_names_pending.yaml` |
| `/arm_left/set_mode`, `/arm_right/set_mode` | service server | fleet → Phody | `fleet_interfaces/srv/SetMode` | CONTRACT `robot_set_mode` |

Not implemented (no dev definition or open decision): `set_lock`, `fault_resolved`, `force`, `set_pause`, `/fleet/heartbeat` handling.

## Behavior

### Startup (IR-72)
- New `boot_id` (UUID), mode `UNKNOWN` for both arms, `lock=true` (reported internally only; not used for rejection, `TODO(DECIDE-4)`).
- No `ArmState` is published until the app has written its first state snapshot (first frame).

### `set_mode` (same rules as team `pode/pode_fsm/pode_fsm/admin.py`)
- `NORMAL` / `TEST` → stored per arm, `accepted=true`.
- Any other value → `accepted=false`, `reason=MODE_MISMATCH`, `detail="unknown mode '<v>'"`, not stored.
- Same `request_id` → same reply, not re-applied (per arm, last 32, 60 s; IR-20/21). Empty `request_id` is not remembered. Response echoes `request_id`.

### `fill_basket`
- Goals are always accepted at the ROS level (same pattern as team `pode_fsm/server_ros.py`). Rejection = immediate `abort` with `success=false`, `placed=[]`, `missing=<all requested>`, `reason`:

| Check (in order) | reason |
| --- | --- |
| both arms not in the same mode, or `UNKNOWN` | `MODE_MISMATCH` |
| a goal is running | `NOT_READY` |
| app not idle (queue running, paused, holding, worker running, any arm not idle) | `NOT_READY` |
| `station_id != "WORK"` | `UNKNOWN_TARGET` |
| unknown `OrderItem.kind` | `UNKNOWN_TARGET` |

- `kind` → vision class: `chocobar → chocolate_bar` (Phody-local, `TODO(DECIDE-2)`).
- Execution (app main thread): reset → stereo capture → YOLO-seg + stereo match → keep requested classes → pick `min(requested, found)` objects with the existing assignment (strict left/right alternation; each turn takes the remaining object nearest its own base) → auto execute (same path as `--auto-execute`, both arms in parallel, one arm at a time in the basket area).
- Plan-only failure (approach plan, arm has not moved) with spare recognized objects → re-plan the same arm with the nearest spare. The failed object is not retried.
- Feedback every 0.5 s, **one message per moving arm** (two when both move): `arm_id` (`arm_left`/`arm_right`, `""` while capturing), `phase`, `placed_total`.
  - `phase` (`TODO(DECIDE-3)`): `CAPTURING`, `PICKING` (stage A), `TO_BASKET` (B entry), `PLACING` (C), `RETURNING` (B return).
- Result:

| Case | success | reason |
| --- | --- | --- |
| queue done | true | `""` |
| fewer found than requested, queue done | true | `OUT_OF_STOCK` (`TODO(DECIDE-3)`, meeting 11-26) |
| camera not live / perception exception | false | `RECOGNITION_ERROR` (proposed, undecided) |
| app paused in stage A (`PLAN`, `FINE`, `EXECUTE`) | false | `PICK_FAILED` |
| operator pressed P after an auto-fine pause (item not found) | false | `PICK_FAILED` |
| operator pressed P otherwise | false | `ARM_FAULT` (proposed, undecided) |
| app paused in any other stage | false | `ARM_FAULT` |
| fleet cancel | false (status CANCELED) | `CANCELED`. Same stop as operator P; a held item stays held (`TODO(DECIDE-9)`) |

  - `placed` = successful gripper-open events above the basket during this goal (DECIDE-8). `missing` = requested − placed, filled in request order.
  - On any pause the goal ends at once; the app stays paused for an operator (no automatic return).
- `placed_confirmed` / `uncertain` are not sent (not in dev `FillBasket`).

### `ArmState` (dev fields only: `arm_id`, `state`, `detail`, `stamp`)

| app state | `state` | note |
| --- | --- | --- |
| `idle`, `pick_waiting` | `IDLE` | `TODO(FIX-2)`: `PICK_WAITING` |
| `picking`, `place_waiting` | `PICKING` | `TODO(FIX-2)`: `PLACE_WAITING` (holding, waiting for basket) |
| `placing` | `PLACING` | B entry + C + B return; never both arms |
| `paused`, `error` | `ERROR` | `TODO(FIX-2)`: `PAUSED`. Kept as ERROR by owner decision until dev has `PAUSED` |

- `detail` = the app's stop reason for that arm (service-wide reason when `error`), else `""`.
- An auto-fine pause alone (detector found no stable item in 20 s) keeps the arm `picking` → `PICKING` (operator wait, not a fault).

## Verification

- Unit tests: `jetcobot_dual_app/test/test_fleet_link.py`, 49 tests (real rclpy action/service/topic round trips on isolated test domains 84–99). Stable over repeated runs.
- `fleet:=false` (default): diff against the pre-work backup shows added lines only in `sam_multi_real.py` / `pick.launch.py`; all new lines run only when the fleet link is on. Real run with fleet off: 2 items placed, unchanged behavior.
- Real run with fleet on (2026-11-26 12:28): `MODE_MISMATCH` before `set_mode`; `set_mode NORMAL` accepted on both arms; `fill_basket` chocobar ×2 → capture, 3 recognized, 2 queued (1 spare), left plan + approach OK; stopped at left fine alignment (see Known issues); operator P → result `ARM_FAULT`, `ArmState` `ERROR (operator pressed P)`. **A full successful goal has not been run yet.**

## Known issues / next

1. Fine-alignment detector (`left_detail.py` `WrapperDetector`, HSV yellow connected components) merges adjacent bars into one region and rejects it (top-edge spread > 50 px) → "no detection". Happens with ≥3 tightly placed bars. Plan: collect close-range images and switch fine alignment to a YOLO box detector (box center X / top Y match the current `x` / `y` definitions).
2. Operator resume (`U`) after a pause in concurrent mode does not work reliably (`physical progress unconfirmed`). Out of fleet phase 1 scope.
3. Phase 2 after team answers: `set_lock` (meeting 11-26: release = torque off), `fault_resolved`, `force`, heartbeat loss, pause/resume (Q-05).

## Requests to the fleet team

- FIX-1: add `/{arm}/state` to CONTRACT with `arm: [arm_left, arm_right, arm_ship]` (agreed in the 11-26 meeting).
- FIX-2: SPEC 4.5 `ArmState` fields and constants (`PAUSED`, `PICK_WAITING`, `PLACE_WAITING`, …).
- **FIX-5 (new):** dev `interfaces/fleet_interfaces/CMakeLists.txt` does not list `"msg/ArmState.msg"` in `rosidl_generate_interfaces`, so `from fleet_interfaces.msg import ArmState` fails. Phody's copy adds that one line (`~/phody_fleet_ws/src/fleet_interfaces/PHODY_변경사항.md`).
- DECIDE-2: product code list. DECIDE-3/7: reason codes for operator stop and perception error (see the failure model document).

## Files (Phody side)

| Path | |
| --- | --- |
| `~/phody_fleet_ws/src/fleet_interfaces/` | dev copy + one-line `CMakeLists.txt` fix |
| `jetcobot_dual_app/scripts/multi_pick/fleet_link.py` | fleet link node (own rclpy context, MultiThreadedExecutor thread; callbacks never touch app state, they use a command queue and read a state snapshot) |
| `jetcobot_dual_app/scripts/multi_pick/fake_fleet.py` | terminal fake fleet (`mode`, `fill N [kind]`, `cancel`, `state`) |
| `jetcobot_dual_app/config/fleet_names_pending.yaml` | names not in CONTRACT yet, `kind` mapping |
| `jetcobot_dual_app/scripts/multi_pick/sam_multi_real.py`, `launch/pick.launch.py` | hooks, enabled by `fleet:=true` |

Run (fleet on): source `/opt/ros/jazzy`, `~/phody_fleet_ws/install`, `~/pinky_ws/install`, then
`ROS_DOMAIN_ID=83 ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left concurrent_pick_place:=true fine_moveit_planning:=false fleet:=true`.
