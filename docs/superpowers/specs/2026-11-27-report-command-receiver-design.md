# `ReportCommand` 받는 쪽 (브리지 서버 · DB 반영 · 화면 사건) 설계

- 날짜: 2026-11-27 · 사용자 승인: 2026-11-27
- 브랜치: `feat/TT-27-fleet-dev-merge-2026-11-27` (TT-27 위에 `dev`를 병합한 브랜치)
- 함께 하는 작은 변경: 운반 건 취소 보고의 사유 `NOTHING_TO_FILL` (5장)

## 1. 왜 하는가

- 관리 명령의 최종 결과는 fleet이 `/bridge/report_command`(`ReportCommand`)로 브리지에 보고합니다(IR-60, 명세 5장 "관리 명령" 흐름).
- contract 0.9에 이름과 타입은 있지만 받는 서버가 없습니다. 그래서 fleet은 결과를 메모리에만 쌓아 둡니다(`fleet_manager_node.report_command`, `TODO(TT-27)`).
- 받는 쪽을 먼저 만들어 두면, fleet 담당은 보내는 쪽만 붙이면 됩니다. 보내는 쪽은 fleet 진행 상황을 물은 뒤에 합니다.

## 2. 목표 · 범위 밖

**목표**
- 브리지가 `ReportCommand`를 받아 `admin_action.final_result`에 한 번만 확정해 씁니다(IR-62, 주문 DB 설계 4.9).
- 받은 결과를 관리자 화면 사건 `command_result`로 보냅니다.
- 반영할 수 없는 결과는 `LATE_COMMAND_RESULT` 알림으로 남깁니다.

**범위 밖**
- 웹에서 관리 명령을 보내는 쪽(`POST /api/admin/commands/{command}` → `admin_action` 행 만들기 → fleet 서비스 부르기)
- fleet에서 `ReportCommand`를 보내는 쪽
- `request_id`별 결과 기억(SB-17)
- central은 이 흐름에 없습니다(서버가 브리지).

**하지 말 것**
- 끝 상태를 두 번 바꾸는 것
- DB 오류가 아닌데 `accepted=false`로 답해 fleet이 같은 보고를 끝없이 다시 보내게 하는 것

## 3. 방법

| 안 | 내용 | 판단 |
|---|---|---|
| **A (고름)** | `ReportTrip`과 같은 모양: `core`의 DB 함수(트랜잭션 하나) + 노드 서버 + `events.route` | 이미 있는 틀(`run_tx_guarded`, `_late` 알림, 서버 콜백 예외 처리)을 그대로 씁니다 |
| B | 판정을 DB 함수(plpgsql)에 둠 | 이 저장소는 판단 코드를 `core`의 파이썬에 두고 pytest로 시험하므로 맞지 않습니다 |

## 4. 동작

### 4.1 서버

- 브리지 노드가 DB에 연결된 뒤 `Hello` · `ReportTrip`과 함께 `/bridge/report_command`를 엽니다. 이름은 `contract.name("report_command")`로 읽습니다.
- 서버마다 MutuallyExclusive 콜백 그룹을 씁니다. 다른 서비스를 부르지 않고(IR-31), 커밋한 뒤 답합니다(IR-12).
- 예외는 모두 잡아 `accepted=false`로 답합니다.

### 4.2 DB 반영 (`core/commands.py`, 트랜잭션 하나)

`admin_action`을 `request_id`로 `FOR UPDATE` 잠그고 판정합니다.

| 받은 `result` | 지금 `final_result` | 처리 | 응답 `accepted` |
|---|---|---|---|
| 아무 값 | `NULL` | 씀 | true |
| 끝 상태(`ACCEPTED` · `REJECTED` · `TIMEOUT`) | `UNCONFIRMED` | 씀 (결과 모름 → 끝 상태) | true |
| `UNCONFIRMED` | `UNCONFIRMED` | 그대로 (다시 보낸 것) | true |
| `UNCONFIRMED` | 끝 상태 | 그대로 (늦게 온 중간 보고) | true |
| 같은 끝 상태 | 같은 끝 상태 | 그대로 (다시 보낸 것) | true |
| 다른 끝 상태 | 끝 상태 | 그대로 + `LATE_COMMAND_RESULT` 알림 (`late_reason: FINAL_ALREADY`) | true |
| 아무 값 | 행 없음 | 쓰지 않음 + 알림 (`late_reason: NOT_FOUND`) | true |
| 4개 밖의 값 | (상관없음) | 쓰지 않음 + 알림 (`late_reason: INVALID_RESULT`) | true |
| (DB 오류) | | | false, `DB_ERROR` |

