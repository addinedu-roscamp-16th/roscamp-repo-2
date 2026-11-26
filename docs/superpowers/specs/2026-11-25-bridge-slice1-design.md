# 서비스 브리지 1단계 설계 (fleet 쪽 먼저)

> 새 이름: 1단계-보고 반영 구현 (옛 이름: 첫 조각)

- 상태: 초안 · 작성 2026-11-25 · 수정 2026-11-25 (빌더 점검 반영, 목록 밖 사유 처리, Q-21)
- 담당: 주문 처리
- 근거 문서
  - [주문 흐름 1차 인터페이스](../../설계/주문_흐름_1차_인터페이스.md): 번호 F-nn, 이번 주 범위, 완료 기준
  - [서비스 브리지 기술 조사](../../설계/서비스_브리지_기술_조사.md) v0.5: 시간 한도 · 풀 값(4.2), 켜질 때 순서(4.4), SB-nn(7장)
  - [주문 DB 설계](2026-11-18-order-db-design.md) 5절: `apply_report` · `Hello` · 허용 전이 표 · 잠금 순서
  - [통신 규칙](../../설계/통신_규칙.md) v1.2, [인터페이스 명세](../../설계/인터페이스_명세.md), `interfaces/fleet_interfaces/contract.yaml` 0.7
- 전제: SB-01 · SB-03 · SB-05 · SB-06 확정 내용은 아직 로컬 `docs/sb-confirm` 브랜치(`f4ee732`)에만 있습니다. 이 문서는 그 내용을 따릅니다.

## 1. 목적과 이유

이번 주 목표는 주문 한 건이 화면에서 출고 완료까지 흐르는 것입니다(주문 흐름 1차 1장).

브리지는 웹(DB)과 ROS 쪽(central · fleet)을 잇는 자리입니다. 브리지가 일부라도 떠 있어야 양쪽이 "실제 상대"에 붙여 인터페이스를 만들고 시험할 수 있습니다. 그래서 브리지를 완성품이 아니라 **상대가 먼저 붙을 수 있는 최소 판**으로 먼저 내고, 그 위에 웹과 central을 함께 키웁니다(사용자 결정, 2026-11-24).

1단계의 상대는 **fleet(관제 담당)**입니다. 관제 담당이 이번 주에 만드는 fleet 쪽 `Hello` · `ReportTrip` 호출을 실제 브리지에 붙여 시험할 수 있게 하는 것이 이 단계의 쓸모입니다.

## 2. 목표 · 하지 않는 것 · 실패로 보는 경우

**목표**
1. 명령 하나로 운반 건을 DB에 넣고 central에 넘기면, central 주문판 → fleet → `Hello` · `ReportTrip` → DB → `ReportOrder` → 주문판에서 빠짐까지 한 바퀴 돈다.
2. 관제 담당이 자기 `fleet_manager_node`를 이 브리지 · central에 그대로 붙일 수 있다 (이름 · 타입 · QoS는 contract 0.7 그대로).
3. 주문 흐름 1차 완료 기준 중 **2번**(DB `trip`의 상태 · `report_epoch` · `report_n`이 마지막 보고와 같음)과 **3번**(같은 `ReportTrip` 두 번째는 `DUPLICATE`, DB 그대로)을 이 단계에서 확인한다.
4. 2단계(웹 화면 · REST · WS)를 얹을 때 브리지 코드를 다시 짜지 않는다.

**하지 않는 것** (다음 단계 이후)
- 웹 화면 · REST · WS (F-01 · F-02 · F-03 · F-12 · F-14 · F-15), `ws_event` WS 전달
- 브리지가 outbox를 읽어 `SubmitTrip`을 부르는 길(F-04 호출 쪽, SB-04). 이번에는 시험 명령이 직접 부름
- `/fleet/robots` 구독(F-11) · 로봇 상태 전달
- 재고 차감(출고 `SHIP` 원장), `NEGATIVE_STOCK` 알림 (TT-24 나머지)
- `ListOpenTrips` · 재시작 복구 화면, 마감, 취소, 관리 명령, `ReportCommand` · `ReportEvent`
- 모드가 다르면 연결 거부(IR-73), 모드별 DB 이름 확인
- 관제의 `fleet_manager_node` 고치기(TT-27). 이 단계는 그 대역으로 "가짜 fleet"을 둠

