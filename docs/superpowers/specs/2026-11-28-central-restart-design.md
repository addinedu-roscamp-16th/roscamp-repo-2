# central 재시작 복구 · 디스커버리 서버 연결 · 1차 미개발 정리 설계

- 작성 2026-11-28 · 상태: 초안 (사용자 검토 전)
- 진행 방식(사용자 결정 2026-11-28): SB-06 · Q-02는 **"제안"으로만** 적고, 다음 주 논의에서 뒤집혀도 되돌리기 쉽게 커밋을 나눈다(6.7절).
- 브랜치: `feat/central-restart-2026-11-28` (`feat/TT-27-fleet-dev-merge-2026-11-27`의 `ee93655`에서 나눔)
- 관련: Q-02(central 재시작 복구, 열림), SB-06(확정, 이 설계로 정정 제안), SB-13(central 주문판 복구), IR-55 · IR-70 · IR-71 · IR-83, Q-18 · Q-20, [주문 흐름 1차](../../설계/주문_흐름_1차_인터페이스.md)

## 1. 목적과 까닭

브리지와 central은 어느 쪽이 먼저 켜질지 모르고, 언제든 다시 켜질 수 있으며, 서로 비동기로 연결됩니다. 지금 central은 주문판을 메모리에만 두어(IR-55) 다시 켜지면 비고, 켜지자마자 빈 주문판을 냅니다. 그러면 두 가지가 깨집니다.

1. fleet은 빈 주문판을 받으면 주문판에서 빠진 건의 최종 보고 · 보류 기록을 지웁니다(`orchestrator.on_order_board`). 이 뒤 같은 건이 다시 실리면 두 번 운행할 수 있습니다.
2. 브리지는 central이 다시 켜진 것을 알 수 없습니다. SB-06은 "알아채면 미확정 건은 다시 보내지 않고 관리자 알림"이라 정했지만, 알아채는 방법도 관리자가 할 일도 없습니다.

운반 건마다의 진행 상태와 끝 상태는 웹 DB(`trip`)에 있고, fleet은 `ReportTrip`이 DB에 반영된 뒤에만 `ReportOrder`를 보내므로 **DB가 central보다 늘 앞섭니다**. 그래서 central이 켜질 때 DB의 끝나지 않은 건으로 주문판을 다시 세우면, 관리자가 확정할 것 없이 이어서 돌 수 있습니다(2026-11-28 논의).

## 2. 목표 · 비목표 · 반목표

**목표**
- central을 세 단계(연결 설정 → 주문판 갱신 → 주문 처리 실행 중)로 운영하고, 단계와 `boot_id`를 토픽으로 알린다.
- central이 켜질 때 브리지에서 끝나지 않은 운반 건 목록을 받아 주문판을 다시 세운 뒤에만 주문판을 발행한다.
- 브리지는 central 재시작을 알아채 안내 알림을 남기고, central이 실행 중이 아닐 때는 운반 건을 보내지 않는다(실패로 세지 않음).
- 관리자 화면 위쪽에 central 단계("연결 중 · 주문판 갱신 중 · 실행 중 · 연결 안 됨")를 보인다.
- 브리지 · central을 디스커버리 서버를 거쳐 띄우는 방법을 문서에 넣고, 서버를 거치는 재시작 시험을 만든다.
- 1차 문서를 진행 상태에 맞추고, 확인된 문서 오류 · 빠진 표시를 고친다.

**비목표**
- fleet 코드 변경. 주문판을 다 세우기 전에 내지 않으면 fleet은 지금 규칙(묶인 건 · 최종 보고 · 보류는 다시 묶지 않음)으로 충분하다.
- fleet 재시작 복구(`/bridge/list_open_trips`, IR-71)와 `FLEET_RESTART` 처리(Q-04). 그대로 둔다.
- central과 fleet이 같이 다시 켜지는 경우의 추가 처리(지금 있는 `FLEET_RESTART` 규칙을 따름).
- 알림을 WS로 보내기(Q-07 미정, `web/app.py`의 `TODO(Q-07)` 그대로).
- Phony `/arm_ship/swap_basket` 서버, Phody `set_pause` 서버(각 로봇 담당).
- central PC 주소(Q-20) · 시험 서버 포트(Q-18) 결정.

