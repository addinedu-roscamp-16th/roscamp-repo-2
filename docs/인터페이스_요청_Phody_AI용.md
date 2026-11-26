# Phody interface change requests (for AI agents)

- Author: Phody owner. Date: 2026-11-25. Human version: [인터페이스_요청_Phody_사람용.md](인터페이스_요청_Phody_사람용.md)
- Target repo: `addinedu-roscamp-16th/roscamp-repo-2`
- Sources compared:
  - `origin/docs/design` `ab7d7b0`:
    - `docs/설계/인터페이스_명세.md` v0.2 (= SPEC)
    - `docs/설계/통신_규칙.md` v1.0 (= IR)
    - `docs/설계/설계_검토_사항.md` (= Q)
    - `docs/설계/fleet_작업_목록.md` (= W)
  - `origin/dev`:
    - `interfaces/fleet_interfaces/contract.yaml` 0.8 (= CONTRACT)
    - `interfaces/fleet_interfaces/{msg,action,srv}`
    - `config/robots.yaml`
- Robot: **Phody** = `arm_left`, `arm_right` (station `WORK`, action `fill`). Server for `/workcell/fill_basket` is one node driving both arms.
- Rules for applying: IR-90 (change CONTRACT + SPEC + IR in the same commit), IR-91 (add reason codes to SPEC 2.2 first), IR-92 (Q items marked open → do not guess; these are proposals for the owner to decide).
- Status keys: `FIX` = inconsistency, apply directly. `DECIDE` = needs an owner decision; the Phody proposal is given.

## FIX items

### FIX-1 `/{arm}/state` missing from CONTRACT
- SPEC 3.5 and 4.5 revive `/{arm}/state` (STATE QoS, about 5 Hz). W-50 expects all three arms.
- CONTRACT 0.8 has no `arm_state` topic and `placeholders.arm: [arm_ship]`.
- Change in CONTRACT:
  ```yaml
  placeholders:
    arm: [arm_left, arm_right, arm_ship]
  topics:
    - {key: arm_state, name: "/{arm}/state", type: fleet_interfaces/msg/ArmState, qos: STATE}
  ```
  Also add `instances: {arm: [arm_ship]}` to `swap_basket` (it already exists), so the wider placeholder does not create `/arm_left/swap_basket`.
- Bump the CONTRACT version and add a changelog line.

### FIX-2 `ArmState.msg` in dev is the old 4-field version
- Replace it with SPEC 4.5 `ArmState`:
  - state constants: `IDLE`, `PICK_WAITING`, `PICKING`, `PLACE_WAITING`, `PLACING`, `RETRYING`, `PAUSED`, `SWAPPING`, `TAKE_WAITING`, `TAKING_FULL`, `PUTTING_EMPTY`, `ERROR`; `GRIPPER_OPEN` / `GRIPPER_CLOSED`
  - fields: `arm_id`, `boot_id`, `state`, `detail`, `lock`, `last_request_id`, `gripper`, `holding`, `order_id`, `placed_total`, `mode`, `forced[]`, `stamp`
- Keep the dev comment "PLACING must not be reported by arm_left and arm_right at the same time".

### FIX-3 `FillBasket.action` in dev lacks SPEC 4.6
- Add the result constants `REASON_PICK_FAILED`, `REASON_RECOGNITION_ERROR`, `REASON_OUT_OF_STOCK`, `REASON_ARM_FAULT`.
- Add the result fields `OrderItem[] placed_confirmed` and `OrderItem[] uncertain`.
- Add feedback phase values (see DECIDE-3).

### FIX-4 `origin/docs/design` still uses the 0.3 names
- `config/robots.yaml` (`arm1..3`) and `interfaces/fleet_interfaces/contract.yaml` (0.3-draft) on `origin/docs/design` are out of date.
- Sync both with `origin/dev`.

### FIX-5 `ArmState.msg` is not built (added 2026-11-26)
- `origin/dev` `52b6da4`: `msg/ArmState.msg` exists, but `CMakeLists.txt` `rosidl_generate_interfaces` does not list it.
- Result: `from fleet_interfaces.msg import ArmState` fails after `colcon build`.
- Change: add `"msg/ArmState.msg"` to `rosidl_generate_interfaces` (together with FIX-1 / FIX-2).

## DECIDE items (Phody proposal included)