**실패로 보는 경우** (기술적으로 돌아가도)
- 관제 담당이 붙이려면 contract와 다른 이름 · 타입 · QoS를 따로 맞춰야 한다.
- 같은 보고를 다시 보냈을 때 DB가 두 번 바뀐다.
- fleet이 보낸 값 하나(모르는 사유 코드, 주문보다 많은 담은 수 등) 때문에 DB 쓰기가 계속 실패해 fleet이 끝없이 다시 보낸다.
- 2단계에서 웹을 얹을 때 판단 코드(`apply_report` 등)를 ROS 코드에서 떼어 내야 한다.

## 3. 방식 (Approaches)

| 안 | 1단계 | 장점 | 대가 |
|---|---|---|---|
| **A. fleet 쪽 먼저 (선택)** | `Hello` · `ReportTrip` 서버 + DB 기록, `/system/mode` 발행. 주문판을 채우려고 central 간이판과 시험 명령을 함께 둠 | 관제 담당이 이번 주에 만드는 F-06 · F-08을 바로 실제 브리지에 붙여 시험. 웹 · central은 본인 몫이라 그동안 대역으로 버팀 | `ReportTrip` 판정이 SB-01 · SB-03에 걸림 → 확정함(2026-11-24) |
| B. 웹 · central 쪽 먼저 | 웹 접수 → `SubmitTrip` → central | 주문이 들어가는 앞부분부터 눈으로 확인 | 관제 담당은 그동안 "가짜 브리지"로 시험, 실제 연결이 늦어짐 |

사용자가 A를 골랐습니다(2026-11-24). fleet은 central 주문판(F-05)을 보고 일을 시작하므로, A만으로는 fleet이 보고할 운반 건이 생기지 않습니다. 그래서 **central 간이판(F-04 서버 · F-05 · F-10)과 "운반 건 하나를 넣는 명령"**을 1단계에 함께 넣었습니다(본인 담당 범위 안).

## 4. 범위

| 부분 | 1단계에 넣을 것 | Jira |
|---|---|---|
| 브리지 (fleet 쪽) | F-06 `/bridge/hello` 서버, F-08 `/bridge/report_trip` 서버 + F-09 DB 반영(`apply_report`), F-13 `/system/mode` 발행 | TT-25 · TT-24(일부) · TT-28(발행만) |
| 웹 · 브리지 기반 | 패키지 · 프로세스 뼈대, DB 연결 풀. 이번 단계는 rclpy만 띄우고 FastAPI는 2단계에서 같은 프로세스에 얹음 | TT-22(일부) |
| central (간이) | F-04 `/central/submit_trip` 서버 → F-05 `/central/orders` 발행 → F-10 `/central/report_order` 서버로 빼기, `/system/mode` 구독(로그만) | TT-26 |
| 웹 대신 | 시험 명령 `submit_trip`: 운반 건을 DB에 넣고 `SubmitTrip`을 부름 | — |
| fleet 대신 | 가짜 fleet 노드: 주문판을 받아 `Hello` → `ReportTrip` 4번 → `ReportOrder` | TT-27 대역 |
| DB | `design/db`의 스키마 그대로(PostgreSQL 16). 이번 단계가 쓰는 표: `account` · `product` · `orders` · `order_item` · `trip` · `trip_item` · `status_history` · `alert` · `bridge_session` (`ws_event`는 트리거가 채움) | — |

## 5. 구성

### 5.1 패키지와 파일 (제안, 코드 위치는 주문 흐름 1차 5.3에서 "구현 계획 때 확정")

