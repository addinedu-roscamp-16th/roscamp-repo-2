# fleet · central · 인터페이스 작업 목록

- 작성: 2026-11-20. [인터페이스 명세](인터페이스_명세.md) v0.2와 [통신 규칙](통신_규칙.md)을 관제 코드에 반영하려면 해야 할 일입니다.
- **기준 코드: `origin/main` (`1d44cfa`, contract 0.6).** `origin/fleet-v1`(`8ddeada`, contract 0.5)은 main보다 뒤처져 있으므로 작업은 main(또는 main에서 딴 브랜치)에서 합니다.
- 지금 main 상태:
  - `fleet_interfaces`: 0.6 정의 (`SubmitOrder` · `SetRunMode`는 파일만 있고 contract에 없음, `ArmState`는 메시지만 있고 토픽 없음)
  - `fleet_core/orders/orchestrator.py`: 입력 · 출력 자리만 있는 뼈대 (`pseudocode.md`가 목표 동작), `on_force_ship`은 `NOT_IMPLEMENTED`
  - `fleet_manager_node.py`: 0.6 토픽 · 액션 · `ReportOrder` 클라이언트, `confirm_clear` · `reinstate` 서버, `force_ship`은 contract에 있을 때만 엶
  - `pc/central`: README와 빈 패키지뿐
- 번호 `W-nn`은 작업 순서가 아니라 묶음 번호입니다. "미정"은 [설계 검토 사항](설계_검토_사항.md)의 `Q-nn`이 정해져야 끝낼 수 있습니다.

## 1. 인터페이스 패키지 (`interfaces/fleet_interfaces`)

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-01 | `contract.yaml` 버전을 올리고(0.7 등), 자리표시자 `arm: [arm3]` → `[arm_left, arm_right, arm_ship]` | IR-03, 명세 1.2 |
| W-02 | contract에 구역 머리말(로봇 ↔ fleet · 브리지 ↔ central · 브리지 ↔ fleet · 상태판 · 알림)과 사유 코드 표를 더함 | IR-02 · IR-05, 명세 2.2 |
| W-03 | 새 토픽: `/{arm}/state`(ArmState, STATE), `/system/mode`(SystemMode, LATCHED), `/bridge/close_state`(CloseState, LATCHED) | 명세 3.2 · 3.5 |
| W-04 | 새 서비스 이름 (구간 B): `/central/submit_trip` · `cancel_order` · `set_run_mode` · `set_assign_hold`, `/bridge/get_close_state` | 명세 3.2 |
| W-05 | 새 서비스 이름 (구간 C 보고 · 문의): `/bridge/hello` · `list_open_trips` · `report_trip` · `report_command` · `report_event` · `check_stock` | 명세 3.3 |
| W-06 | 새 서비스 이름 (구간 C 관리 명령): `/fleet/set_lock` · `fault_resolved` · `resume_arm` · `charge_end` · `force_input` · `resume_fill` · `force_ship` · `force_partial` · `return_to_shelf` · `resume_held` · `requeue_trip` | 명세 3.3 |
| W-07 | 새 서비스 이름 (구간 E): `/{robot}/set_lock` · `set_mode` · `force`, `/{arm}/fault_resolved`, `/{pode}/charge_end` | 명세 3.5 |
| W-08 | 새 정의 파일: msg `OpenTrip` · `SystemMode` · `CloseState` / srv `SubmitTrip` · `CancelOrder` · `SetAssignHold` · `GetCloseState` · `Hello` · `ListOpenTrips` · `ReportTrip` · `ReportCommand` · `ReportEvent` · `CheckStock` · `TripCommand` · `RobotCommand` · `SetLock` · `ForceInput` · `SetMode` | 명세 4장 |
| W-09 | 바뀌는 정의: `OrderBoard`(+`assign_hold` · `close_id`), `RobotState`(+`boot_id` · `lock` · `mode` · `forced` · `detail` · `last_request_id`), `ArmState`(상태 값 · 칸 다시 정의), `RobotStatus`(+`kind` · `lock` · `mode` · `forced` · `boot_id`), `FillBasket`(사유 값 · `placed_confirmed` · `uncertain` · 멈춤 phase), `ConfirmClear` · `Reinstate`(+`request_id` · `detail`) | 명세 4.1 · 4.4 ~ 4.6 |
| W-10 | `SubmitOrder.srv`는 contract에서 쓰지 않음 표시 (`SubmitTrip`으로 대신). `ReportOrder`는 그대로 | 명세 3.4 |
| W-11 | `CMakeLists.txt`에 새 파일 등록, `contract_check`(`fleet_core/common/contract_check.py`)가 새 항목을 검사하는지 확인 | IR-01 |

## 2. fleet (`pc/fleet_core` · `pc/fleet_manager`)