**반목표 (돌아가도 실패인 것)**
- central이 갱신을 마치기 전에 빈 주문판이나 일부만 담은 주문판을 내는 것.
- 같은 `trip_id`가 다시 세운 주문판과 `SubmitTrip`으로 두 번 실리는 것.
- central이 다시 세우는 동안 브리지가 `NOT_READY`를 실패로 세어 30초 쉼 · `DISPATCH_STALLED` 알림에 빠지는 것.
- central이 DB를 갖는 것(IR-55 위반).

## 3. 제약

- 인터페이스를 바꾸면 `contract.yaml` · 명세 · 통신 규칙을 같은 커밋에서 고친다(IR-90). contract는 **1.0 → 1.1**.
- fleet이 쓰는 인터페이스(`OrderBoard` 등)는 바꾸지 않는다. 새 정의는 브리지 · central만 쓴다. 관제는 contract 검토만 한다.
- central은 rclpy 노드, DB 없음. 브리지는 core(ROS 없음) · ros · web으로 나눈 지금 구조를 따른다.
- 시간 한도 · 다시 보내기 값은 IR-40 `Report*` 줄 임시 값(3초, 1 → 2 → 4 → … 최대 10초, ±20%)을 쓰고 `TODO(Q-09)`를 남긴다.
- 주석 · 문서 · 커밋은 한국어, 주석은 구체적으로(무엇을 왜).

## 4. 접근 (고른 것)

| 정할 것 | 안 | 고름 |
|---|---|---|
| central 단계 알림 | (A) 새 토픽 `/central/state` · (B) `OrderBoard`에 칸 추가 | **(A)**. 갱신 중(주문판을 내지 않는 동안)에도 알릴 수 있고 fleet이 바뀌지 않음. (B)는 갱신 중 알릴 수 없고 fleet을 다시 빌드해야 함 |
| 끝나지 않은 건 목록 | (A) 새 이름 `/bridge/list_board_trips` + 기존 `ListOpenTrips` 타입 · (B) 기존 `/bridge/list_open_trips`를 central도 부름 · (C) 새 서비스 타입 | **(A)**. 새 정의 파일 없이 `OpenTrip`을 쓰고, 부르는 쪽(central · fleet)이 이름으로 갈림. (B)는 fleet 재시작 복구와 섞임, (C)는 얻는 것보다 정의가 늘어남 |

사용자가 2026-11-28에 두 곳 모두 (A)를 골랐고, 이 설계 범위는 주문 담당 쪽 구현까지(사용자 선택 "(나)")입니다.

## 5. 인터페이스 (contract 1.1)

### 5.1 `msg/CentralState.msg` (새로)

```
# ---- central 운영 단계 (/central/state) ----
string STATE_CONNECTING="CONNECTING"   # 브리지를 찾는 중
string STATE_SYNCING="SYNCING"         # 끝나지 않은 운반 건을 받아 주문판을 세우는 중
string STATE_RUNNING="RUNNING"         # 주문판 발행 · SubmitTrip · ReportOrder 처리 중
string boot_id                         # central이 켜질 때마다 새로 만드는 값 (uuid4 hex 32자)
string state
builtin_interfaces/Time stamp          # 발행 시각 (보내는 쪽, IR-42)
```

- contract `topics`: `{key: central_state, name: "/central/state", type: fleet_interfaces/msg/CentralState, qos: LATCHED, not_in_fake: true}`
- 바뀔 때 바로, 그리고 1초마다 다시 발행한다(`/central/orders`와 같은 주기).

### 5.2 `/bridge/list_board_trips` (새 이름, 타입 `ListOpenTrips` 그대로)

- contract `services`: `{key: list_board_trips, name: "/bridge/list_board_trips", type: fleet_interfaces/srv/ListOpenTrips, not_in_fake: true}`
- 요청 `epoch`: central은 0을 보내고 브리지는 보지 않는다(central은 epoch를 쓰지 않음).
- 답: `accepted=true`면 `trips`에 아래 6.3의 건을 접수 시각 순으로. DB를 못 읽으면 `accepted=false`, `reason="DB_ERROR"`.
- 명세 3.3(구간 C 문의) 표에 줄을 더하고, `ListOpenTrips` 설명에 "central은 `list_board_trips`로 같은 타입을 부른다"를 적는다.

