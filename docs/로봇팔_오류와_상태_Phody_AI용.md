# Phody arm failures and states (for AI agents)

- Author: Phody owner. Date: 2026-11-26 (draft). Human version: [로봇팔_오류와_상태_Phody_사람용.md](로봇팔_오류와_상태_Phody_사람용.md)
- Purpose: input for the fleet team to define `FillBasket.reason`, `FillBasket` feedback `phase`, and `ArmState.detail` for Phody (DECIDE-3, DECIDE-7, FIX-3 in [인터페이스_요청_Phody_AI용.md](인터페이스_요청_Phody_AI용.md)).
- Status: proposal. Nothing here is in the team SPEC/CONTRACT yet. Do not apply without an owner decision (IR-92).

## Robot model

- Phody = two 6-DoF JetCobot arms, `arm_left` and `arm_right`, station `WORK`, one `/workcell/fill_basket` server for both arms.
- Per goal: capture once (stereo, both wrist cameras) → assign detected items to arms alternately → each item runs stages A → B → C → B.
  - A: `see_center` → approach → fine align (camera) → descend → gripper close → lift → `see_center`. Joint goals (IK).
  - B: `see_center` ↔ `pre_place`. MoveIt trajectory.
  - C: `pre_place` → descend → gripper open → `pre_place`. Joint goals (IK).
- Concurrency: both arms move in parallel. Only one arm at a time owns the basket (B entry, C, B return). This matches the dev `ArmState` comment that `PLACING` must not be reported by both arms.
- Every motion passes an execution gate on the PC: collision check against the other arm's remaining path, then the goal goes to the arm's Pi over Wi-Fi.
- No grasp sensor and no basket sensor. Gripper is open/close only.
- Servo torque off (`set_lock` release, per the 11-26 meeting) makes the arm fall under its own weight and drops any held item.

## Failure points

On any failure the app pauses and waits for an operator (resume or return). There is no automatic retry today.

| id | stage | event | cause | holding item |
| --- | --- | --- | --- | --- |
| F1 | capture | fewer items detected than requested | real shortage or occlusion; Phody cannot tell which | no |
| F2 | A plan | no IK solution for approach | item near reach limit or awkward yaw | no |
| F3 | before any motion | collision check rejects the goal | paths of the two arms overlap | unchanged |
| F4 | A fine align | camera detection not stable within 20 s | lighting, occlusion | no |
| F5 | A grasp | item not grasped or slipped | position error; not detected by a sensor | unknown |
| F6 | any | joint state feedback lost (seconds) | Wi-Fi gaps, slow servo reads on the Pi; 7–8 s gaps observed 2026-11-25 | possibly, mid-air |
| F7 | any | execution gate fault latch | F6 during motion, or arm did not reach the goal; both arms stop; operator must clear | possibly |
| F8 | after C | TCP not back at `see_center` height | same as F6/F7 | no (already placed) |

## Proposed values

`FillBasket` result `reason` (string constants in the action result):

| value | failures |
| --- | --- |
| `OUT_OF_STOCK` | F1 (11-26 meeting: treat shortage as out of stock for now; cannot be distinguished from recognition error) |
| `PICK_FAILED` | F2, F4, F5 |
| `ARM_FAULT` | F3, F6, F7, F8 |
| `CANCELED` | goal canceled (already in dev) |

`ArmState.detail` (add to SPEC 2.2 first, IR-91): `ARM_UNREACHABLE` (F2), `ARM_COLLISION_CHECK` (F3), `FINE_ALIGN_FAILED` (F4), `ARM_FEEDBACK_LOST` (F6), `ARM_FAULT_LATCHED` (F7, F8).

`FillBasket` feedback `phase`: `CAPTURING`, `PICKING` (A), `TO_BASKET` (B entry), `PLACING` (C), `RETURNING` (B return).

`placed`: number of successful gripper-open events above the basket (DECIDE-8).

## What Phody can and cannot report

- Can: current stage per arm, paused or not, failure id, placed count, operator resume/return.
- Cannot: whether an item is really held (no sensor), shortage vs recognition error (F1), whether the item landed inside the basket.

## Open questions for the next meeting

1. On a failure, should fleet end the goal (`success=false`) or wait while the operator resumes? Phody currently pauses and waits.
2. Accept the `reason` / `detail` values above into SPEC 2.2 and the action file?
3. `set_lock` release = torque off: define what happens to a held item and the falling arm (for example, release only while the arm is supported, or only with no item held).