### 2.1 보고와 복구

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-20 | 켜지면 `Hello`로 `epoch`를 받고, 받기 전에는 `Report*`를 보내지 않음. 그다음 `ListOpenTrips`로 끝나지 않은 운반 건을 받아 진행을 재개. 팔 상태의 `order_id` · `placed_total`로 진행 중이던 목표를 찾음 | IR-22 · IR-71 |
| W-21 | 운반 건 상태가 바뀔 때마다 `ReportTrip`(지금 상태 전체, `(epoch, n)`) 보냄: RECEIVED(되돌림) · ASSIGNED · FILLING · FILLING_PAUSE · TRANSPORTING · SHIPPED · PARTIAL · HELD · CANCELLED. 기다리는 보고는 최신으로 바꿔 끼움, 포기하지 않고 1 → 2 → 4초(최대 10초) 다시 보냄, 같은 운반 건은 순서대로 | IR-12 · IR-23 · IR-40 |
| W-22 | 0.6 내부 상수 `CANCELED`는 보낼 때 `CANCELLED`로 바꿈. "하나도 못 담음"은 `CANCELLED` + `NOTHING_TO_FILL` | IR-05, 명세 2.1 |
| W-23 | `ReportOrder`(fleet → central)는 끝난 운반 건을 주문판에서 빼는 용도로만 씀 (진행 상황은 싣지 않음) | 명세 3.4 |
| W-24 | 놓치면 안 되는 알림(WARN 이상)은 `ReportEvent`로도 보냄 (`event_id` UUID). `/central/events`는 기록용으로 그대로 | IR-12 |

### 2.2 관리 명령 서버

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-30 | 관리 명령 서버 13개(W-06 + `confirm_clear` · `reinstate`)를 연다. 응답은 fleet 자기 판단만 담아 곧바로(`accepted` · `reason` · `request_id` · `detail`), 같은 `request_id`는 같은 응답 | IR-20 · IR-60 |
| W-31 | 로봇까지 가는 명령은 로봇 서비스를 비동기로 부르고 2초 한도 · 1초 간격 · 총 5번. 결과는 `ReportCommand`(ACCEPTED · REJECTED). 끝내 응답이 없으면 UNCONFIRMED(`ROBOT_TIMEOUT`), 늦은 응답 · `/fleet/robots` 관측으로 한 번만 확정, 30초 넘으면 TIMEOUT + `LATE_COMMAND_RESULT` 알림 | IR-40 · IR-62 |
| W-32 | 로봇에 보내지 않는 명령(`TripCommand` 6개 · `confirm_clear` · `reinstate`)도 처리 직후 `ReportCommand` 한 번 | IR-60 |
| W-33 | `force_ship` 구현 (지금 `NOT_IMPLEMENTED`), `force_partial` · `return_to_shelf` · `resume_fill` · `resume_held` · `requeue_trip` 구현 | 명세 3.3 |
| W-34 | `set_lock` 판단: 멈춘 로봇만(`NOT_STOPPED`), 팔이 상품을 쥐고 있으면 `confirm_drop` 없이는 `HOLDING_ITEM`, Phony는 `UNKNOWN_TARGET`. 락이 풀린 로봇은 배정에서 빼고 재개 · 복귀 명령을 거절(`LOCK_RELEASED`) | IR-61, 명세 4.4 |
| W-35 | `resume_arm`(거리 정지 뒤 팔 재개)의 팔 쪽 전달 방법 — **미정 Q-05** | Q-05 |
| W-36 | `resume_held` · `requeue_trip`의 받는 조건(운반 건 상태별) — **미정 Q-04** | Q-04 |

### 2.3 주문 처리 동작

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-40 | 담기 전 `CheckStock`(3초)으로 결품 확인. 모자라면 FILLING_PAUSE(`OUT_OF_STOCK`) + 알림, 답이 없으면 담기를 시작하지 않고 기다리며 알림 | 명세 3.3 · 5장 |
| W-41 | 집기 실패 · 인식 오류: FillBasket 결과 → FILLING_PAUSE(`placed`) 보고 → `resume_fill`(필요하면 `mark_missing`을 뺀) 남은 상품으로 새 목표 | 명세 4.6 · 5장 |
| W-42 | 거리 정지 · 서비스 일시 정지: 담기 목표를 취소하지 않고 그 안에서 멈춤, 상태는 FILLING 유지 · `placed`만 보고. 신호 방식은 **미정 Q-05** | Q-05 |
| W-43 | 담기 도중 장애: 자동 출고하지 않고 HELD(`ARM_FAULT` · `ROBOT_FAULT`). `uncertain`이 비어 있지 않으면 반드시 HELD | 명세 2.1 · 4.6 |
| W-44 | 장애 + 담은 것 0개 → RECEIVED로 되돌려 다시 배정. 되돌린 운반 건은 주문판 자리(처음 접수 시각 순)를 바꾸지 않고, 새 주문처럼 다시 넣지 않음. Phody 장애 동안 배정을 막는 법 · Phody 쪽 조건은 **미정 Q-03** | 명세 2.1, Q-03 |
| W-45 | 주문판의 `assign_hold`(마감 중 배정 보류)가 켜지면 새 배정을 멈춤 (이미 시작한 나뉜 주문의 남은 운반 건은 계속) | 명세 4.1 |
| W-46 | 서비스 일시 정지는 주문판의 `run_mode`(RUN · PAUSE)로만 처리 (STOP 쓰지 않음) | IR-56, 명세 4.2 |