```
pc/service_bridge/            ament_python 패키지 (새로 만듦)
  service_bridge/
    core/                     ROS 없음. 2단계에서 웹도 이 함수를 그대로 씀
      db.py                   연결 풀 · 설정 (DSN, 시간 한도)
      transitions.py          허용 전이 표(SB-03), 주문 요약 규칙(DB 설계 4.4) — 순수 함수
      reports.py              apply_report(conn, ReportIn) -> ReportOut
      hello.py                apply_hello(conn, HelloIn) -> HelloOut
    ros/
      bridge_node.py          Hello · ReportTrip 서버, /system/mode 발행
      convert.py              ROS 메시지 ↔ core 데이터 클래스
    main.py                   진입점: rclpy + MultiThreadedExecutor (2단계에서 uvicorn 추가)
    tools/
      setup_db.py             시험 DB 만들기 + 시험 데이터 넣기
      submit_trip.py          시험 명령 (웹 대신)
      fake_fleet.py           가짜 fleet 노드
  test/
pc/central/central/
  board.py                    주문판 · 응답 기억 — 순수 클래스 (ROS 없음)
  central_node.py             SubmitTrip · ReportOrder 서버, 주문판 발행
pc/central/test/
```

- 판단 코드(`core/`, `board.py`)와 ROS 코드를 나눕니다. 웹이 붙어도 `core/`를 그대로 부르고, 운영 때 웹 · 브리지를 나눌 때도 이 경계에서 자릅니다(주문 흐름 1차 5.3).
- contract는 `fleet_core.common.contract`의 `load_contract` · `to_ros_qos`로 읽습니다(IR-01). `fleet_manager_node` · `fake_central_node`와 같은 방법입니다. 코드에 이름 문자열을 적지 않습니다.
- `tests/service_bridge/README.md`의 "나중에 할 일"은 코드가 `pc/central`에 생긴다고 적었습니다. 브리지를 `pc/service_bridge`에 두면 그 줄을 함께 고칩니다.

### 5.2 흐름

```
submit_trip(명령) ──DB INSERT (orders · trip RECEIVED)──▶ PostgreSQL
      │
      └─ /central/submit_trip ─▶ central ─ /central/orders (LATCHED) ─▶ fleet (가짜 또는 관제 것)
                                    ▲                                     │
                                    └──── /central/report_order ──────────┤ (끝 보고가 accepted=true 뒤)
                                                                          │
         PostgreSQL ◀── apply_hello · apply_report ── 브리지 ◀── /bridge/hello · /bridge/report_trip
                                                       │
                                                       └─ /system/mode (LATCHED) ─▶ central · fleet
```

## 6. 브리지

### 6.1 켜질 때 순서 (조사 문서 4.4에서 이번 단계에 해당하는 것만)

1. 설정을 읽고 노드를 만든 뒤 `/system/mode`를 발행합니다. DB와 상관없이 바로 냅니다.
2. 동기 연결 풀을 열고 첫 연결을 확인합니다. 실패하면 3 ~ 4를 미루고 다시 시도합니다(서버가 없으면 fleet이 시간 초과 뒤 다시 보내므로 잃는 것이 없음).
3. `bridge_session`을 읽어 로그에 남깁니다. 브리지 재시작은 fleet 재시작이 아니므로 epoch를 올리지 않고 `HELD` 처리도 하지 않습니다.
4. `/bridge/hello` · `/bridge/report_trip` 서버를 만듭니다.

끌 때는 서버를 먼저 없애고 `executor.shutdown(timeout_sec=2.0)`, 그다음 풀을 닫습니다.

### 6.2 설정

| 항목 | 값 | 어디서 |
|---|---|---|
| 운영 모드 | `NORMAL` · `TEST` (이번 주 시험은 `TEST`) | 노드 매개변수 `mode`, 기본값 없음(빠지면 켜지지 않음) |
| DB 주소 | 예: `postgresql://postgres:ifp_test@localhost:55432/ifp_test` (`design/db/compose.yaml`) | 환경 변수 `IPONIC_DB_DSN` |
| 연결 풀 | min = max = 4, 연결 얻기 0.5초, `max_waiting` 8, 연결 점검 켬 | 조사 문서 4.2 |
| DB 시간 한도 | `statement_timeout` 1500ms · `lock_timeout` 500ms · `idle_in_transaction_session_timeout` 10초 | 조사 문서 4.2. 연결할 때 세션 옵션으로 겁니다(시험 DB는 역할을 따로 두지 않음) |
| 실행 스레드 | `MultiThreadedExecutor(num_threads=4)` | 풀 크기와 같게 |