| ID | Topic | Gap | Phody proposal | Affects |
| --- | --- | --- | --- | --- |
| DECIDE-1 | Basket pose at `WORK` | Phody places at a fixed basket pose (`jetcobot_dual_control/config/tin_case.yaml`). Pode docking error moves the real basket. | Pick one: (a) docking tolerance rule, for example ±10 mm and ±3°, checked by Pode before `fill_basket` is sent; (b) a basket pose source (new field in the `FillBasket` goal or a topic); (c) Phody measures it with its cameras. Phody prefers (a) now and (c) later. | SPEC 4.6 / Pode dock |
| DECIDE-2 | Product codes | `OrderItem.kind` has no code list. Phody recognizes `chocolate_bar` only (Mentos is disabled). | Add a product code table to SPEC (code, name, which station or arm can handle it). Phody maps codes to vision classes internally. | SPEC 1.2 · 2.2 |
| DECIDE-3 | `FillBasket` feedback `phase` values | Not enumerated. | `CAPTURING`, `PICKING`, `TO_BASKET`, `PLACING`, `RETURNING`, `PAUSED_DISTANCE`, `PAUSED_SERVICE` (string constants in the action file). | SPEC 4.6 |
| DECIDE-4 | `set_lock` semantics for arms | Releasing servo torque on JetCobot drops the arm. | `locked=false`: move to a safe pose, keep torque, exclude the arm from goals, reject motion goals with `LOCK_RELEASED`. Torque release is not part of `set_lock`. | SPEC 4.4 |
| DECIDE-5 | `fault_resolved` response time vs IR-40 (2 s) | Phody clears its execution-gate fault latch only after the arm has been stationary and collision-free for 2 s, so the reply takes longer than 2 s. | The service replies at once with `accepted=true` ("started"). The final result shows in `ArmState` (`state` leaves `ERROR`). fleet confirms by observation within 30 s (IR-62). Alternative: a per-command timeout of 5 s. | IR-40 · IR-62 |
| DECIDE-6 | Heartbeat loss behavior for arms | IR table: stop after 1.5 s of silence. Measured Wi-Fi silences were 7 to 8 s (11-25, logs). A stop in mid-transport leaves the arm holding the item in the air. | On loss: finish the current joint goal, then hold. Report `state=PAUSED`, `detail=HEARTBEAT_LOST`. When the heartbeat returns, resume only after a fleet command (the Q-05 signal). | IR-40 table, Q-05 |
| DECIDE-7 | Arm reason codes | SPEC 2.2 has only `ARM_FAULT` for arm faults. | Add to SPEC 2.2 (IR-91): `ARM_FEEDBACK_LOST`, `ARM_UNREACHABLE`, `ARM_COLLISION_CHECK`, `GRIPPER_FAULT`, `HEARTBEAT_LOST`. Use them in `ArmState.detail`; `FillBasket.reason` stays `ARM_FAULT`. | SPEC 2.2 |
| DECIDE-8 | Meaning of `placed_confirmed` | Phody has no basket sensor. | Count one item when the gripper opens successfully above the basket. | SPEC 4.6 |
| DECIDE-9 | Cancel while holding an item | Not defined. | Finish placing into the basket if already past the basket entry point, otherwise stop and report it in `uncertain` with `holding=true`. | SPEC 4.6, Q-03 |
| DECIDE-10 | Mapping of Phody stages to `ArmState.state` | Phody stages: A = see_center ↔ pick; B = see_center ↔ pre_place (MoveIt); C = pre_place ↔ place. Two arms run in parallel; only one arm uses the basket at a time. | A → `PICKING`; waiting for the basket → `PLACE_WAITING`; B entry + C + B return → `PLACING` (basket owner, never both arms); otherwise `IDLE`. | SPEC 4.5 |

## Answers proposed for open Q items

- **Q-05:** new service `/{arm}/set_pause`, type `SetPause.srv`:
  - request: `string request_id`, `string robot_id`, `bool paused`
  - response: the common response (`accepted`, `reason`, `request_id`, `detail`)
  - It is a target value (IR-21). Phody pauses between joint goals; `paused=false` continues with the next goal. Add the service to CONTRACT and SPEC 3.5.
- **Q-06:** with one arm unlocked, the `FillBasket` goal continues with the remaining arm. Items it cannot reach go to `missing` with `detail=ARM_UNREACHABLE`.
- **Q-03:** Phody reports `holding` from its gripper-close and placement checkpoints. `uncertain` is non-empty if and only if the goal ends while `holding=true`.

## Phody implementation status (for the fleet team)

Phody does not use `fleet_interfaces` yet. Planned, in order:

1. `/workcell/fill_basket` server
2. `/{arm}/state`
3. `set_lock` / `set_mode` / `force` / `fault_resolved`
4. Heartbeat handling
5. Startup rules (IR-72)

Phody develops on `ROS_DOMAIN_ID=83` and switches to 6 (IR-81) for integration tests.