### 2.4 상태판 · 모드 · 로봇 연결

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-50 | `/{arm}/state`(세 팔) 구독. `/fleet/robots`에 팔 3대를 넣고 `kind` · `lock` · `mode` · `forced` · `boot_id` 채움 | 명세 3.5 · 4.5 |
| W-51 | 켜질 때 `/system/mode`를 읽고 다르면 연결 거부 · 알림 | IR-73 |
| W-52 | 로봇 상태가 1.5초 넘게 끊겼다가 다시 오거나 `boot_id`가 바뀌면 "다시 연결" → `set_mode` 다시 부름. 보고한 모드가 다르면 다시 알리고, 그래도 다르면 배정에서 빼고 `MODE_MISMATCH` 알림 | IR-34 · IR-72 · IR-73 |
| W-53 | `force_input` · `charge_end`는 시험 모드에서만 받음 (`NOT_TEST_MODE`) | IR-63 |

### 2.5 구현 공통

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-60 | 모든 서비스 호출을 `call_async` + 별도 콜백 그룹으로. 콜백 안에서 동기 호출 금지. `wait_for_service` 5초 이상 | IR-30 ~ IR-32 |
| W-61 | 다시 보낼 때 ±20% 무작위 지연, 노드별 동시 요청 상한, 한도 넘긴 요청은 `remove_pending_request` | IR-41 |
| W-62 | 이름은 contract에서 읽음 (코드에 문자열 금지) | IR-01 |

## 3. central (`pc/central`, 지금 비어 있음)

| 번호 | 할 일 | 근거 |
|---|---|---|
| W-70 | 서버: `SubmitTrip`(같은 `trip_id`는 처음 응답 그대로) · `CancelOrder` · `SetRunMode`(RUN · PAUSE) · `SetAssignHold`(반영 여부 · 진행 중 운반 건 수) | 명세 3.2 · 4.2 |
| W-71 | `/central/orders` 발행: 운반 건 목록(처음 접수 시각 순) · `run_mode` · `assign_hold` · `close_id`, LATCHED로 바뀔 때와 주기적으로 | 명세 3.4 · 4.1 |
| W-72 | `ReportOrder` 서버: 끝난 운반 건을 주문판에서 뺌 | 명세 3.4 |
| W-73 | 켜질 때 `/system/mode` 확인, `GetCloseState`로 마감 상태 받기, `/bridge/close_state` 구독 (평소 원본은 `SetAssignHold`) | 명세 4.2, IR-73 |
| W-74 | 자기 알림은 `ReportEvent`로 (Hello 없이) | IR-12 |
| W-75 | `CancelOrder`의 "모든 운반 건이 접수됨" 판단 — **미정 Q-01** (central은 배정 사실을 볼 통로가 없음) | Q-01 |
| W-76 | 재시작 때 주문판을 다시 채우는 길 — **미정 Q-02** | Q-02 |
| W-77 | README의 서비스 목록(`submit_order` 등)을 명세에 맞게 고침 | 명세 3.2 |

## 4. 다른 담당에 넘길 것 (참고)

- **서비스 브리지 · 웹:** W-05 서버들(`hello` · `list_open_trips` · `report_*` · `check_stock` · `get_close_state`), `FLEET_RESTART` 보류 쓰기, `/system/mode` · `/bridge/close_state` 발행, 관리 명령 HTTP 202 → fleet 서비스, 보고 반영 규칙(IR-24). DB는 주문 DB 설계 v1.1.
- **로봇 노드 (Pode · 팔):** W-07 서버들, 상태 메시지 새 칸(W-09), 켜질 때 모드 `UNKNOWN` · 강제 값 지움 · 락 잠김(IR-72), `request_id` 최근 32건 60초 기억(IR-21), FillBasket 결과의 `placed_confirmed` · `uncertain` · 멈춤 phase.

## 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-11-20 | 처음 작성. origin/main(`1d44cfa`) 코드와 인터페이스 명세 v0.2 · 통신 규칙 v1.0 대조 |