### 6.3 `/bridge/hello` (F-06)

`Hello` 서버는 자기 MutuallyExclusive 콜백 그룹에 둡니다. 한 트랜잭션에서:

1. `node`가 `fleet`이 아니면 `accepted=false`, `reason=UNKNOWN_NODE` (DB 쓰지 않음).
2. `bridge_session`의 그 줄을 `FOR UPDATE`로 읽습니다.
3. 줄이 있고 `boot_id`가 같으면: **epoch를 올리지 않고** 그 값을 그대로 돌려줍니다(다시 보낸 `Hello`, 주문 흐름 1차 F-06, `TODO(Q-09)`).
4. 아니면 epoch를 1 올리고(줄이 없으면 1) `boot_id` · `hello_at`을 씁니다.
5. 새 epoch를 만든 경우, **지금 진행 중인 운반 건 모두**(`ASSIGNED` · `FILLING` · `FILLING_PAUSE` · `TRANSPORTING`, 번호와 상관없이)를 `HELD`로 씁니다. 이력 사유 `FLEET_RESTART`, actor `SYSTEM`. 주문 요약도 다시 계산합니다. 이때 그 운반 건의 번호를 **(새 epoch, 0)**으로 바꿉니다. 그래야 죽은 fleet이 보낸 옛 epoch의 늦은 보고가 `DUPLICATE`로 걸러지고, 새 fleet의 첫 보고 (새 epoch, 1)부터 반영됩니다. 잠금은 orders(`order_id` 순) → trip(`trip_id` 순)으로 잡습니다(SB-05. 원장에 쓰지 않으므로 7001은 잡지 않음).
6. 커밋 뒤 `accepted=true`, `epoch`.

- `HELD`로 쓴 운반 건은 central 주문판에 그대로 남아 있습니다. 새로 켜진 fleet이 다시 묶어 새 epoch로 `ASSIGNED`를 보내면 허용 전이(`HELD` → `ASSIGNED`)로 반영됩니다. 이번 주에는 `ListOpenTrips` 없이 이 길로만 이어집니다.
- DB 오류는 모두 `accepted=false`, `reason=DB_ERROR`.

### 6.4 `/bridge/report_trip` (F-08 · F-09)

서버 콜백은 받은 요청을 `ReportIn`으로 바꿔 `apply_report`를 부르고, **커밋한 뒤** 응답합니다(IR-12). 다른 서비스를 부르거나 기다리지 않습니다(IR-31). `ReportTrip` 서버는 `Hello`와 다른 MutuallyExclusive 그룹에 둡니다(보고는 한 번에 하나씩 처리됨).

**`apply_report` 한 트랜잭션** (DB 설계 5절 · SB-01 · SB-03 · SB-05)