## 6. 동작

### 6.1 central 단계

```
  켜짐 ──> CONNECTING ──(목록 서비스 보임 + /system/mode 한 번 받음)──> SYNCING
                                                                         │ 목록 받음
                                          (되돌아가는 길은 다시 켜질 때뿐) v
                                                                      RUNNING
```

| 받는 것 / 하는 것 | CONNECTING | SYNCING | RUNNING |
|---|---|---|---|
| `/central/state` 발행 | 함 | 함 | 함 |
| `/central/orders` 발행 | **안 함** | **안 함** | 함 (들어가자마자 한 번 + 1초마다) |
| `SubmitTrip` | `accepted=false, NOT_READY` (기억 안 함) | 같음 | 지금처럼 |
| `ReportOrder` | `accepted=false` (fleet이 다시 보냄) | 같음 | 지금처럼 |
| `/system/mode` 구독 | 받으면 기억 | 지금처럼 | 지금처럼 |

- **CONNECTING을 끝내는 조건:** `/bridge/list_board_trips` 클라이언트가 `service_is_ready()`이고, `/system/mode`를 한 번 받았다. 1초 주기로 확인하고, 5초마다 한 번 "브리지를 찾는 중" 로그를 남긴다(디스커버리 서버가 없거나 브리지가 아직 없는 경우, IR-83).
- **SYNCING:** `list_board_trips(epoch=0)`를 `call_async`로 부른다. 3초 안에 답이 없거나 `accepted=false`면 1 → 2 → 4 → 8 → 10초(±20%) 간격으로 다시 부른다(포기하지 않음, `TODO(Q-09)`). 답을 받으면:
  1. 받은 순서대로 주문판에 넣는다(`trip_id`, 품목(`OpenTrip.items`의 kind · count), `submitted_at`).
  2. 넣은 건마다 "처음 확정 응답 = 접수"로 기억한다. 브리지가 같은 `trip_id`를 `SubmitTrip`으로 다시 보내면 새로 넣지 않고 `accepted=true`로 답한다.
  3. `status == HELD`인 건은 "관리자 조치 대기"로 표시한다.
  4. RUNNING으로 바꾸고 주문판을 바로 발행한다. 로그: `주문판 갱신 끝: N건 (HELD M건) boot_id=…`.
- **RUNNING:** 지금 central 동작 그대로. 브리지가 다시 켜지거나 디스커버리 서버가 꺼져도 단계를 바꾸지 않는다(DB가 남아 있고, 이미 찾은 상대와는 통신이 이어짐, 안내 문서 8절 C5 · C10).
- 판단(단계 전환, 목록 반영)은 ROS 없는 코드(`central/board.py` 또는 새 `central/lifecycle.py`)에 두고 `pytest`로 시험한다. 노드는 연결만 한다.

### 6.2 fleet에 보이는 것

- central이 다시 켜지는 동안 fleet은 새 주문판을 받지 않는다. 마지막으로 받은 주문판과 최종 보고 · 보류 기록을 그대로 둔다(`on_order_board`는 주문판이 올 때만 불림).
- RUNNING에서 처음 오는 주문판은 DB의 끝나지 않은 건을 접수 시각 순으로 담으므로, 재시작 전 주문판과 순서가 같다. 이미 묶인 건 · 최종 보고를 낸 건 · 보류 건은 fleet이 다시 묶지 않는다(`_waiting_orders`).
- 재시작 동안 fleet이 보낸 `ReportOrder`는 `accepted=false`를 받고 fleet이 다시 보낸다. RUNNING 뒤에는 끝 상태 건이 주문판에 없으므로 "모르는 건"으로 `accepted=true`를 받는다(지금 central 규칙).

### 6.3 브리지: 목록 서버

ROS 없는 함수 `core/board_trips.py`의 `list_board_trips(pool, deadline) -> BoardTripsOut`, 노드는 DB 확인 뒤 다른 서버와 함께 연다(`bridge_node._probe_db`).