- 쓸 때는 `final_result = result`, `result_detail = reason`으로 씁니다. 이 칸의 주석이 `ROBOT_REJECTED` · `ROBOT_TIMEOUT`을 예로 듭니다. `reason`이 비어 있으면 `result_detail`은 그대로 둡니다.
- 알림은 `alert`에 `level=WARN`, `code=LATE_COMMAND_RESULT`, `source=fleet`, `event_id = LATECMD:{request_id}:{result}`로 넣고 `ON CONFLICT (event_id) DO NOTHING`입니다. 같은 보고가 다시 와도 알림은 하나입니다.
  - `robot_id`: `admin_action.robot_id`가 있으면 그 값, 없으면 보고의 `target`
  - `detail`: 보고 전체와 `late_reason`
- `TIMEOUT`을 받았다고 브리지가 따로 알림을 내지는 않습니다. 30초 시간 초과 알림은 fleet 몫입니다(W-31). (구현자 판단 아님, 설계 결정)

### 4.3 화면 사건

- `final_result`를 쓴 경우에만 같은 트랜잭션에서 `ws_event`에 `command_result` 한 줄을 넣습니다. NOTIFY는 `tg_ws_event_notify` 트리거가 합니다.
- `data`: `{request_id, command, target, result, reason, detail}` (보고 값 그대로)
- `events.route`는 `command_result`를 관리자에게만 보냅니다.
- 알림을 넣으면 `alert_raised` 사건은 기존 트리거가 냅니다. 이 작업에서 따로 하지 않습니다.

### 4.4 응답의 `result` 칸

`ReportCommand.Response`에는 `accepted`만 있습니다. 판정 이름(`APPLIED` · `DUPLICATE` · `LATE`)은 core 반환값과 로그에만 둡니다.

## 5. 함께 하는 변경: 취소 사유 `NOTHING_TO_FILL`

- TT-27 보고 대기열(`fleet_manager/report_queue.py` `_payload`)은 `ReportOrderCmd`의 `CANCELED`를 `ReportTrip` `CANCELLED`로 바꾸면서 사유를 `hold_reason`(빈 값)으로 보냅니다. 그래서 DB 이력의 취소 사유가 비어 있습니다.
- `dev`의 `adapters.trip_status(status, hold_reason)`를 불러 상태와 사유를 함께 정합니다. 취소의 사유는 `NOTHING_TO_FILL`이 되고, HELD는 `hold_reason`을 그대로 씁니다.
- `report_queue`는 지금 ROS 메시지를 몰라도 시험할 수 있습니다. `adapters`가 `fleet_interfaces`를 부르므로 이 import가 시험에 문제가 없는지 확인하고, 문제가 되면 같은 규칙을 `report_queue` 안에 상수로 둡니다(구현자 판단).

## 6. 문서

- 명세 3.1 WS `data` 표에 `command_result` 줄을 더합니다.
- 설계 검토 사항 Q-07의 "남은 것"에서 `command_result`를 뺍니다.

## 7. 시험

- 시험 DB: `docker compose -f design/db/compose.yaml up -d` (포트 55432, `IFP_PG_DSN`).
- `core/commands.py`: 4.2 표의 줄마다 시험 하나. 시험용 `admin_action` 행은 시험 코드가 넣습니다. `command_result` 사건이 쓴 경우에만 생기는지, 알림이 한 번만 생기는지 봅니다.
- `events.route`: `command_result`가 관리자에게만 가는지 봅니다.
- 노드: 기존 `ReportTrip` 노드 시험과 같은 방식으로 서버가 열리고 답하는지 봅니다.
- fleet: `report_queue` 철자 시험에 취소 사유 `NOTHING_TO_FILL`을 넣습니다.
