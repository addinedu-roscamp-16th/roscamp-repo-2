# 주문 코어 1차: 채우기 · 출고 · 교환 · 보고 설계

- 날짜: 2026-11-27 · 사용자: 범위 1차, 방법 A 선택, 설계 승인 (2026-11-27)
- 브랜치: `feat/TT-27-fleet-dev-merge-2026-11-27` (최신 `dev` = contract 1.0 + TT-27 보고 연결)
- 대상 파일: `pc/fleet_core/fleet_core/orders/orchestrator.py` (`OrderOrchestrator`)
- 근거: 같은 폴더 `pseudocode.md`(v2.4, 2026-11-19), contract 1.0 팔 관제 합의(PR #21, `arm/jobs.py` · `common/commands.py`), TT-27 보고 대기열(`fleet_manager/report_queue.py`)

## 1. 왜 하는가

- 관제 코어는 틱마다 `orchestrator.step(robot)`을 부르지만(`manager_route.py:20` · `:24`) `step`과 `on_arm_report`의 몸통이 비어 있습니다.
- 그래서 진짜 fleet은 Pode를 작업대에 묶은 뒤 담기 목표(`ArmFillCmd`)를 한 번도 내지 않습니다. 운반 건은 `ASSIGNED`에서 멈추고 Phody에는 담기 명령이 가지 않습니다.
- 이 작업이 끝나면 진짜 fleet + Phody(또는 가짜 팔)로 주문 한 건이 담기 → 출고 → 교환 → 끝 상태 보고까지 돌고, 담기 일시 중지 · 보류 · [복구]의 연동 시험을 할 수 있습니다.

## 2. 목표 · 범위 밖

**목표 (1차)**
1. 채우기 (`pseudocode.md` 3장 4단계): 남은 상품으로 `ArmFillCmd`, 다 담으면 마무리
2. 출고대(OUTBOUND)로 이동 (5단계): 호출 · 실패 재시도 · 한도 넘으면 `RobotFaultCmd`
3. 교환 (6단계): `ArmSwapCmd`, 찬 바구니를 빼면 끝 상태 보고, 빈 바구니를 놓으면 놓아 줌
4. 보고 (7단계): `ReportOrderCmd`(`SHIPPED` · `PARTIAL` · `CANCELED` · `HELD`), 진행 보고 `ReportTripCmd`(`FILLING` · `TRANSPORTING` · `FILLING_PAUSE`)
5. 1차 쉼 (1단계): 바구니가 없는(`NONE`) 빈 Pode는 빈 바구니를 받으러 OUTBOUND로 감
6. contract 1.0 팔 보고 5종: `ArmFillProgress` · `ArmFillPaused` · `ArmFillPlaced` · `ArmFillResumed` · `ArmStationBlocked`
7. 팔 고장 `ArmFault` → `HELD(ARM_FAULT)` (5장의 "팔 고장"에서 격리 부분을 뺀 것)
8. 주문판 정리 (2단계의 남은 부분): 최종 보고 집합 · 보류 목록을 새 주문판에 있는 id만 남김, 보류 · 최종 보고 건은 대기 주문에서 뺌
9. 서비스 정지(`run_mode = PAUSE`): 담기를 시작한 운반 건은 끝까지, 새 일은 막음 (4.8, 사용자 결정 2026-11-27)

**범위 밖 (2차)**
- 격리 `on_robot_isolated`, 치움 `on_robot_removed`, 다시 투입 `on_robot_reinstated` (지금처럼 빈 함수)
- 늦은 결과 `ArmLate`의 수량 반영(1차는 알림만, 4.6)
  - 메모: HELD가 될 때 진행분(`progress`)은 이미 `placed`에 들어 있으니(`ArmFault`가 옮김), 늦은 결과를 더할 때는 그만큼을 빼고 셉니다.
- 운반 건 명령 `on_trip_command`(`resume_fill` · `force_ship` · `force_partial` · `return_to_shelf` · `resume_held` · `requeue_trip`), `on_force_ship`(지금처럼 `NOT_IMPLEMENTED`)
- 그래서 1차에서 `FILLING_PAUSE`와 `HELD` 건은 그 상태로 멈춰 있고, Pode도 작업대에 묶여 있습니다.

**하지 말 것**
- 바깥 접점(생성자 · 입력 함수 이름과 인자 · `take_output` · `order_of` · `robot_view` · `shipped`)을 바꾸는 것 (`pseudocode.md` 1장, `manager_route` · `manager_views` · 시험 틀이 씀)
- 같은 상품을 두 번 담게 되는 재요청 (담은 수를 잃거나 두 번 더하는 것)
- 주문 코어가 팔 재시도 횟수 · 시간 초과를 직접 세는 것 (ArmJobs 몫)

## 3. 방법

| 안 | 내용 | 판단 |
|---|---|---|
| **A (고름)** | `pseudocode.md` 그대로 `orchestrator.py` 안에 채움. 로봇 기록을 작은 자료형으로 두고 1.0 보고 5종을 더함 | 팀이 합의한 흐름과 파일 구조를 따르고 바깥 접점을 바꾸지 않음. 파일은 약 500 ~ 600줄 |
| B | 주행 관제 담당의 개인 저장소 구현(의사코드 6장 언급)을 가져와 맞춤 | 볼 수 없고 v2.4 기준이라 1.0 보고를 새로 붙여야 함 |
| C | `filling.py` · `shipping.py` 등으로 나눔 | 지금 구조와 담당의 다음 작업을 크게 바꿈. 1차 범위에 비해 큼 |

## 4. 동작

### 4.1 로봇 한 대의 기록 (`_RobotRecord`, `pseudocode.md` 2장)

| 칸 | 값 · 처음 값 |
|---|---|
| `stage` | `""` · `FILLING` · `SWAPPING` |
| `basket` | `EMPTY`(처음) · `FULL` · `NONE` |
| `order` | 묶인 주문 `_Bound(order_id, items, placed, missing, progress, reported_filling)` 또는 `None` |
| `last_order_id` | 교환 명령에 쓰는 마지막 주문 id |
| `outbound_goal` | 답을 기다리는 OUTBOUND 호출 id 또는 `None` |
| `dispatch_failures` · `dispatch_retry_at` · `dispatch_reason` | OUTBOUND 실패 횟수 · 다시 보낼 시각 · 마지막 이유 |
| `asked` | `""` · `fill` · `swap` · `paused` · `resume_wait` |
| `bound` | 주문을 묶을 때마다 1씩 오름 |
| `held` | `""` 또는 `ARM_FAULT` |

- 지금 코드의 `self._bound: robot -> OrderRecord`는 이 기록으로 옮깁니다. `order_of` · `robot_view`는 같은 값을 돌려줍니다. `robot_view`의 `placed`는 이제 실제 누적값입니다.
- `asked`의 `paused` · `resume_wait`은 1.0 때문에 더한 값입니다(4.4). `step`은 `asked`가 비어 있지 않으면 기다립니다.

코어 전체: 주문판, 최종 보고 집합, 보류 목록(주문 id → (로봇, 사유)), 용량 초과 집합(지금 있음), 열린 WORK 호출 목록(지금 있음), 막힌 작업대 집합(4.5).

### 4.2 `step(robot)` (`pseudocode.md` 3장 1 · 4 · 5 · 6단계)

- `held`면 아무것도 하지 않습니다.
- `asked`가 비어 있지 않으면 기다립니다.
- **stage `""`**: 주문이 없고 basket `NONE`이고 OUTBOUND 호출 · 재시도 대기가 없으면 빈 바구니를 받으러 `DispatchCmd(새 id, OUTBOUND, robot)`. 그 밖에는 아무것도 하지 않음.
- **stage `FILLING`**:
  - 남은 것 = `items - placed - missing` (0 이하 품목은 뺌).
  - 남은 것이 있으면 `asked="fill"`, `ArmFillCmd(robot, WORK, order_id, 남은 것)`. 이 주문에서 처음 내는 것이면 `ReportTripCmd(FILLING, placed, missing)`을 한 번 냅니다(`reported_filling`).
  - 남은 것이 없으면 마무리: `ArmResetCmd(robot)`, `missing = items - placed`.
    - 담은 것이 하나도 없으면 `ReportOrderCmd(CANCELED, placed={}, missing, "nothing could be placed, missing …")`, 최종 보고 처리(4.7), stage `""`, `ReleaseCmd(robot)`.
    - 그 밖에는 basket `FULL`, stage `""`, 5단계(출고 이동)로: OUTBOUND 호출을 내고 `ReportTripCmd(TRANSPORTING, placed, missing)`.
- **OUTBOUND 재시도** (stage `""`이고 `dispatch_retry_at`이 지났을 때): 실패 횟수 > `dispatch_retry_limit`(3)면 `RobotFaultCmd(robot, reason)` + `EventOut(ERROR, DISPATCH_FAILED)`만 내고 재시도 기록을 지움. 아니면 `EventOut(WARN, DISPATCH_RETRY)` + 새 id로 다시 호출.
- **stage `SWAPPING`**: basket `EMPTY`면 stage `""`, `ReleaseCmd`. 아니면 `asked="swap"`, `ArmSwapCmd(robot, OUTBOUND, last_order_id, take_full=(basket == FULL), put_empty=True)`.

### 4.3 `on_dispatch_result` (지금 코드 + OUTBOUND)

- 열린 WORK 호출이면 지금 코드처럼 묶습니다. 묶을 때 로봇 기록에 `order`를 만들고 `bound += 1`, stage `FILLING`, 이미 있는 `EventOut(ORDER_BOUND)` · `ReportTripCmd(ASSIGNED)`를 냅니다.
  - 묶을 자격: 아는 로봇이고 `order`가 없고 `held`가 아니고 stage `""`이고 basket `EMPTY`.
  - 성공했고 자격이 있는데 대기 주문이 없으면 `ReleaseCmd(robot)`를 냅니다(`pseudocode.md` 2단계. 지금 코드는 놓아 주지 않아 작업대 구역을 잡은 채 남음).
  - 성공했지만 자격이 없으면(예: `held` 로봇) 묶지도 놓아 주지도 않습니다. `held` 로봇을 놓아 주면 붙잡아 둔 뜻이 사라지기 때문입니다. (내 판단)
- 어느 로봇의 `outbound_goal`이면 그 로봇의 OUTBOUND 결과입니다. `outbound_goal`을 비우고
  - 성공: stage `SWAPPING`, 실패 횟수 0.
  - 실패: 실패 횟수 +1, `dispatch_retry_at = now + dispatch_retry_wait_s`(2.0 s), 이유 저장.

### 4.4 `on_arm_report(report)`

모르는 로봇 이름은 무시합니다. `ArmStationBlocked`는 로봇이 아니라 작업대 id를 씁니다(4.5).

| 보고 | 처리 |
|---|---|
| `ArmDeclined(robot)` | `asked`가 `fill` · `swap`이면 비움. 다음 `step`에서 다시 묻습니다 |
| `ArmFillResult(robot, success, placed, reason, missing, placed_confirmed, uncertain, held)` | `asked` 비움, `order.placed += placed`, `progress` 비움. 성공이면 `missing = items - placed`. 실패면 `ArmFailedCmd(robot)` (재시도할지는 ArmJobs가 정함: `NOT_READY`만 재시도, 나머지는 이어서 `ArmFillPaused` 또는 `ArmFault`가 옴) |
| `ArmFillProgress(robot, placed)` | `order.progress = placed`(지금 목표의 진행). `ReportTripCmd(FILLING, order.placed + progress, order.missing)` |
| `ArmFillPaused(robot, server, reason, detail)` | `asked="paused"`. `ReportTripCmd(FILLING_PAUSE, order.placed, order.missing, reason)`. 1차에는 풀 길이 없음(2차 [담기 재개]) |
| `ArmFillPlaced(robot, placed, held)` | 재시작으로 목표가 사라짐. `order.placed += placed`, `progress` 비움, `asked="resume_wait"`. `held`면 아무것도 더 하지 않음(이어서 `ArmFault`가 옴). **운반 건 보고는 내지 않습니다**: [복구]를 기다리는 동안 운반 건은 `FILLING` 그대로이고 관리자는 ArmJobs의 `ARM_RESTARTED`(WARN) 이벤트로 압니다(팔 관제 계약 정리 2판 3.3 "보고하지 않는 경우"). 담은 수는 바로 앞 `ArmFillProgress` 보고에 이미 실렸습니다 |
| `ArmFillResumed(robot)` | `asked`가 `resume_wait`이면 비움. 다음 `step`이 남은 상품으로 `ArmFillCmd` |
| `ArmSwapResult(robot, took_full, put_empty_done)` | `pseudocode.md` 6단계: `asked` 비움 → took_full이고 basket `FULL`이면 basket `NONE` + 끝 상태 보고(4.7) → put_empty_done이고 basket `NONE`이면 basket `EMPTY`, `ArmResetCmd`, stage `""`, `ReleaseCmd` → 그 밖에는 `ArmFailedCmd(robot)` |
| `ArmFault(robot, kind, server, reason)` | `asked` 비움, `held = ARM_FAULT`. 주문이 있으면 `ReportOrderCmd(HELD, placed, items - placed, reason, ARM_FAULT)`, 보류 목록에 넣음. 주문은 로봇에 묶인 채 둡니다 |
| `ArmLost(robot, …)` | 1차는 `asked`만 비움(바로 뒤 `ArmFault`가 옴) |
| `ArmLate(goal_id, kind, …)` | 1차는 아무것도 내지 않음(잃은 작업 기록이 없어 pseudocode 5장의 '맞는 잃은 작업 없음 → 무시'에 해당). 수량 반영과 알림은 2차 |

- **진행 보고의 `missing`**: 담는 중에는 실제로 못 담은 것(`order.missing`)만 싣습니다. 남은 상품을 `missing`으로 싣지 않습니다(브리지가 `qty_missing`=못 담은 수로 씀). (내 판단, 다음 주 논의 1번을 이것으로 맞춤)
- **`placed`는 `placed`를 씁니다**(`placed_confirmed`가 아님). 1.0 계약에서 Phody는 둘을 같게 보냅니다. (내 판단)
- **`FILLING_PAUSE`의 `missing`**: `order.missing`(그때까지 확정된 못 담은 것)만 싣습니다. 팔 결과의 `missing`은 [보충 불가]를 고를 때(2차) 확정합니다. (내 판단)

### 4.5 작업대 막힘 `ArmStationBlocked(station_id, reason)`

- `reason`이 있으면 그 작업대를 막힌 집합에 넣고, 비어 있으면 뺍니다.
- WORK가 막혀 있으면 열린 호출 맞추기(`_match_calls`)는 대기 주문 수와 상관없이 열린 호출을 0으로 맞춥니다(열린 것은 `CancelDispatchCmd`). 다시 열리면 지금 규칙대로 맞춥니다.
- 이미 묶인 Pode는 그대로 기다립니다. 담기 목표는 ArmJobs가 막힌 동안 `ArmDeclined`로 돌려줍니다.
- 막힌 동안 코어는 담기를 묻지 않고(`step`이 건너뜀), `ArmStationBlocked` 풀림 뒤 다음 `step`에서 다시 묻습니다. 묻기 직전에 막혀 거절된 담기도 같습니다.
- 막힘을 바꿀 때 `EventOut`은 내지 않습니다(ArmJobs가 이미 냄).

### 4.6 주문판 (`on_order_board`, 2단계의 남은 부분)

- 최종 보고 집합 · 보류 목록은 새 주문판에 있는 id만 남깁니다.
- 대기 주문 = 지금 조건 + 최종 보고도 보류도 아닌 주문.
- `held` 로봇의 주문이 주문판에서 빠지면 로봇의 주문만 비웁니다. 로봇은 붙잡힌 채(2차 치움으로 풀림).

### 4.7 끝 상태 보고 (7단계)

- 교환에서 찬 바구니를 뺄 때: `shipped += 1`, `ReportOrderCmd(SHIPPED 또는 PARTIAL, placed, missing, "")`. missing이 있으면 `PARTIAL`.
- 최종 보고(`SHIPPED` · `PARTIAL` · `CANCELED`)는 주문 id를 최종 보고 집합에 넣고, 보류 목록에서 빼고, 로봇의 `order` · `held`를 비웁니다. 교환 명령에는 `last_order_id`를 씁니다.
- `ReportOrderCmd`는 TT-27 대기열이 먼저 `ReportTrip`(철자 `CANCELLED`, 사유 `NOTHING_TO_FILL`, HELD 사유는 `hold_reason`)으로 브리지에 보내고, 받으면 `ReportOrder`로 central에 알립니다. 주문 코어는 이 순서를 몰라도 됩니다.

### 4.8 서비스 정지 (`run_mode = PAUSE`)

근거: 회의 정리 2026-11-27 8번("상품을 담는 주문건까지 완료"), 팔 관제 계약 정리 2판 3.5, `manager.py:347`(주문 코어에 `service_paused`가 있으면 정지 중에도 `step`을 부름, PR #21).

- 바깥 접점에 읽기 속성 하나를 더합니다: `service_paused` = 마지막 `on_order_board`의 `run_mode`가 `PAUSE`인지. (`pseudocode.md` 1장 표에 더함)
- 정지 중에 하는 것: 이미 묶인 주문(= Pode가 작업대에 닿아 묶인 운반 건)은 채우기 → 출고 이동 → 교환 → 끝 상태 보고까지 그대로 진행합니다. OUTBOUND 재시도도 합니다.
- 정지 중에 막는 것
  - 열린 WORK 호출은 0으로 맞춥니다(열린 것은 `CancelDispatchCmd`). 작업대로 오던 빈 Pode는 관제 코어가 대기 자리로 보냅니다(`manager.py` 주석).
  - 그 사이 성공한 WORK 호출 결과가 와도 묶지 않고 `ReleaseCmd(robot)`.
  - 바구니가 없는(`NONE`) 빈 Pode가 빈 바구니를 받으러 가는 것도 하지 않습니다(새 일로 봄). (내 판단)
- `RUN`으로 돌아오면 열린 호출을 다시 맞춥니다. `STOP`은 지금처럼 `step`이 불리지 않습니다(이 작업에서 바꾸지 않음).
- `pseudocode.md` 4장 "주문 코어는 run_mode를 저장만 하고 가리지 않는다"를 이 규칙으로 고칩니다.
- 지금 central은 늘 `RUN`만 보내므로 1차는 단위 시험으로만 확인합니다.

## 5. 남는 위험 (1차)

- Pode 격리 · 치움이 1차에 없으므로, 주행 고장이 나면 그 주문은 그 로봇에 묶인 채 남습니다(지금과 같음).
- `FILLING_PAUSE` · `HELD` 건은 2차 전까지 풀 수 없습니다. 시험 때는 central에서 주문을 빼거나 fleet을 다시 켜서 정리합니다.

## 6. 시험

**단위 (pytest, ROS 없음)**: `pc/fleet_core/test/test_orders_fill_ship.py`(새 파일). 지금 `test_orders_dispatch.py`처럼 코어만 세우거나 `orders_and_arm.py`(주문 코어 + ArmJobs)를 씁니다. 줄마다 시험 하나:
- 한 바퀴: 묶음 → `FILLING` 보고 → `ArmFillCmd` → 성공 결과 → 마무리 → OUTBOUND 호출 + `TRANSPORTING` → 성공 → `ArmSwapCmd(take_full, put_empty)` → `SHIPPED` → 빈 바구니 놓음 → `ReleaseCmd`
- 일부만 담음 → `PARTIAL`, 하나도 못 담음 → `CANCELED` + `ReleaseCmd`
- 진행 보고의 `placed`(앞 목표 + 지금 진행)와 `missing`(실제 못 담은 것만)
- `ArmFillPaused` → `FILLING_PAUSE` 보고, 그 뒤 `step`이 다시 묻지 않음
- `ArmFillPlaced` → `resume_wait` 동안 다시 묻지 않음 → `ArmFillResumed` → 남은 상품만 다시 요청 (두 번 담지 않음)
- `ArmFault` → `HELD(ARM_FAULT)`, 보류 목록, `step`이 아무것도 하지 않음, 대기 주문에서 빠짐
- OUTBOUND 실패 → 2.0 s 뒤 재시도, 한도 넘으면 `RobotFaultCmd`
- `ArmStationBlocked` → 열린 WORK 호출 취소, 풀리면 다시 엶
- 바구니 `NONE`인 빈 Pode → 빈 바구니 받으러 OUTBOUND
- `ArmDeclined` → 다음 `step`에서 다시 물음
- 작업대에 닿았는데 대기 주문이 없음 → `ReleaseCmd`, `held` 로봇은 놓아 주지 않음
- 서비스 정지: 묶인 주문은 끝 상태 보고까지 진행, 열린 WORK 호출 취소, 정지 중 닿은 Pode는 묶지 않고 놓아 줌, `RUN`이면 다시 엶, `service_paused` 값

**통합 (사람이 확인)**: `pseudocode.md` 6장의 시뮬레이션에 브리지를 더합니다.
- 지금 시뮬레이션에는 브리지 대역이 없어, TT-27 뒤로는 fleet이 `Hello`를 받지 못해 끝 상태를 central에 알리지 않습니다.
- 시험 DB + `service_bridge.main` + `pc/central` + `pc.launch.py use_fake:=true fake_central:=false scenario:=real_two_orders map_file:=v7 robots:="pode1 pode2" fake_robots:="pode1 pode2"`로 띄우고, 운반 건 4개가 DB에서 `SHIPPED`가 되는지 봅니다(`ws_watch --admin` 또는 DB 행).

## 7. 구현자에게 남기는 것

- `_RobotRecord` · `_Bound`의 정확한 모양과 이름(자료형, 칸 이름은 위 표를 따름)
- 출력 순서는 위 표에 적은 순서를 지킴(예: 마무리는 `ArmResetCmd` 다음 보고)
- 주석은 구체적으로: 분기마다 `pseudocode.md`의 단계 번호나 팔 관제 합의 번호를 근거로 적음