목록에 넣는 건:
- `trip.status`가 끝 상태(`SHIPPED` · `PARTIAL` · `CANCELLED`)가 아니고,
- `trip_dispatch` 줄이 있으며, central이 **거절한 건이 아니다**(`central_acked_at IS NOT NULL AND last_result <> 'ACCEPTED'`인 줄은 뺌).
- 아직 central에 넘기지 못한 건(`central_acked_at IS NULL`)도 넣는다. 앞 central이 받았는지 모르므로 넣어 두고, 브리지가 뒤이어 `SubmitTrip`을 보내면 6.1의 2번 규칙으로 `accepted=true`가 된다.
- 정렬: `trip.created_at, trip.trip_id` (`dispatch.head_item`과 같은 순서).
- `OpenTrip` 칸은 모두 채운다(`order_id`, `trip_index`/`trip_count`, `status`, `reason`, `robot_id`, `placed`/`missing`, `submitted_at`, `report_epoch`/`report_n`). central은 이 중 `trip_id` · `items` · `submitted_at` · `status`만 쓴다.
- 읽기만 하므로 한 번의 짧은 트랜잭션(`REPEATABLE READ` 또는 하나의 SELECT)으로 읽는다.

### 6.4 브리지: central 지켜보기

- `/central/state`를 구독해 마지막 값(`boot_id`, `state`, 받은 시각)을 둔다.
- **신선함:** 마지막으로 받은 지 3초가 넘으면 "연결 안 됨"으로 본다(1초 주기 발행, 내 판단의 값 — `TODO(Q-09)`).
- **재시작 알아채기:** 브리지가 켜진 뒤 처음 본 `boot_id`는 기억만 한다. 그 뒤 `boot_id`가 바뀌면 DB `alert`에 한 줄 쓴다: `level='INFO'`, `code='CENTRAL_RESTART'`, `source='service_bridge'`, `message='central이 다시 켜져 주문판을 다시 세웁니다'`, `detail={old_boot_id, new_boot_id}`. 관리자는 확인만 한다(조치 없음). 브리지가 꺼져 있는 동안 central이 다시 켜진 경우는 알아채지 못한다 — 안내 알림이라 받아들임.
- **넘기기 멈춤:** central이 RUNNING이고 신선할 때만 `Dispatcher`가 `SubmitTrip`을 보낸다. 아니면 보내지 않고 시도 수도 올리지 않는다. RUNNING이 되면 `Dispatcher.wake()`를 부른다.
- **재시작 틈의 실패 (설계자 판단, 빌더 점검 반영):** 옛 central이 꺼진 뒤 신선함 한도(3초)가 지나기 전에, 또는 새 `/central/state`가 닿기 전에 보낸 요청은 시간 초과 · 서비스 없음 · `NOT_READY`가 될 수 있다. 이 틈 때문에 30초 쉼 · `DISPATCH_STALLED`에 빠지지 않게 두 가지를 한다.
  1. `NOT_READY`는 언제나 실패로 세지 않는다(`attempts` 그대로, `last_result`만 적고 1초 ±20% 뒤 다시). central이 "아직 못 받음"이라고 확정해 준 답이라 결과 모름이 아니기 때문이다.
  2. `boot_id`가 바뀐 것을 알아채면, 같은 트랜잭션에서 central 확정 답 전인 줄(`central_acked_at IS NULL`)의 `attempts` · `next_attempt_at` · `unknown_since`를 0 · NULL · NULL로 되돌리고 그 줄들의 열린 `DISPATCH_STALLED` 알림을 `RESOLVED`로 닫는다. 새 central은 앞 central의 일을 모르므로 앞의 실패 기록이 뜻을 잃는다.
  - RUNNING 중 시간 초과 · 서비스 없음은 지금처럼 센다.
- **화면:** `GET /api/snapshot`과 WS `work_state`의 `data`에 `central` 칸을 더한다: `{"system_mode": …, "central": "CONNECTING" | "SYNCING" | "RUNNING" | "LOST"}` (`LOST` = 3초 넘게 못 받음 또는 아직 한 번도 못 받음). 바뀔 때 `work_state`를 다시 보낸다. 관리자 화면 위쪽 모드 표시 옆에 "central: 연결 중 / 주문판 갱신 중 / 실행 중 / 연결 안 됨"을 보인다. 고객 화면에는 보이지 않는다.