1. `trip`에서 `order_id`를 잠금 없이 읽습니다. 운반 건이 없으면 7로 갑니다(사유 `NOT_FOUND`).
2. `SELECT pg_advisory_xact_lock_shared(7001)` → `orders` 그 줄 `FOR UPDATE` → `trip` 그 줄 `FOR UPDATE`. 이번 단계는 원장에 쓰지 않지만, 2단계에서 출고 차감을 넣을 때 순서를 바꾸지 않도록 처음부터 7001을 잡습니다.
3. **번호 비교**: `(report_epoch, report_n)`이 `trip`의 값보다 **같거나 작으면**(`trip` 값이 NULL이면 무엇보다 작음) 아무것도 바꾸지 않고 `DUPLICATE` (SB-01).
4. **값 검사** (8 참고): 상태가 9개 중 하나인지, `report_n`이 DB 칸(integer) 범위 안인지, `placed` · `missing`의 `kind`가 그 운반 건의 상품인지, 상품마다 `placed + missing ≤ 주문 수`인지. 어기면 7로 갑니다(사유 `INVALID_REPORT`).
5. **허용 전이** (SB-03 표): 지금 상태에서 갈 수 없는 상태면 7로 갑니다(사유 `NOT_ALLOWED`).
6. **반영** → `APPLIED`
   - 같은 상태면: `trip_item`의 담은 수 · 못 담은 수와 번호만 고칩니다. 이력은 남기지 않습니다.
   - **사유**: 빈 값은 NULL. 운반 건 사유 목록(`status_history` CHECK와 같은 목록) 밖의 값이면 상태는 그대로 반영하고, 이력 사유 칸은 NULL, 받은 값은 이력 `detail`에 `{"reported_reason": "…"}`로 남기고 WARN 로그를 씁니다. 사유 하나 때문에 상태를 버리거나 DB 쓰기가 실패하지 않게 하기 위함입니다. 예: 팔 멈춤 코드 `DISTANCE_STOP`은 알림 코드라 운반 건 사유가 아님(설계 검토 사항 Q-21)
   - 다른 상태면: `trip.status` · `robot_id` · 번호, `trip_item` 수량, `placed`가 처음 채워지면 `placed_at`, 처음 `ASSIGNED`가 되면 `first_assigned_at`(한 번만), 끝 상태(`SHIPPED` · `PARTIAL` · `CANCELLED`)면 `finished_at`. `status_history`에 한 줄(`from` · `to` · `reason` · `robot_id`, actor `SYSTEM`).
   - 주문 요약 상태를 다시 계산하고(DB 설계 4.4 규칙), 바뀌면 `orders.status` · `finished_at`과 `trip_id` 없는 이력 한 줄.
   - `ws_event`는 `trip` · `orders` 트리거가 같은 트랜잭션에서 채웁니다(코드에서 따로 쓰지 않음).
7. **`LATE_REPORT`**: 상태 · 수량 · 번호를 바꾸지 않고 `alert`을 엽니다(`code=LATE_REPORT`, `level=WARN`, `source=fleet`, 받은 보고 전체를 `detail`에). 같은 보고가 다시 와도 알림이 하나만 생기도록 `event_id = "LATE:{trip_id}:{epoch}:{n}"`로 넣고 이미 있으면 넘어갑니다. 운반 건이 없으면 `trip_id` 칸은 비우고 `detail`에 적습니다.
8. 커밋 뒤 응답합니다.

**응답 표**

| 결과 | `accepted` | `result` | `reason` |
|---|---|---|---|
| 반영 | true | `APPLIED` | — |
| 같거나 작은 번호 | true | `DUPLICATE` | — |
| 허용되지 않는 전이 · 운반 건 없음 · 값 오류 | true | `LATE_REPORT` | `NOT_ALLOWED` · `NOT_FOUND` · `INVALID_REPORT` |
| DB 오류(잠금 시간 초과 · 연결 실패 · 그 밖의 예외) | false | — | `DB_ERROR` |

- 교착(40P01) · 잠금 시간 초과(55P03)는 요청 마감(받은 시각 + 2.5초) 안에서 **한 번만** 트랜잭션 전체를 다시 합니다(조사 문서 4.2 · 5.2). 그래도 실패하면 `DB_ERROR`.

- `accepted=false`는 "다시 보내 주세요"뿐입니다. fleet이 보낸 값 때문에 다시 보내도 같은 결과가 나오는 경우(4번 값 오류)는 `false`로 답하지 않습니다. 그러면 fleet이 끝없이 다시 보내기 때문입니다.
- 콜백 전체를 예외 처리로 감싸고, 어떤 예외도 서버 밖으로 나가지 않게 합니다(조사 문서 5.2 합의).

### 6.5 `/system/mode` (F-13)

켤 때 매개변수 `mode`를 LATCHED로 한 번 발행합니다(`stamp`는 발행 시각). 실행 중에는 바꾸지 않습니다.

## 7. central (간이)

`board.py`(ROS 없음)가 상태를 갖고, `central_node.py`는 서버 · 발행만 합니다. 서비스 콜백은 짧고 다른 서비스를 부르지 않으므로 단일 스레드 실행기로 충분합니다. DB는 없습니다(IR-55).

