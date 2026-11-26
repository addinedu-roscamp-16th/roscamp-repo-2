# fleet 보고 연결 (TT-27) 설계

- 작성 2026-11-26 · 상태: 승인 (2026-11-26)
- Jira: TT-27 (BRG-06 fleet 쪽) · 관련 기능: CEN-03 · CEN-04 · BRG-06
- 브랜치: `feat/fleet-report-trip` (`dev` a9b4737에서)
- 근거 문서: [주문 흐름 1차 인터페이스](../../설계/주문_흐름_1차_인터페이스.md) 4.3의 2 · 4.4 · 6장 F-06 · F-08 · F-10, [통신 규칙](../../설계/통신_규칙.md) IR-22 ~ IR-25 · IR-40 · IR-41, `pc/fleet_core/fleet_core/orders/pseudocode.md` 1장 · 3장 7

## 1. 왜 하는가

- 11-25 주문 → Pode 배정 연동 테스트에서 T-10("fleet → 브리지 `ReportTrip(ASSIGNED)`, DB가 배정됨으로 바뀜")이 **보류**였습니다.
- 원인: 주문 코어는 `ReportTripCmd(ASSIGNED)`를 내지만 `fleet_manager_node`의 `_dispatch`에 이 명령을 처리하는 곳이 없어 버려집니다. 노드에는 `/bridge/hello` · `/bridge/report_trip` 클라이언트도 없습니다.
- 브리지의 `Hello` · `ReportTrip` 서버와 DB 반영(`apply_report`)은 #14에서 구현 · 시험됐습니다. 이 작업은 fleet 쪽 호출 코드와, 그 과정에서 드러난 central의 `HELD` 처리 어긋남을 고칩니다.

## 2. 목표 · 범위 밖 · 실패로 볼 것

**목표**
1. `fleet_manager_node`가 켜지면 `Hello`로 epoch를 받고, 그 뒤 `ReportTripCmd`와 `ReportOrderCmd`를 `ReportTrip`으로 브리지에 보낸다.
2. 끝 상태와 보류(`SHIPPED` · `PARTIAL` · `CANCELED` · `HELD`)는 `ReportTrip`이 `accepted=true`를 받은 뒤에 `ReportOrder`로 central에 보낸다.
3. central은 `HELD`를 받아도 주문판에서 빼지 않고 "관리자 조치 대기"로 표시한다.
4. 세 서비스의 다시 보내기를 IR-40 `Report*` 줄(3초 한도, 1 → 2 → 4 → 8 → 10초 간격, 포기하지 않음)로 맞춘다.
5. T-10을 다시 시험해 성공으로 바꾼다.

**범위 밖 (이번 브랜치에서 하지 않음)**

| 항목 | 까닭 · 담당 |
|---|---|
| 주문 코어가 `FILLING` · `TRANSPORTING` · 끝 상태 · `HELD`를 내는 코드 | 관제 (`orchestrator.py`의 `on_arm_report` · `step` · `on_force_ship`이 아직 빈 함수). 노드는 그 명령이 오면 바로 보낼 수 있게만 만듦 |
| 관리자 화면의 보류 알림 · 조치 버튼, 강제 출고 | 다음 주 (SCR-12 · SCR-22, Q-04 열림) |
| "담은 것 0개면 접수됨으로 되돌림" | 방법 미정 (Q-04). 짐작해 넣지 않음 |
| 재시작 뒤 이어서 하기(`ListOpenTrips`) | 1차 인터페이스 6.4에서 줄임 |
| central "조치 대기"를 밖으로 알리기 | 주문판 `Order` 메시지에 상태 칸이 없어 인터페이스 변경이 필요함. 이번에는 central 안 상태와 로그만 |
| 가짜 fleet | #14에서 있음 (`service_bridge/tools/fake_fleet.py`) |

**실패로 볼 것 (동작해도 안 됨)**
- 같은 보고에 epoch가 두 번 늘어 뒤 보고가 옛 epoch로 버려짐 (F-06).
- `report_n`이 운반 건마다 따로 세어지거나, 같은 운반 건의 보고가 순서를 바꿔 도착해 DB가 뒤로 감.
- `ReportTrip`이 닿기 전에 central이 주문판에서 건을 빼 fleet이 그 건을 잊음.
- `HELD` 건이 주문판에서 빠져 fleet이 보류를 잊거나, 같은 id로 다시 들어와 새로 배정됨.
- 같은 보고가 동시에 여러 개 나가 응답 대기 상한(IR-41)을 채움.