### 6.5 SB-06 정정 (설계 문서 수정)

- 서비스 브리지 기술 조사 SB-06: "브리지가 central 재시작을 알아채면 미확정 건은 다시 보내지 않고 관리자 알림" → "central은 켜질 때 브리지에서 끝나지 않은 건 목록(`/bridge/list_board_trips`)을 받아 주문판을 다시 세운 뒤에만 발행한다. 브리지는 central이 실행 중일 때만 넘기고, 재시작은 안내 알림(`CENTRAL_RESTART`)으로만 남긴다"로 바꾸자는 **제안**으로 적는다. SB-06의 "확정(2026-11-24)" 표시는 그대로 두고, 그 아래에 "2026-11-28 정정 제안 · 구현은 되돌리기 쉽게 나눠 둠(다음 주 논의에서 결정)"을 단다.
- SB-13: "이번 주는 SB-06의 알려진 위험" 아래에 "2026-11-28 제안 구현(SB-06 정정 제안과 함께 결정)"을 단다.
- Q-02: 상태 "열림"은 그대로 두고, "2026-11-28 제안 구현" 메모로 재시작 구분은 `/central/state`의 `boot_id`, 다시 채우는 길은 `list_board_trips`라고 적는다. 결정은 다음 주 논의에서.
- 명세 3.2 · 3.4(central 주문판 미정 문구), IR-70 · IR-71(central 켜질 때 순서), 1차 문서 6.4의 "central 재시작 뒤 주문판 다시 세우기" 줄은 인터페이스 커밋(IR-90에 따라 contract와 같은 커밋)에서 맞춘다.
- 백엔드 검토 목록 189행(SB-06을 "다시 보냄"으로 잘못 옮김) → 새 문구로.

### 6.6 브리지 없이 central만 띄우던 사용법 (설계자 판단, 빌더 점검 반영)

- 지금 `docs/개발_규칙.md`와 `pc/service_bridge/README.md`는 `ros2 run central central` + `service_bridge.tools.submit_trip`으로 central만 손으로 보는 방법을 안내한다. 새 설계에서는 목록 서비스가 없으면 central이 CONNECTING에서 나오지 않으므로 이 사용법은 쓸 수 없다.
- "브리지 없이 바로 RUNNING" 매개변수는 두지 않는다. 운영에서 잘못 켜면 빈 주문판 발행(반목표)이 되살아나기 때문이다.
- 대신 시험 도구 `service_bridge.tools.board_trips_stub`(빈 목록 또는 `--trips` 파일의 목록으로 `/bridge/list_board_trips`에 답하고 `/system/mode`를 한 번 발행)를 더하고, 두 문서의 central 손 확인 줄을 "브리지와 함께, 또는 `board_trips_stub`과 함께"로 고친다. `test_central_node.py`는 이 도구와 같은 방식의 시험용 서버를 띄워 RUNNING까지 확인한다.

### 6.7 커밋 나누기 (사용자 결정 2026-11-28)

SB-06 정정이 다음 주 논의에서 뒤집히면 아래 묶음을 뒤에서부터 `git revert`로 되돌릴 수 있게 나눈다. 묶음끼리 서로의 코드를 고치지 않게 한다.

1. **연결 문서 · 1차 정리:** 7절 디스커버리 안내, 8절(TODO 표시 · `/fleet/robots` 문구 · 1차 문서 상태 · 백엔드 검토 목록 189행을 **현재 SB-06 그대로** 바로잡음). SB-06 정정과 상관없이 남는 것.
2. **인터페이스(contract 1.1):** `CentralState.msg`, `/central/state`, `/bridge/list_board_trips`, contract · 명세 · 통신 규칙(IR-70 · IR-71)을 한 커밋에(IR-90).
3. **브리지 목록 서버:** `core/board_trips.py`, 노드 연결, `board_trips_stub` 도구.
4. **central 단계:** CONNECTING → SYNCING → RUNNING, 시험, 개발 규칙 · README의 central 손 확인 줄.
5. **브리지가 central 지켜보기:** `CENTRAL_RESTART` 알림, RUNNING일 때만 넘김, `NOT_READY` 안 셈, `boot_id` 바뀌면 풀기, 화면 `work_state.central`.
6. **SB-06 · SB-13 · Q-02 "제안" 메모:** 6.5의 문서 메모만. 가장 먼저 되돌릴 수 있는 묶음.
7. **연결 시험 스크립트:** `tools/net/test_central_restart.sh`(S1 ~ S4).
8. **1차 연동 실행 안내 HTML(8.1절):** 검토를 마친 뒤 마지막에. central 단계를 설명하므로, 2 ~ 6을 되돌리면 이 안내의 central 부분도 함께 고친다.