| 상태 | 내용 |
|---|---|
| 주문판 | 받은 순서대로의 운반 건 목록 (`trip_id`, `items`, `created_at = submitted_at`) |
| 응답 기억 | `trip_id` → 처음의 확정 응답(접수 · 거절). `NOT_READY`는 기억하지 않음(SB-06). 이번 단계에는 `NOT_READY`를 내는 경우가 없음 |
| 끝난 건 | 주문판에서 뺀 `trip_id` → 끝 상태 |

**`SubmitTrip` (F-04 서버)**
- 같은 `trip_id`가 다시 오면 기억한 응답을 그대로 돌려줍니다(주문판은 바뀌지 않음).
- `trip_id`가 비었거나, `items`가 비었거나, 개수가 1 미만이면 `accepted=false`, `reason=INVALID_REQUEST` (기억함).
- 그 밖에는 주문판 끝에 넣고 `accepted=true`, 주문판을 바로 발행합니다.

**`/central/orders` (F-05)**
- `OrderBoard(run_mode=RUN, orders=[Order(order_id=trip_id, items, created_at)])`. 바뀔 때마다, 그리고 1초마다 다시 발행합니다(`fake_central_node`와 같음).

**`ReportOrder` (F-10 서버)**
- `status`가 `SHIPPED` · `PARTIAL` · `CANCELED` · `HELD`이고 주문판에 있으면: 빼고, 끝난 건에 적고, 주문판을 발행, `accepted=true`.
- 이미 뺀 건이면 그대로 `accepted=true` (IR-11, 같은 결과).
- 주문판에도 끝난 건에도 없는 `order_id`면 WARN 로그를 남기고 `accepted=true`. fleet의 `ReportOrder`는 포기하지 않고 다시 보내므로, `false`로 답하면 끝없이 되풀이됩니다.
- 그 밖의 `status`면 `accepted=false` (fleet 코드 오류라 로그로 드러냄).

**`/system/mode`**: 구독해 로그만 남깁니다.

- central을 다시 켜면 주문판 · 응답 기억이 사라집니다. **지원하지 않음(알려진 위험, SB-06)**. 이번 단계는 central 재시작을 알아채는 브리지 쪽 코드도 없습니다(브리지가 `SubmitTrip`을 부르지 않으므로).

## 8. 시험 도구

### 8.1 `setup_db` (시험 DB 준비)

- `design/db`의 `order_db.sql` · `order_db_extra.sql`로 시험 DB를 새로 만들고, 상품 `A` · `B` · `C` · `D`(`sim/fake_robots/scenarios`와 같은 이름)와 시험 계정(`USER`) 하나를 넣습니다.
- 재고 20은 상품을 재고 0으로 만든 뒤 `stock_ledger` 입고(`INBOUND`)로 넣습니다. `product.stock_qty`를 직접 쓰면 `tg_product_stock_guard`가 막습니다. 원장을 쓰지 않는 것은 `apply_report`(출고 차감)이고, 시험 데이터 준비는 해당하지 않습니다.
- PostgreSQL은 `design/db/compose.yaml`(포트 55432, tmpfs)로 띄웁니다.

### 8.2 `submit_trip` (웹 대신)

```bash
ros2 run service_bridge submit_trip --items A:1,C:1
```

1. 한 트랜잭션에서 `orders` · `order_item` · `trip`(seq 1, `RECEIVED`) · `trip_item`과 접수 이력 두 줄(운반 건 · 주문, `from_status` NULL → `RECEIVED`, actor `USER`)을 넣습니다. 웹이 접수할 때와 같게 둡니다. 나누지 않으며, 품목 합이 `basket_capacity`(4)를 넘으면 거절합니다. 번호 형식은 DB 설계를 따릅니다(예: `O-20261125-0001`, 운반 건은 그 뒤에 `-1`).
2. 커밋한 뒤 `/central/submit_trip`을 부릅니다. 2초 한도, 1초 간격, 첫 요청 포함 5번(F-04 값). 응답을 출력합니다.
3. `--trip-id`를 주면 DB에 넣지 않고 그 운반 건을 다시 보냅니다(central 응답 기억 시험).

### 8.3 `fake_fleet` (fleet 대신)