## 3. 정해진 것 (사용자 결정 · 1차 인터페이스)

| 항목 | 내용 | 출처 |
|---|---|---|
| 끝 상태 보내는 순서 | `ReportTrip` 먼저, `accepted=true` 뒤 `ReportOrder`. 대상은 `SHIPPED` · `PARTIAL` · `CANCELED` · **`HELD`** | 1차 인터페이스 F-10 + 11-26 결정(`HELD` 추가) |
| central의 `HELD` | 주문판에 남기고 "관리자 조치 대기"로 표시. 주문판에서 빼는 끝 상태는 `SHIPPED` · `PARTIAL` · `CANCELED` 셋. 보류 건에 끝 상태가 오면 그때 뺌 | 11-26 결정, 의사코드 1장("HELD는 끝이 아니다") |
| 브리지의 `HELD` | DB `trip`을 `HELD`로 씀 (지금 전이 표에 있음). 관리자 조치는 다음 주 | 11-26 결정 |
| 다시 보내기 | 1 → 2 → 4 → 8 → 10초, 10초에서 멈춤, ±20% 무작위 지연(IR-41), 응답 한도 3초, 포기하지 않음. `Hello` · `ReportOrder`에는 `TODO(Q-09)` | 11-26 결정, IR-40, 1차 인터페이스 6장 |
| `Hello` | 켜지면 맨 먼저. 다시 보낼 때 같은 `boot_id`. 마지막 요청의 응답만 씀. epoch를 받기 전에는 `Report*`를 보내지 않음 | F-06 |
| `ReportTrip` 칸 | `report_epoch` = `Hello` 값, `report_n` = 노드 하나의 카운터(보고마다 +1, 운반 건과 무관), `stamp` = 보고를 만든 시각, 누적 `placed` · `missing`. `CANCELED` → `CANCELLED` | 1차 인터페이스 4.4 ③ |
| 순서 · 바꿔 끼우기 | 같은 운반 건은 하나씩 순서대로. 기다리는 보고는 최신 상태로 바꿔 끼우고 새 `n` | IR-23, 4.4 ③ |
| `accepted=false` | `ReportTrip`: DB 쓰기 실패라 같은 간격으로 다시 보냄. `ReportOrder`: central이 받을 수 없는 상태라 다시 보내지 않고 `ORDER_REPORT_REFUSED` (지금과 같음) | F-08 · 11-26 논의 |

## 4. 방법 비교

| 안 | 내용 | 장단점 |
|---|---|---|
| **A. 보고 대기열을 ROS 없는 모듈로 넓힘 (선택)** | `goal_tracker.py`의 `ReportOutbox`를 대신할 순수 파이썬 대기열에 epoch 대기, 번호, 운반 건별 순서, 바꿔 끼우기, 늘어나는 간격, `ReportTrip` → `ReportOrder` 잇기를 모음. 노드는 `call_async`와 응답 전달만 | 시계를 넣어 pytest로 모든 경우를 시험할 수 있음. 지금 `ReportOutbox` · `test_report_flush.py`와 같은 결. 노드 코드는 얇게 남음 |
| B. 노드 안에 바로 구현 | `fleet_manager_node.py`에 상태와 콜백을 추가 | 파일 하나로 끝나지만 시험에 rclpy가 필요하고, 이미 큰 노드가 더 커짐 |
| C. 주문 코어(`fleet_core`)에서 번호 · 순서 처리 | 코어 출력에 번호를 붙임 | IR-22("번호는 노드 한 곳") · 1차 인터페이스 4.4 ①("코어에는 ROS와 재시작 개념이 없음")과 어긋남. 관제 접점(의사코드)도 바뀜 |

A를 고릅니다 (11-26 논의에서 "`ReportOutbox`를 넓혀 셋이 함께 씀"으로 제안).

## 5. 설계

### 5.1 fleet 노드 (`pc/fleet_manager/`)

**새 모듈 `report_queue.py` (ROS 없음)**