되돌릴 때: 6 → 7 → 5 → 4 → 3 → 2 순으로 되돌리면 1차 동작(SB-06 원문)으로 돌아간다. 1은 남기고, 8은 central 부분만 고친다. 손으로도 고칠 것: 1차 문서의 완료 기준 기록(커밋 `7c55d5e`, central 표시 · `CENTRAL_RESTART` 문장)과, 최종 검토 보완 커밋의 SB-06 의존 문구(통신 규칙 IR-70 · 명세 3.2의 `NOT_READY` 줄)는 2 ~ 6을 되돌려도 저절로 되돌아가지 않는다.

## 7. 디스커버리 서버 연결

코드는 바꾸지 않는다(central의 CONNECTING이 서버 없음 · 브리지 없음을 견딤). 문서와 시험만 더한다.

- **실행 안내:** `pc/service_bridge/README.md` 실행 표 앞에 "브리지 · central을 띄우는 터미널마다 `tools/net/net_ds.sh`를 source(실물 망 `iponic_real`, 혼자 시험 `iponic_local`)"를 적는다. 브리지는 `python -m`으로 띄우므로 venv를 켠 뒤 같은 터미널에서 source한다. `docs/개발_규칙.md`의 브리지 개발 환경 절에도 한 줄.
- **docker 주의:** 운영 구성에서 브리지를 컨테이너에 넣을 때는 host 망을 쓴다(디스커버리 안내 4절 9항). README에 한 줄.
- **central PC 서버(1번):** Q-20이 정해지면 `net_ds.sh`의 `DS_CENTRAL`을 채운다는 안내를 1차 문서 9장에 남긴다(값은 이 설계에서 정하지 않음).
- **시험 스크립트 `tools/net/test_central_restart.sh`:** 한 PC에서 디스커버리 서버 두 개(`127.0.0.1`의 시험 포트, `test_two_servers.sh`와 같은 방식)와 개인 도메인으로 시험 DB · 브리지 · central을 띄우고 아래를 확인해 통과 · 실패를 출력한다.
  - S1 브리지 먼저 켜고 central 나중: central이 RUNNING, 운반 건 넘기기 됨.
  - S2 central 먼저 켜고 브리지 나중: central이 CONNECTING에 머물다 RUNNING.
  - S3 끝나지 않은 건 2개가 있을 때 central만 다시 켬: 새 `boot_id`로 SYNCING → RUNNING, 주문판에 2건이 접수 순서대로, `CENTRAL_RESTART` 알림 1건, `DISPATCH_STALLED` 없음.
  - S4 서버 0을 끈 뒤 central만 다시 켬: 서버 1로 상대를 찾아 RUNNING(안내 문서 C2의 브리지 · central판).
  - 확인은 `ros2 topic echo --once /central/state`(super client), `/central/orders` 한 번 받기, DB 조회(`alert`, `trip_dispatch`)로 한다. fleet은 띄우지 않는다(주문판 내용만 봄).

## 8. 1차 미개발 사항 정리