`fleet_manager_node`가 브리지 · central을 부르는 방식만 흉내 냅니다. 배정 · 로봇은 없습니다.

1. 켜지면 `boot_id`(UUID)로 `Hello`. epoch를 받기 전에는 보고하지 않습니다.
2. 주문판에 새 운반 건이 보이면 `ASSIGNED` → `FILLING` → `TRANSPORTING`(placed = 주문 전체) → `SHIPPED`를 간격(매개변수, 기본 1초)을 두고 보냅니다. `report_n`은 노드 전체에서 1씩 늘리고, 보고마다 지금 상태 전체를 싣습니다.
3. `SHIPPED`가 `accepted=true`를 받은 뒤 `ReportOrder(SHIPPED)`를 부릅니다.
4. 3초 한도, 1 → 2 → 4초(최대 10초) 간격, 포기하지 않음(F-08 값).
5. 시험용 매개변수: `duplicate_every_report`(같은 번호로 한 번 더 보냄), `missing`(결품 흉내, 끝 상태 `PARTIAL`).

관제 담당의 `fleet_manager_node`가 준비되면 이 노드 대신 그것을 붙입니다. 두 노드를 같이 켜지 않습니다(둘 다 같은 주문판을 보고 보고함).

- `sim/fake_robots` 런치는 기본으로 `fake_central`을 띄웁니다. 이 central과 이름(`/central/orders` · `/central/report_order`)이 겹치므로, 붙여 볼 때는 반드시 `fake_central:=false`로 켭니다.

## 9. 시험

| 무엇 | 어떻게 | 확인 |
|---|---|---|
| 허용 전이 · 주문 요약 | `transitions.py` 단위 시험 (DB 없음) | SB-03 표의 모든 칸, DB 설계 4.4 규칙 4개 |
| `apply_report` · `apply_hello` | 실제 PostgreSQL 16 (`design/db/tests`와 같은 방식) | 아래 사례 |
| `board.py` | 단위 시험 (ROS 없음) | 응답 기억, 주문판 순서, 빼기 · 다시 빼기 · 모르는 건 |
| 한 바퀴 | rclpy 시험: 브리지 + central + 가짜 fleet + `submit_trip`을 한 시험 도메인에서 | 완료 기준 2 · 3, 주문판에서 빠짐 |

**DB 시험 사례**
- 정상 흐름 4번 보고 → `trip`이 `SHIPPED`, 번호 (e, 4), 운반 건 이력 5줄(접수 1 + 보고 4), 주문 `SHIPPED`, `ws_event` 줄 생김 (완료 기준 2)
- 같은 보고 두 번 → 두 번째 `DUPLICATE`, DB 그대로 (완료 기준 3)
- 더 작은 번호의 옛 보고(예: (7, 3)이 (8, 1) 뒤) → `DUPLICATE`, DB 그대로
- 끝난 운반 건에 다른 상태 → `LATE_REPORT`, 알림 하나. 같은 보고를 다시 보내도 알림은 하나
- 앞으로 건너뛰기(`RECEIVED` → `TRANSPORTING`) → `APPLIED`
- 값 오류(모르는 사유 코드, 주문보다 많은 담은 수, 모르는 상품) → `LATE_REPORT`, `accepted=true`
- 운반 건 없음 → `LATE_REPORT`, `accepted=true`
- DB를 내린 상태 → `accepted=false`, `DB_ERROR`
- 7001 배타 잠금을 다른 연결이 잡고 있으면 → 500ms 뒤 `accepted=false`
- `Hello` 같은 `boot_id` 두 번 → epoch 그대로. 다른 `boot_id` → epoch +1, 진행 중 운반 건 `HELD`(`FLEET_RESTART`) · 번호 (새 epoch, 0), 그 뒤 옛 epoch의 늦은 보고 → `DUPLICATE`, 새 epoch의 `ASSIGNED` → `APPLIED`
- 사유 코드가 목록 밖 → `APPLIED`, 이력 사유 NULL, `detail`에 받은 값
- `report_n`이 integer 범위를 넘음 → `LATE_REPORT`(`INVALID_REPORT`), `accepted=true`