- 들어오는 것: `ReportTripCmd`, `ReportOrderCmd`, 현재 시각, 각 요청의 응답(성공 · `accepted` · 실패 · 한도 넘김).
- 내보내는 것: "지금 보낼 요청" 목록 — `("hello", boot_id)`, `("trip", trip_id, 요청 칸)`, `("order", trip_id, 요청 칸)`.
- 상태
  - `epoch`: 없음 → `Hello` 응답 뒤 값. 없음이면 `trip` · `order`를 내보내지 않음.
  - `report_n`: 노드 하나의 카운터. 요청을 **만들 때**(바꿔 끼울 때 포함) +1.
  - 운반 건마다: 지금 보내는 보고 하나, 기다리는 최신 보고 하나, 이어서 보낼 `ReportOrder` 하나(있으면).
  - 요청마다: 보낸 시각, 다음 간격 단계(1, 2, 4, 8, 10), 지연 배수.
- 규칙
  - `ReportTripCmd` → 그 운반 건의 `ReportTrip` 보고로 넣음.
  - `ReportOrderCmd` → `ReportTrip` 보고로 넣고(`CANCELED` → `CANCELLED`, `hold_reason` → `reason`), 그 보고가 `accepted=true`를 받으면 같은 칸의 `ReportOrder`를 줄에 올림.
  - 운반 건에 보내는 중인 보고가 있으면 새 보고는 "기다리는 최신 보고"로 두고, 이미 기다리는 것이 있으면 바꿔 끼움. 보내는 중인 것이 끝나면 기다리는 것을 새 `n`으로 보냄.
  - 다음 간격은 응답 한도(3초)가 지나거나 실패 응답을 받은 **뒤부터** 셈. 요청 하나당 답을 기다리는 것은 하나뿐.
  - 성공하면 그 요청의 간격 단계를 처음으로. 바꿔 끼운 보고는 앞 보고의 단계를 이어 씀.
  - 서비스가 준비되지 않았으면(`service_is_ready()` 거짓) 보내지 않고 기다렸다가 준비되면 바로 보냄.
  - 답을 기다리는 요청 전체 수에 상한(`report_max_in_flight`, IR-41).
  - `Hello`는 응답 한도를 넘긴 요청의 응답을 버리고(`remove_pending_request`), 같은 `boot_id`로 다시 보냄.
- `ReportOutbox`(`goal_tracker.py`)는 이 모듈로 옮기고 지움. `test_report_flush.py`는 새 규칙에 맞춰 고침.

**`fleet_manager_node.py`**
- `Hello` · `ReportTrip` 클라이언트를 contract 이름으로 만듦(IR-01).
- `boot_id`는 켜질 때 UUID 하나.
- `_dispatch`에 `case ReportTripCmd()`를 더하고, `ReportOrderCmd`와 함께 `report_queue`에 넣음.
- `_on_tick`의 `_flush_reports`가 `report_queue`가 내보낸 요청을 부르고, 응답을 `_post`로 코어 콜백 그룹에서 돌려줌(지금 `ReportOrder`와 같은 방식).
- epoch를 받기 전에도 배정은 계속함. 보고만 쌓아 둠 (**구현자 판단**: 문서는 "보고를 보내지 않음"만 정함).

**`adapters.py`**: `ReportTrip.Request` · `Hello.Request` 만들기와 응답 읽기.

**설정 (`pc/fleet_core` — 관제 패키지)**
- `fleet_params.yaml` · `params.py`: `report_retry_s`는 이름을 그대로 두고 뜻을 "처음 간격"으로 바꿔 값 2.0 → 1.0. `report_retry_max_s: float = 10.0`(기본값 있음)을 더함. 이름을 바꾸지 않는 까닭은 필수 칸이라 이름을 바꾸면 이전 설정 · 기록 파일을 읽지 못하기 때문입니다 (계획 작성 때 정함). 응답 한도 `REPORT_REPLY_LIMIT_S = 3.0`은 그대로.
- `test_params_validate.py`에 `report_retry_max_s >= report_retry_s` 검사.

### 5.2 central (`pc/central/`)

- `board.py`
  - `FINAL_STATUSES` = `SHIPPED` · `PARTIAL` · `CANCELED` (주문판에서 뺌).
  - `HELD`를 받으면 그 건을 주문판에 두고 "관리자 조치 대기"(`held`)로 표시, `True`를 돌려줌. 이미 표시된 건이면 그대로 `True`.
  - 표시된 건에 끝 상태가 오면 표시를 지우고 뺌.
  - 모르는 건 · 이미 뺀 건의 `HELD`는 `True`, 경고 로그 (지금 "모르는 order_id" 경고와 같음).