| 할 일 | 고칠 곳 |
|---|---|
| 1차 문서 상태 갱신: contract 1.0 · 1.1, ReportCommand 받는 쪽 · `FILLING_PAUSE` · 주문 코어 1차 완료, 취소 사유 `NOTHING_TO_FILL`, 4.3의 할 일 1 완료, 6.4 central 재시작 줄, 남은 것(Phony 교환 서버 · Phody `set_pause` · 화면 완료 기준 확인) | `docs/설계/주문_흐름_1차_인터페이스.md` (머리 상태 줄 + 해당 절) |
| central의 `/fleet/robots` 구독: central은 쓰지 않으므로 문서에서 "central은 구독만"을 뺀다(코드 추가 안 함) | 1차 문서 3.3 · 4.2 F-11 줄 |
| 일부러 줄인 것의 표시: 주문 담당 코드에 `TODO(Q-nn)` (해당 Q가 없으면 IR 번호). central 모드 불일치 거부(IR-73, `central_node._on_system_mode`), WS `since_seq`(IR-51, `web/hub.py` 첫 메시지 자리) | `pc/central`, `pc/service_bridge` |
| 백엔드 검토 목록 189행 정정(현재 SB-06 원문대로) | 6.7의 1번 묶음. 제안 문구는 6번 묶음에서 덧붙임 |
| 완료 기준 1(고객 화면 접수됨 → 출고 완료) 확인 | 시험 단계에서 시뮬레이션 한 바퀴 + 고객 화면을 브라우저로 보고 결과를 1차 문서에 적음 |

fleet 코드(`pc/fleet_manager`)의 `TODO` 표시는 관제 담당 코드라 이번에 넣지 않는다.

### 8.1 1차 연동 실행 안내 (HTML, 사용자 요청 2026-11-28)

이 설계까지 마치면 1차 연동 시험에 쓸 주문 쪽 개발이 대부분 끝난다. 팀원이 웹 서버 · 브리지 · central을 빌드하고 띄울 수 있게 실행 안내를 HTML 한 장으로 만든다.

- **위치 · 형식 (설계자 판단):** `docs/1차_연동_실행_안내.html` 한 파일. 바깥 스크립트 · 글꼴 · 그림을 부르지 않아 `file://`로 바로 열린다. 375px 폭에서도 가로로 밀리지 않고, 명령 블록마다 복사 단추를 둔다(스크립트가 없어도 내용은 다 보임). 한국어.
- **원본과의 관계:** 환경 · 명령의 원본은 계속 `docs/개발_규칙.md`와 `pc/service_bridge/README.md`다. HTML 머리에 "기준: 브랜치 · 커밋 · 날짜, 원본 문서"를 적고, 두 원본에서 HTML로 가는 링크를 한 줄 둔다. 두 곳의 명령이 다르면 원본을 따른다는 문장을 넣는다.
- **담을 것:**
  1. 구성 그림: 관제 PC에서 도는 것(DB · 브리지와 웹 · central · fleet)과 로봇 쪽, 디스커버리 서버(0번 192.168.0.252:11811, 1번 central PC는 Q-20 미정), 서로 주고받는 이름(`/system/mode` · `/central/state` · `/bridge/list_board_trips` · `SubmitTrip` · `/central/orders` · `ReportTrip` · `ReportOrder`).
  2. 처음 한 번 준비: ROS 2 Jazzy · docker, 저장소 위치(`$IPONIC_WS/src/`), 가상환경 `~/venv/iponic`과 `pc/requirements.txt`, DB 컨테이너(`design/db/compose.yaml`).
  3. 빌드: 관제 PC용 `colcon build`(또는 `--packages-up-to central service_bridge fleet_manager iponic_bringup`), `--symlink-install`을 쓸 때의 주의(브리지는 `python -m`).
  4. `~/.bashrc` 등록 예: 개발 규칙의 예 그대로 + 브리지 터미널용 함수 예(`iponic_bridge`: ROS → install → venv → `net_ds.sh` 순으로 source). 혼자 시험(`iponic_local`)과 실물 망(`iponic_real`) 고르는 법.
  5. 실행 절차: 터미널별 명령(DB → 브리지(웹) → central → fleet 또는 `fake_fleet`). 띄우는 순서는 아무래도 되지만 권하는 순서를 적고, central이 브리지를 기다리는 동안 "연결 중"으로 머무는 것이 정상임을 적는다.
  6. 확인: `ros2 topic echo --once /central/state`(super client), 고객 화면 `http://<관제 PC>:8000/customer/?account=<id>`, 관리자 화면 `/admin/`의 central 표시, `tools.order` · `tools.ws_watch`로 한 바퀴(접수됨 → 출고 완료).
  7. 끄기 · 다시 켜기: Ctrl+C 한 번, central만 다시 켤 때 보이는 것(연결 중 → 주문판 갱신 중 → 실행 중, `CENTRAL_RESTART` 안내 알림, 관리자 조치 없음), 브리지 · DB를 다시 켤 때.
  8. 주의 사항: 도메인 6은 실물 망(혼자 시험은 개인 도메인 + `FLEET_LOCAL=1`), 통신 설정을 바꾼 터미널은 노드를 다시 띄우고 `ros2 daemon`을 다시 켬, ROS · install을 source한 **뒤** venv, 브리지는 `ros2 run`이 아니라 `python -m`, `mode:=` 필수(`TEST`면 주문 503), `setup_db`는 `_test` DB를 지움, `fake_fleet`과 진짜 fleet을 같이 켜지 않음, `pc.launch.py`의 `fake_central` 기본값이 `true`라 진짜 central을 쓸 때는 `fake_central:=false`, docker는 host 망, 웹 포트 8000.
  9. 문제 해결 표: 증상 → 까닭 → 할 일(`No module named 'psycopg'`, central이 "연결 중"에서 안 넘어감, 관리자 화면 "연결 안 됨", 주문이 503, 노드가 서로 안 보임 등).