**시험 네트워크**: 개인 도메인 + `LOCALHOST` (`FLEET_LOCAL=1 source tools/net/net_ds.sh`, IR-81). 관제 담당의 실제 fleet과 붙일 때는 둘이 같은 도메인 · 같은 디스커버리 설정을 쓰고, 운영 도메인 6은 쓰지 않습니다.

## 10. 관제 담당과 맞출 것 (1단계를 붙이기 전)

| 항목 | 이 문서의 안 |
|---|---|
| `ReportOrder`를 부르는 끝 상태 | `SHIPPED` · `PARTIAL` · `CANCELED`(SB-02 제안)와, 0.6처럼 `HELD`도. central은 `HELD`를 받아도 주문판에서 뺌(`fake_central_node`와 같음) |
| central이 모르는 `order_id`의 `ReportOrder` | `accepted=true` + 로그 (0.6 `fake_central_node`는 `false`) |
| `LATE_REPORT`의 `reason` 값 | `NOT_ALLOWED` · `NOT_FOUND` · `INVALID_REPORT`. fleet은 `accepted=true`면 결과와 상관없이 다음으로 넘어감 |
| `Hello`의 `node` 값 | `fleet` (그 밖은 `UNKNOWN_NODE`) |
| 붙여 보는 날짜 · 도메인 번호 | 정하지 않음 |

## 11. 정한 사람

**사용자가 정한 것**
- A안(fleet 쪽 먼저), 1단계 범위(4장 표의 큰 틀), 웹 대신 시험 명령 (2026-11-24)
- SB-01 · SB-03 · SB-05 · SB-06 확정 (2026-11-24)
- 이번 주 기술 스택: 웹 · 브리지 한 프로세스, FastAPI · rclpy, PostgreSQL 16 · psycopg 3, central은 `pc/central` rclpy 노드 (주문 흐름 1차 5.3)

**이 문서에서 제가 정한 것** (검토 때 확인 부탁드립니다)
- 브리지 위치 `pc/service_bridge`, `core/` · `ros/` 나누기, 가짜 fleet을 이 패키지 `tools/`에 둠
- 재고 차감을 다음 단계로 미룸. 단, 7001 공유 잠금은 처음부터 잡음
- fleet 값 오류를 `accepted=false`가 아니라 `LATE_REPORT`(`INVALID_REPORT`)로 답함. 단 목록 밖 사유는 오류로 보지 않고 상태를 반영함 (사용자 확인 2026-11-25)
- `LATE_REPORT` 알림 중복을 `event_id`로 막음 (운반 건 번호는 바꾸지 않음)
- `Hello`의 `HELD` 대상에 `FILLING_PAUSE` 포함
- central: 모르는 `order_id`도 `accepted=true`, 서비스 대기 없이 단일 스레드
- 운영 모드 매개변수에 기본값을 두지 않음
- `Hello`의 `HELD` 대상은 번호와 상관없이 진행 중인 모든 건, 번호를 (새 epoch, 0)으로 바꿈 (검수 질문 4)
- 교착 · 잠금 시간 초과는 마감 안에서 한 번만 다시 (조사 문서 4.2를 따름, 검수 질문 5)
- 시험 재고는 원장 입고로 넣음, `submit_trip`이 접수 이력을 넣음 (검수 질문 2 · 6)
- `report_n`이 integer를 넘으면 `INVALID_REPORT`. 칼럼을 bigint로 바꾸는 일은 하지 않음 (1부터 세면 넘을 일이 없음, 검수 질문 3)

## 12. 만드는 사람에게 맡기는 것

- 데이터 클래스 · 함수 이름, 로그 문구, 패키지 설정 파일 세부
- 시험 DB 이름 · 만드는 방법(`setup_db`가 `psql`을 부를지 psycopg로 넣을지)
- 한 바퀴 시험을 `launch_testing`으로 할지 pytest 안에서 노드를 띄울지
- 가짜 fleet의 보고 간격 기본값 · 매개변수 이름
- venv(`--system-site-packages`)에 psycopg를 넣는 방법과 `docs/개발_규칙.md`에 적을 실행 절차