- `central_node.py`: `HELD`를 받으면 INFO 로그 "관리자 조치 대기 `order_id` (`hold_reason`)". 주문판 발행은 바뀌지 않음.
- `test_board.py` · `test_central_node.py` 고침.
- 알려진 한계: `ROBOT_FAULT` 보류는 fleet이 강제 출고도 거절하므로(의사코드 5장) fleet에서 끝 상태가 다시 오지 않습니다. 이 건은 관리자 조치(다음 주)가 생길 때까지 주문판에 "조치 대기"로 남습니다. 주문판에 남아 있는 동안 fleet은 다시 배정하지 않습니다.

### 5.3 문서

- 1차 인터페이스
  - 4.3 표 2번 줄 "`ReportOrder`를 `SHIPPED` 보고 뒤에" → 끝 상태와 `HELD` 보고 뒤에.
  - 4.4 ③ "`SHIPPED` · `PARTIAL` · `CANCELLED`가 `accepted=true`를 받은 뒤" → `HELD` 추가.
  - F-10 줄(4.3 표 · 6.2 표): central은 `HELD`를 주문판에 남기고 조치 대기로 표시.
- 기능 목록 CEN-03 · CEN-04 비고: 시트에서 고침 (사용자 요청 시).

## 6. 시험

| 무엇 | 어떻게 |
|---|---|
| `report_queue` | pytest, 시계 주입. epoch 전 대기, `n` 증가, 운반 건 순서, 바꿔 끼우기, 간격 1 → 2 → 4 → 8 → 10, 응답 한도 뒤부터 셈, `ReportTrip` 성공 뒤에만 `ReportOrder`, `HELD` 포함, `CANCELED` 철자, `ReportOrder` 거절 시 다시 보내지 않음, `Hello` 같은 `boot_id` |
| 노드 연결 | `test_report_flush.py` 등 기존 노드 시험 방식으로 `ReportTripCmd` · `ReportOrderCmd`가 클라이언트까지 가는지 |
| central | `HELD` 남김 · 표시, 보류 뒤 끝 상태로 뺌, 같은 보고 반복 |
| 전체 | `python3 -m pytest pc/fleet_core/test pc/fleet_manager/test -q`, `colcon test --packages-select central` |
| T-10 재시험 | 백엔드 PR(`feat/screen-api-links`, 웹 뿌리 마운트 N-07 · 실제 상품 코드 포함) 병합 뒤 이 브랜치를 `dev`에 맞춤. 실행: `setup_db` → 브리지(`bridge -p mode:=NORMAL`, 웹 포함) → `central` → `pc.launch.py use_fake:=true fake_central:=false ...`. **주문은 11-25의 `submit_trip` 대신 고객 화면**(`http://localhost:8000/customer/?account=<id>`)에서 넣음 (11-26 사용자 결정). 확인: 고객 화면 접수됨 → 준비 중, DB `trip`이 `ASSIGNED`이고 `report_epoch` · `report_n`이 채워짐(완료 기준 2), `status_history`에 RECEIVED → ASSIGNED(SYSTEM) 줄, `ws_event`의 `trip_changed` · `order_changed`, 관리자 화면. (브리지가 답하는 `result=APPLIED`는 로그에 남지 않으므로 반영 근거는 DB 행으로 봄) 결과를 `reports/스프린트_3회차/주문_배정_연동_테스트_메모.md` T-10 줄에 적고, T-01 확인 방법이 화면 → 웹 API → DB → 브리지 → central로 바뀐 것을 함께 적음 |

## 7. 구현자에게 맡기는 것

- `report_queue`의 클래스 · 함수 이름과 내부 자료 구조.
- 로그 문구.
- 노드 시험에서 rclpy를 쓰는 범위 (기존 `test_report_flush.py` 방식을 따름).

## 8. 검토 · 병합

- `pc/fleet_manager` · `pc/fleet_core`는 관제 패키지이므로 PR에 관제 담당 코드 검토를 받습니다 (TT-27 "관제 코드 검토").
- `interfaces/` · `contract.yaml`은 바꾸지 않습니다.