- **검토 (끝내기 전에 반드시):**
  1. *따라 해 보기 검토:* 이 대화를 모르는 검토자(새 하위 에이전트)가 HTML만 보고 새 셸에서 혼자 시험 구성(개인 도메인 · LOCALHOST · 시험 DB · `fake_fleet`)으로 처음부터 한 바퀴를 돌린다. 막힌 곳 · 틀린 명령 · 빠진 단계를 적어 낸다. `.bashrc`는 고치지 않고 같은 줄을 셸에서 실행한다.
  2. *원본 대조 검토:* 다른 검토자가 HTML의 명령 · 이름 · 기본값을 개발 규칙 · README · contract · `pc.launch.py` 매개변수와 한 줄씩 맞춰 본다.
  3. *화면 확인:* 브라우저로 데스크톱 폭과 375px 폭에서 열어 깨짐 · 가로 밀림이 없는지 본다.
  - 나온 지적을 고친 뒤 커밋한다. 고치지 않은 지적은 까닭을 계획 기록에 남긴다.

## 9. 시험

- **central (pytest, ROS 없음):** 단계 전환(조건 둘 다 채워야 SYNCING), SYNCING 중 `SubmitTrip` NOT_READY · `ReportOrder` false, 목록 반영(순서 · HELD 표시 · 같은 `trip_id` 다시 보내면 접수, 새로 넣지 않음), 빈 목록이면 빈 주문판으로 RUNNING, 다시 부르는 간격.
- **central (노드, 도메인 분리):** RUNNING 전에는 `/central/orders`가 오지 않음, `/central/state`가 1초마다 옴.
- **브리지 core (pytest + 시험 DB):** 목록에 넣는 건 · 빼는 건(끝 상태, 거절, 미확정 넣음), 순서, `OpenTrip` 칸, `CENTRAL_RESTART` 알림(처음 `boot_id`는 알림 없음, 바뀌면 1건), 넘기기 멈춤 동안 `attempts` 그대로, `NOT_READY`는 `attempts`를 올리지 않음, `boot_id`가 바뀌면 미확정 줄의 `attempts` · 쉼 · `DISPATCH_STALLED` 알림이 풀림.
- **브리지 화면:** `work_state.central` 값과 관리자 화면 표시(`web/static/test/run_headless.sh`에 사례 추가).
- **연결:** 7절 스크립트 S1 ~ S4.
- **회귀:** 브리지 · central · fleet_core · fleet_manager 시험 전체.

## 10. 남은 것 (구현하며 정함)

- central 판단 코드를 `board.py`에 더할지 `lifecycle.py`로 나눌지.
- 브리지가 central 상태를 어디에 둘지(노드 속성 + 웹으로 넘기는 콜백, `on_robots`와 같은 방식 권장).
- 시험 스크립트의 포트 · 도메인 번호(개인 도메인 범위 안에서).
