# 2단계-주문 접수 REST API/WS 구현 설계

- 상태: 구현됨 (커밋 68d7002 ~ 3c2a9f2, 이름 맞춤 포함) · 작성 2026-11-25 · 수정 2026-11-26 (화면 연결 요청 N-05 ~ N-08 반영: 스냅샷 · `stock` 칸, 화면 파일 내주기) · 이전 수정 2026-11-25 (검토자 질문 반영, 넘기기 가 안 · 멈춤 알림, 알림 WS는 고도화 때)
- 담당: 주문 처리
- 앞 단계: **1단계-보고 반영 구현** ([설계](2026-11-25-bridge-slice1-design.md), PR #14). 옛 이름 "첫 조각 · 1조각"
- 근거 문서
  - [주문 흐름 1차 인터페이스](../../설계/주문_흐름_1차_인터페이스.md)(이하 [계획]): F-nn 번호, 완료 기준, 5.2 WS `data` 칸, 5.3 기술 스택
  - [인터페이스 명세](../../설계/인터페이스_명세.md) 3.1 구간 A (HTTP · WS)
  - [주문 DB 설계](2026-11-18-order-db-design.md) 4.6 · 4.13 · 4.14 · 4.16, 5절 주문 등록 · 잠금 7001
  - [서비스 브리지 기술 조사](../../설계/서비스_브리지_기술_조사.md) v0.6: SB-04 · SB-06, 4.2 시간 한도, 4.4 켜질 때 순서
  - [백엔드 검토 목록](../../설계/백엔드_검토_목록.md) v0.8: 2.1 이번 주 먼저 볼 것, 7장 구현 방식 논의
  - [통신 규칙](../../설계/통신_규칙.md): IR-20 · IR-31 · IR-40 · IR-50 · IR-51 · IR-52 · IR-55
- 기준: dev `52b6da4`, contract 0.8

## 1. 목적과 이유

이번 주 목표는 주문 한 건이 화면에서 출고 완료까지 흐르는 것이다([계획] 1장). 1단계로 fleet 쪽(`Hello` · `ReportTrip` → DB)은 이어졌다. 그러나 주문은 아직 시험 명령(`submit_trip`)이 DB에 직접 넣고 central을 직접 부른다.

2단계는 **웹 쪽 입구와 출구**를 만든다.
- 입구: 주문 접수 API가 주문을 운반 건으로 나눠 DB에 쓰고, 브리지가 그 운반 건을 central에 넘긴다.
- 출구: DB의 상태 변화와 로봇 상태를 WS로 화면에 보낸다.

화면(HTML · JS)은 이번 일에서 뺀다. 화면은 스토리보드와 스타일만 있어 함께 만들면 일이 많고 검수가 어렵다. 화면이 나오면 별도 시험 계획으로 확인한다(사용자 결정 2026-11-25).

## 2. 목표 · 하지 않는 것 · 실패로 보는 경우

**목표**
1. HTTP로 주문을 넣으면 운반 건으로 나뉘어 DB에 쓰이고, 브리지가 central에 넘기고, fleet 보고가 반영되어 WS로 `order_changed`가 접수됨 → 준비 중 → 출고 중 → 출고 완료 순서로 나온다. 완료 기준 1번을 화면 대신 WS 사건 순서로 확인한다.
2. 완료 기준 2 · 3번(1단계에서 확인)이 2단계 구성에서도 그대로 통과한다.
3. 완료 기준 4번: 시험 모드로 켜면 주문이 거절되고 고객 WS에 "점검 중"이 간다.
4. 화면 담당이 이 API · WS에 바로 붙일 수 있다. 이름 · 겉모양 · `data` 칸은 명세 3.1과 [계획] 5.2 그대로다.

**하지 않는 것**
- 화면(HTML · JS) 만들기. 화면 파일은 화면 담당이 `web/static/`에 넣고, 웹은 그 폴더를 내주기만 한다(5.6, 2026-11-26 N-07)
- 인증. 이번 주는 `account_id`를 요청에 싣는다(5.1)
- 운영 모드 바꾸기 요청(F-15). 모드는 켤 때 실행 인자로만 정한다
- 취소, 관리 명령, `ReportCommand` · `ReportEvent`, 마감, 재시작 복구(`ListOpenTrips`)
- WS `since_seq` 다시 받기. 연결하면 스냅샷부터 다시 받는다
- 알림 사건 WS(`alert_raised` · `alert_resolved`)와 알림 화면 방식(Q-07 · Q-08). 알림 행 · 사건 기록은 남기고, 보내기는 고도화 때 한다(7.5)
- 출고 차감(`SHIP` 원장), `NEGATIVE_STOCK`
- `basket_capacity` 관제 전달(`Hello` + 서비스). 다음 주에 한다. 이번 주는 DB · 관제 모두 4다
- 웹 · 브리지를 두 프로세스로 나누기(검토 목록 7.2 B 안)

**실패로 보는 경우** (기술적으로 돌아가도)
- 같은 `Idempotency-Key`로 다시 보낸 주문이 운반 건을 두 번 만든다.
- 접수는 됐는데 `trip_dispatch` 줄이 없어 central에 영영 넘어가지 않는다.
- WS 사건이 DB 커밋과 어긋난다: 롤백된 변화가 나가거나, 커밋된 변화가 빠진다.
- 고객 WS에 다른 고객의 주문이나 내부 상태 이름 · 사유가 나간다(IR-52).
- 웹 요청 처리 중에 ROS 콜백을 기다리거나, ROS 콜백 안에서 DB 잠금을 쥔 채 central을 기다린다(IR-31).
- 화면 담당이 붙이려면 명세와 다른 이름 · 칸을 따로 맞춰야 한다.

## 3. 방식 (Approaches)

| 안 | 내용 | 장점 | 대가 |
|---|---|---|---|
| **A. 한 프로세스에 웹을 얹음 (선택)** | 메인 스레드 FastAPI · uvicorn, 뒷 스레드 rclpy 실행기(1단계 `BridgeNode`). 로봇 상태는 프로세스 안에서 넘김 | [계획] 5.3에서 정한 구성. 1단계 코드를 그대로 씀. 이번 주에 가장 빠름 | 웹을 다시 켜면 브리지도 다시 켜짐(fleet이 보고를 다시 보내 잃는 것 없음). 운영에서 나눌 때 로봇 상태 길을 다시 만듦 |
| B. 두 프로세스, DB로만 이음 | 웹 · 브리지를 따로 띄움. 로봇 상태는 `pg_notify('robots', …)`로 넘김 | 운영 구성과 같음. 내부 REST가 거의 필요 없음 | [계획] 5.3 · 명세 1.1을 바꿈. 이번 주 일이 늘어남 |
| (뺌) 처음부터 내부 REST로 나눔 | 명세 1.1의 운영 구성 | — | 이번 주에 만들 것이 가장 많음. 계획에서 미룬 일 |

사용자가 A를 골랐다(2026-11-25). B는 운영에서 나눌 때의 후보로 검토 목록 7.2에 남겼다.

## 4. 범위

| 부분 | 넣을 것 | 번호 |
|---|---|---|
| 웹: 주문 접수 | `POST /api/orders`: 검사, 나누기, 7001 공유, 주문 · 운반 건 · 이력 · `trip_dispatch` 쓰기, 같은 키는 같은 결과 | F-01 |
| 웹: 스냅샷 | `GET /api/snapshot?view=customer|admin` | F-02 |
| 웹: WS | `/ws/customer?account_id=…` · `/ws/admin`. 연결하면 스냅샷, 그 뒤 사건 · 상태 | F-03 · F-12 · F-14 |
| 웹: 사건 전달 | LISTEN `ifp_events` → `ws_event` 읽기 → WS | F-03 |
| 브리지: 넘기기 | `trip_dispatch` 빈 줄 → `SubmitTrip`(F-04 호출 쪽), 접수 커밋 뒤 곧바로 깨움 | F-04 |
| 브리지: 로봇 상태 | `/fleet/robots` 구독 → 메인 스레드 → WS `robots` | F-11 · F-12 |
| 브리지: 보고 보강 | `ReportTrip.stamp`를 `LATE_REPORT` detail에 남김 | E8 (검토 목록 2.1 #5) |
| DB | `trip_dispatch` 테이블(4.16), `ws_event` 트리거가 [계획] 5.2 `data`를 채우도록 고침, 시험 계정 seed | SB-04, [계획] 8장 |
| 실행 | `bridge` 진입점이 FastAPI와 rclpy를 함께 띄움 | [계획] 5.3 |
| 시험 도구 | `order`(주문 넣기), `ws_watch`(WS 받아 보기). `submit_trip`은 남겨 둠 | — |
| 이름 바꾸기 | 문서 · README · 주석의 "조각"(구현 순서 뜻)을 "1단계 · 2단계"로. `fleet_core`의 경로 "조각"은 그대로 | — |

## 5. 웹 API

### 5.1 공통

- 고객 구분: 주문 요청 본문의 `account_id`와 WS 주소의 `account_id`. 읽는 곳은 함수 하나(`current_account`)로 모은다. 인증을 붙일 때 그 함수만 바꾼다. `account`에 없는 번호면 404 `UNKNOWN_ACCOUNT`.
- 관리자 쪽(`/ws/admin`, `view=admin`)도 이번 주는 인증 없이 둔다. 내부 시험용이다.
- 오류 본문: `{"code": "...", "message": "...", "detail": {...}}`. `code`는 아래 표의 값이다.
- DB 세션 시간 한도는 브리지와 같다(`lock_timeout` 500ms, `statement_timeout` 1.5초). 잠금 대기를 넘기면 503 `BUSY` + `Retry-After: 1`. 화면은 같은 키로 다시 보낸다(주문 DB 설계 잠금 7001 절).

### 5.2 `POST /api/orders` (F-01)

**요청**
```
POST /api/orders
Idempotency-Key: <화면이 만든 UUID>
{"account_id": 1, "items": [{"product_id": 3, "qty": 2}, {"product_id": 5, "qty": 1}]}
```

**답**
- 202 `{"order_id": "O-20261125-0001", "status": "RECEIVED", "trip_ids": ["O-20261125-0001-1"]}`. 결과 변화는 WS로 간다.
- 같은 키 · 같은 본문으로 다시 오면 처음과 같은 202를 돌려주고 아무것도 쓰지 않는다.

| 거절 | 상태 코드 · `code` | 기록 |
|---|---|---|
| `Idempotency-Key` 없음, 본문 모양이 틀림, 품목이 비었거나 수량 < 1, 같은 상품이 두 번 | 400 `BAD_REQUEST` | 없음 |
| 모르는 계정 | 404 `UNKNOWN_ACCOUNT` | 없음 |
| 모르는 상품 | 422 `UNKNOWN_PRODUCT` | 없음 |
| 품목 합 > `max_order_items`(8) | 422 `OVER_MAX_ITEMS` | 없음 |
| 상품마다 주문 수량 > `stock_qty` | 422 `OUT_OF_STOCK`, `detail`에 상품별 주문 수 · 재고 | 주문은 쓰지 않음. `LOW_STOCK` 알림을 열거나 횟수를 올림(주문 DB 설계 4.8) |
| 같은 키인데 본문이 다름 | 409 `IDEMPOTENCY_CONFLICT` | 없음 (IR-20) |
| 시험 모드 | 503 `MAINTENANCE` | 없음 |
| 잠금 대기 초과 | 503 `BUSY` + `Retry-After: 1` | 롤백 |

**처리 순서** (한 트랜잭션)
1. `SELECT pg_advisory_xact_lock_shared(7001)` (V2 접수 부분 확정)
2. `orders.idem_key`로 찾음. 있으면 저장된 계정 · 품목과 본문을 비교한다. 같으면 처음 답을 만들어 돌려주고, 다르면 409.
3. 계정 · 상품 · `max_order_items` · 재고를 검사한다. 재고는 `product.stock_qty`를 잠금 없이 읽는다.
4. 주문 번호를 매긴다(5.4). `orders`(`idem_key` 포함) · `order_item`을 넣는다.
5. 운반 건으로 나눈다(5.3). 운반 건마다 `trip` · `trip_item` · `trip_dispatch` 한 줄을 넣는다.
6. 접수 이력: 운반 건마다 한 줄 + 주문 한 줄(`status_history`, actor `USER`). 1단계 `tools/trips.py`와 같은 모양.
7. 커밋한다. 그 뒤 브리지의 넘기기를 깨우고(6.2), WS `stock`을 보낸다(7.4).

재고 초과 거절일 때는 2 ~ 6을 롤백하고, `LOW_STOCK` 알림만 따로 짧은 트랜잭션으로 쓴다.

### 5.3 운반 건으로 나누기 (설계자 판단)

- 요청의 품목 순서대로 바구니를 `basket_capacity`(지금 4)까지 채우고, 차면 다음 운반 건을 연다. 한 상품이 두 운반 건에 걸쳐 나뉠 수 있다.
  - 예: A 3개 · B 3개 → `-1`: A 3 · B 1, `-2`: B 2
- `trip.seq`는 1부터. `trip_id`는 `{order_id}-{seq}`(주문 DB 설계 4.6).
- `basket_capacity`는 접수 트랜잭션 안에서 `app_setting`에서 읽는다.

### 5.4 주문 번호

- 모양은 1단계와 같다: `O-YYYYMMDD-NNNN`.
- 접수는 7001 공유라 동시에 여러 개가 들어올 수 있다. 그래서 "오늘의 최댓값 + 1"로 매긴 뒤 `orders` 넣기를 SAVEPOINT 안에서 하고, 기본 키 중복(23505)이면 다시 매긴다(최대 3번). 시퀀스를 새로 만들지 않는다(설계자 판단).
- 다만 최댓값 + 1만으로는 동시 접수가 계속 같은 번호를 잡아 늦게 온 쪽이 다시 매기기를 다 쓸 수 있다(시험에서 재현). 그래서 번호를 매기기 직전에 `pg_advisory_xact_lock(7002)`를 잡고 커밋까지 쥔다. 잠금 순서는 7001 공유 → 7002 → `orders`이고 7002를 7001보다 먼저 잡는 곳이 없어 마감과 교착하지 않는다. 위의 SAVEPOINT 다시 매기기는 안전망으로 남긴다.

### 5.5 `GET /api/snapshot` (F-02)

| `view` | 내용 |
|---|---|
| `customer` (`account_id` 필수) | `orders`: 그 계정의 주문(끝나지 않은 것 + 최근 24시간 안에 끝난 것)의 `{order_id, status_text, created_at, items: [{product_id, qty}]}`. `status_text`는 고객 말(7.3), `qty`는 `order_item`의 주문 수량. `stock`: 7.4 `stock`과 같은 모양. `limits`: `{max_order_items}`(`app_setting`). `notice`: 시험 모드면 `MAINTENANCE` |
| `admin` | `orders`: 끝나지 않은 것 + 최근 24시간 안에 끝난 것의 `{order_id, status, created_at, items: [{product_id, qty}]}`. `trips`: 같은 주문들의 [계획] 5.2 `trip_changed` 모양. `robots`: 브리지가 가진 마지막 값. `work_state`: `{system_mode}`. `stock` |

WS에 연결했을 때 첫 메시지(`type: "snapshot"`)의 `data`도 같은 내용이다.

- `orders`는 `created_at` 순(같으면 `order_id` 순), `items`는 `product_id` 순이다. `created_at`은 ISO 8601 글(시간대 포함)이다.
- 품목 · `created_at`은 스냅샷에만 싣는다. WS `order_changed`는 그대로 `{order_id, status_text}` · `{order_id, status}`다. 새 주문의 품목은 화면이 202 답을 받을 때 보낸 본문에서 붙인다(백엔드 검토 목록 8.2 N-06 보충).
- 2026-11-26 화면 연결 요청 N-05 · N-06 · N-08을 받아 `items` · `created_at` · `limits`를 더했다(백엔드 검토 목록 8장).

### 5.6 화면 파일 (N-07)

- `create_app`이 화면 폴더 `pc/service_bridge/service_bridge/web/static/` 전체를 `StaticFiles(directory=…, html=True)`로 웹 뿌리(`/`)에 마운트한다. 모든 경로를 받는 마운트라 API · WS 경로를 다 등록한 **뒤** 맨 끝에 붙인다.
- `/customer/`는 고객 화면, `/admin/`은 관리자 화면, `/`는 `static/index.html`이 고객 화면으로 넘긴다(주소의 `?…`는 그대로). 끝 `/` 없이 오면 폴더로 넘긴다.
- 같은 출처라 CORS가 필요 없다.
- 설치: `setup.py` `package_data`로 패키지와 함께 깔린다. 화면 시험 폴더 `static/test/`는 깔지 않는다.

## 6. 브리지 쪽

### 6.1 실행 구성

```
main()
  rclpy.init(신호 처리는 uvicorn에 맡김)
  동기 풀(1단계 그대로, 4개) · BridgeNode · MultiThreadedExecutor(4) → 뒷 스레드에서 spin
  FastAPI 앱(웹 전용 동기 풀 4개 + LISTEN 전용 비동기 연결 1개)을 uvicorn으로 메인 스레드에서 실행.
    DB 읽기 · 쓰기는 동기 풀로 하고, 비동기 처리(WS · LISTEN)에서는 asyncio.to_thread로 부른다
끌 때: uvicorn 멈춤(앱 lifespan이 WS 닫기 · LISTEN 멈춤) → 1단계 순서(서버 없앰 → 실행기 → 노드 → 브리지 풀) → 웹 풀
```

- 웹 ↔ 브리지는 같은 프로세스 안에서 두 가지만 주고받는다.
  - 웹 → 브리지: "넘기기 깨우기"(rclpy `GuardCondition.trigger()`, 어느 스레드에서 불러도 됨)
  - 브리지 → 웹: "로봇 상태 새 값"(`loop.call_soon_threadsafe`)
- 웹 코드는 ROS를 부르지 않고, ROS 콜백은 웹을 기다리지 않는다(IR-31).
- 듣는 주소 · 포트: 환경 변수 `IPONIC_WEB_HOST`(기본 `0.0.0.0`) · `IPONIC_WEB_PORT`(기본 `8000`).

### 6.2 운반 건 넘기기 (F-04, SB-04)

- 깨우는 때: 1초 타이머 + 접수 커밋 뒤 깨우기. 전용 콜백 그룹(상호 배제) 하나에서 돈다.
- 한 번에 하나만, **접수 순서대로** 보낸다. 보내는 중이면 깨워도 건너뛴다. 맨 앞 건이 확정 답을 받기 전에는 뒤 건을 보내지 않는다(사용자 결정 2026-11-25, 가 안). 그래서 central 주문판 순서가 접수 순서와 같다(명세 #21).
- "접수 순서"는 `trip.created_at`(접수 트랜잭션 시작 시각), 같으면 `trip_id` 순이다. 번호는 7002를 잡은 뒤 매기므로 주문 번호 순과 밀리초 단위로 다를 수 있다.
- 한 번의 차례:
  1. 넘기기가 쉬는 중(아래 3)이면 아무것도 하지 않는다. 아니면 짧은 읽기 트랜잭션: `central_acked_at IS NULL`인 줄 중 `trip.created_at`(같으면 `trip_id`)이 가장 이른 것 하나와 그 품목. 커밋하고 연결을 돌려준다.
  2. `SubmitTrip`을 `call_async`로 부른다. 칸: `trip_id`, `order_id`, `trip_index` = `seq`, `trip_count` = 그 주문의 운반 건 수, `items`, `submitted_at` = `trip.created_at`. 시간 한도 2초.
  3. 답을 받으면 짧은 쓰기 트랜잭션.
     - 확정 답(`accepted` 참 · 거짓): `central_acked_at = now()`, `last_result`(`ACCEPTED` 또는 받은 `reason`), `attempts + 1`.
     - `NOT_READY` · 시간 초과 · 서비스 없음: `attempts + 1`, `last_result`. 1 ~ 4번째 실패 뒤에는 약 1초 뒤 같은 건을 다시 보낸다.
     - 5번째 실패부터: 그 줄에 `unknown_since`(처음 한 번) · `next_attempt_at = now() + 30초`를 쓰고, **넘기기 전체가 30초 쉰다**. 쉬고 나면 맨 앞 건(같은 건)부터 다시 보낸다. 30초마다 다시 실패해도 같은 건에 머문다.
     - 5번째 실패 때 **관리자 알림을 연다**(6.5). 그 건이 확정 답을 받으면 알림을 자동으로 해결한다.
  4. 다음 빈 줄이 있으면 곧바로 이어서 보낸다.
- 1 ~ 4회 실패 사이 간격은 IR-40 "요청" 줄(1초 간격, ±20% 지연, IR-41)을 따른다. 기다림은 메모리의 "다음 보낼 시각"으로 하고 콜백 안에서 잠들지 않는다(IR-31). `next_attempt_at`은 30초 쉼을 남기는 데만 쓴다(재시작 뒤에도 이어 감).
- central이 `accepted=false`로 거절한 건의 처리는 다음 주에 정한다. 지금 간이 central은 거절하지 않는다.
- 5번 뒤 30초 간격은 SB-12(결과 모름 처리)를 정하기 전의 임시 값이다(설계자 판단). 코드에 `TODO(SB-12)`.
- 켜질 때: 1단계 켜질 때 순서 끝에 "남은 `trip_dispatch` 다시 보내기"를 더한다([SB] 4.4 7).

### 6.3 로봇 상태 (F-11 → F-12)

- `/fleet/robots`(LATCHED)를 구독하고, 콜백에서 화면 칸만 골라 바꾼다. 칸: `robot_id` · `fsm_state` · `fault` · `battery` · `x` · `y` · `trip_id`(`RobotStatus.order_id`).
- 마지막 값 하나만 둔다. 새 값이 오면 `call_soon_threadsafe`로 웹에 넘긴다.
- 웹은 0.2초마다 새 값이 있을 때만 `/ws/admin`에 `robots`를 보낸다(초당 5번 이하, 명세 "초당 2 ~ 5번").
- 로봇 상태는 DB에 쓰지 않는다.

### 6.4 `ReportTrip.stamp` 남기기 (E8)

`apply_report`가 `LATE_REPORT` 알림 `detail`에 받은 `stamp`(ISO 문자열)를 더한다. 인터페이스 변경은 없다. 1단계 `ReportIn`에 `stamp` 칸을 더한다.

### 6.5 넘기기 멈춤 알림 (사용자 결정 2026-11-25)

앞 건에 막혀 뒤 건이 모두 기다리므로, 관리자가 알아야 한다.

- 여는 때: 같은 운반 건이 5번 연속 실패했을 때(6.2). 넘기기가 쉬는 동안 계속 같은 알림 하나다.
- `alert` 한 줄 (주문 DB 설계 4.8):

  | 칸 | 값 |
  |---|---|
  | `level` | `WARN` |
  | `code` | `DISPATCH_STALLED` (웹 서비스 코드. `LOW_STOCK` · `LATE_REPORT`와 같은 갈래) |
  | `source` | `service_bridge` |
  | `order_id` · `trip_id` | 막힌 운반 건 |
  | `message` | "운반 건을 central에 넘기지 못하고 있습니다 (뒤의 운반 건도 기다림)" |
  | `detail` | `{attempts, last_result, unknown_since, waiting_trips}` (`waiting_trips` = 뒤에서 기다리는 운반 건 수) |

- 같은 운반 건에 열린 `DISPATCH_STALLED`는 하나만: 부분 UNIQUE 인덱스 `(code, trip_id) WHERE status = 'OPEN' AND code = 'DISPATCH_STALLED'`(`LOW_STOCK`과 같은 방식). 다시 실패할 때는 `detail`의 `attempts` · `last_result`만 고친다.
- 해결: 그 운반 건이 확정 답을 받으면 `status = 'RESOLVED'`, `resolved_at = now()`, `resolved_by` NULL(자동).
- 이번 주에 관리자가 아는 길: **WARN 로그**와 DB `alert` 행. 관리자 화면이 아직 없으므로 WS로는 보내지 않는다.
- 고도화 때: `alert` 행이 생기거나 해결되면 `ws_event` 트리거가 이미 사건을 남기므로, 웹이 `/ws/admin`에 `alert_raised` · `alert_resolved`로 보내는 쪽만 만들면 된다(7.5, `TODO(Q-07)`).

## 7. WS

### 7.1 겉모양과 연결

- 명세 3.1 그대로: `{type, seq(사건만), ts, data}`. `ts`는 보내는 시각(ISO).
- `/ws/customer?account_id=1`: 모르는 계정이면 닫는다(코드 4404).
- 연결하면 첫 메시지로 `snapshot`(5.5)을 보낸다. 시험 모드면 고객에게 이어서 `customer_notice` `{kind: "MAINTENANCE", message: "점검 중"}`을 보낸다.
- 받는 쪽이 느려 보낼 줄이 쌓이면(연결마다 100개) 그 연결을 닫는다. 화면은 다시 연결해 스냅샷부터 받는다.

### 7.2 사건 전달 (LISTEN)

```
DB 트랜잭션 커밋 → NOTIFY ifp_events {"seq", "t", "id", "status"}
   → 웹 LISTEN 전용 연결 → ws_event 그 seq 줄 읽기 → 종류별로 보냄
```

| `ws_event.type` | 관리자 | 고객 |
|---|---|---|
| `order_changed` | `{order_id, status}` | 주문의 `account_id`가 같은 연결에만 `{order_id, status_text}` |
| `trip_changed` | [계획] 5.2 모양 그대로 | 보내지 않음 |
| `alert` | 이번 주 보내지 않음, DEBUG 로그, `TODO(Q-07)` (7.5) | 보내지 않음 |
| 그 밖(`stock_close` 등) | 이번 주 보내지 않음, DEBUG 로그 | 보내지 않음 |

- 받은 순서대로 하나씩 처리한다.
- **LISTEN 연결이 끊기면**(검토 목록 N-01): 다시 연결한 뒤 모든 WS 연결에 스냅샷을 다시 보낸다. 끊긴 동안의 사건은 스냅샷이 대신한다. 다시 연결은 1초 → 2초 → 최대 5초 간격.

### 7.3 고객 말 (사용자 스토리보드 상태 대응표)

| 주문 상태 | `status_text` |
|---|---|
| `RECEIVED` | 접수됨 |
| `ASSIGNED` · `FILLING` · `FILLING_PAUSE` · `HELD` | 준비 중 |
| `TRANSPORTING` | 출고 중 |
| `SHIPPED` | 출고 완료 |
| `PARTIAL` | 일부 출고 |
| `CANCELLED` | 취소됨 |

대응표의 "거절됨"(R-08)은 아직 주문 상태 값에 없어 넣지 않는다. 표는 `core`의 한 곳에 둔다.

### 7.4 상태 메시지

| `type` | 보내는 때 | 받는 쪽 |
|---|---|---|
| `robots` | 6.3 | 관리자 |
| `work_state` `{system_mode}` | 연결할 때(스냅샷 안). 모드는 켤 때 정해져 바뀌지 않는다 | 관리자 |
| `stock` `{items: [{product_id, kind, name, stock_qty}]}` | 주문을 받을 때마다 ([계획] 5.2). 고객 · 관리자 스냅샷의 `stock`도 같은 모양이다. 화면은 `name`으로 상품 카드 이름 · 검색을, `kind`로 상품 이미지(`img/<kind>.png`)를 고른다 (2026-11-26 N-05) | 관리자 · 고객 |

### 7.5 알림 사건의 `data` (고도화 때 정함, `TODO(Q-07)`)

이번 주에는 알림 사건을 WS로 보내지 않는다(사용자 결정 2026-11-25). 받을 관리자 화면이 없고, 칸(Q-07)과 화면 방식(Q-08)을 함께 정하는 편이 맞다. 아래는 그때 쓸 참고 안이다.


- `alert_raised`: `{alert_id, level, code, message, order_id, trip_id, robot_id, product_id, created_at}`
- `alert_resolved`: `{alert_id, code, resolved_at}`
- `alert` 테이블 칸을 그대로 옮긴다. 비어 있는 칸은 `null`. `detail`(원본 이벤트)은 싣지 않는다. 화면이 필요하면 다음 주에 더한다.
- 이번 주에 열리는 알림은 `DISPATCH_STALLED`와 1단계의 `LATE_REPORT`, 재고 초과의 `LOW_STOCK`이다. 사건 기록(`ws_event`)은 계속 쌓이므로, 고도화 때 모두 이 길로 보낼 수 있다.
- 쌓기 · 묶기 · 해결 조건 같은 화면 쪽 방식(Q-08)은 이 칸과 따로 정한다.

## 8. DB 바꿀 것

### 8.1 `trip_dispatch` 테이블 (주문 DB 설계 4.16)

- `order_db.dbml` · `order_db.sql` · `order_db_extra.sql`에 넣는다.
  - 칸 여섯 개, FK → `trip`
  - CHECK `attempts >= 0`
  - 부분 인덱스 `(trip_id) WHERE central_acked_at IS NULL`
- `design/db/tests`에 제약 시험을 더한다.
- `alert`에 부분 UNIQUE 인덱스 `(code, trip_id) WHERE status = 'OPEN' AND code = 'DISPATCH_STALLED'`를 더한다(6.5).

### 8.2 `ws_event` 트리거

지금은 `type`이 테이블 이름(`orders` · `trip`)이고 `data`는 `{id, status}`뿐이다. [계획] 8장대로 [계획] 5.2 `data`를 채우게 고친다.

- `orders` → `type 'order_changed'`, `data {order_id, status, account_id}`. `account_id`는 고객 거르기용 내부 칸이다. 웹이 보내기 전에 지운다.
- `trip` → `type 'trip_changed'`, `data {trip_id, order_id, status, robot_id, items: [{product_id, qty_ordered, qty_placed, qty_missing}]}`
- `alert` · `stock_close`는 지금 그대로 둔다(`alert`는 `type 'alert'`, `data {id, status}`). 알림 사건 칸은 고도화 때 7.5로 정하면서 바꾼다.
- **트리거를 커밋 때 돌게 바꾼다**(`CREATE CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED`). 지금처럼 문장마다 돌면 두 가지가 생긴다.
  - 접수 때 `trip`을 넣은 순간에는 `trip_item`이 아직 없어 `items`가 빈다.
  - 보고 반영 때 `trip_item` 수량을 고치기 전 값이 실린다.
  - 커밋 때 돌면 그 트랜잭션의 마지막 값이 실린다. 단 DEFERRED 트리거의 `NEW` · `OLD`는 그 문장 때 값이므로, 트리거 안에서 `orders` · `trip` · `trip_item`을 **다시 읽어** `data`를 만든다.
  - "상태가 바뀔 때만 사건"이라는 비교는 지금처럼 **그 문장의** `OLD.status` · `NEW.status`로 한다. 바뀐 횟수만큼 사건을 남기고, 모두 커밋 때 값을 싣는다(설계자 판단). 한 트랜잭션에서 같은 줄의 상태가 두 번 바뀌는 길은 지금 없다(접수는 INSERT 한 번, `apply_report` · `Hello`는 운반 건마다 UPDATE 한 번, 주문 요약은 한 번). 그래서 겹친 사건이 나갈 일이 없고, 중복을 없애는 장치는 두지 않는다.
- NOTIFY payload는 지금처럼 `{seq, t, id, status}`만 싣는다(8000바이트 한도와 무관).
- 1단계 시험(`test_reports.py`)과 `design/db/tests/test_notify.py`의 `ws_event` 기대값을 새 모양에 맞춘다.

### 8.3 seed

`tools/seed.py`의 시험 계정을 고객 둘(`tester`, `tester2`)과 관리자 하나(`admin`, role `ADMIN`)로 늘린다. 고객 거르기를 시험하기 위해서다.

시험 상품은 실제 상품 다섯 개다(2026-11-26 사용자 결정, 각 재고 20). `kind`는 비전 클래스 이름과 같고 영문 소문자와 `_`만 쓴다: `mentos`(미니멘토스 레인보우) · `choco_bar`(자유시간 Mini) · `mygummy`(과즙팡 비타민 마이구미) · `choco_ball`(M&M's) · `bungeo_cake`(참붕어빵). 원본은 `seed.PRODUCTS`이고, `sim/fake_robots/scenarios`의 실물 시나리오는 두 팔에 같은 상품을 10개씩 둔다(팔별 배치는 비전이 정하므로 고정하지 않음).

## 9. 시험 도구

| 명령 | 하는 일 |
|---|---|
| `ros2 run service_bridge order --account 1 --items 3:2,5:1 [--key <UUID>]` | `POST /api/orders`를 부르고 답을 출력. 같은 `--key`로 두 번 부르면 같은 답이어야 함 |
| `ros2 run service_bridge ws_watch [--admin \| --account 1]` | WS에 붙어 받은 메시지를 한 줄씩 출력 (화면 대신) |
| `submit_trip` (1단계) | 남겨 둠. 웹 없이 브리지 · central만 볼 때 |

`order`는 표준 라이브러리 `urllib`로 부른다. `ws_watch`는 uvicorn이 함께 설치하는 `websockets`를 쓴다.

## 10. 시험

| 무엇 | 어떻게 | 확인 |
|---|---|---|
| 나누기 · 고객 말 · 본문 검사 | `core` 단위 시험 (DB 없음) | 5.3 예, 경계(4개 · 5개 · 8개 · 9개), 7.3 표 전부 |
| 주문 접수 | 실제 PostgreSQL 16 + FastAPI `TestClient` | 아래 사례 |
| 넘기기 | DB 시험 + 가짜 `SubmitTrip` 답 | 확정 답 → `central_acked_at`, `NOT_READY` · 시간 초과 → 같은 건 다시, 맨 앞 건이 막히면 뒤 건은 보내지 않음, 5번 뒤 전체 30초 쉼 · `unknown_since`, `DISPATCH_STALLED` 알림 행 하나(다시 실패해도 하나) · WARN 로그 → 확정 답이면 자동 해결. 알림 사건은 `ws_event`에 남지만 WS로는 나가지 않음 |
| 트리거 | `design/db/tests` | 커밋 때 한 줄, 마지막 값, 상태가 같으면 사건 없음, 롤백하면 사건 · NOTIFY 없음 |
| WS | `TestClient` WS | 첫 메시지 스냅샷, 고객 거르기, 관리자 · 고객 모양, LISTEN 끊김 → 스냅샷 다시, 느린 연결 닫기 |
| 한 바퀴 | rclpy 시험: 브리지(웹 포함) + central + `fake_fleet` + `order` + `ws_watch` | 목표 1 · 2 · 3 · 4 |

**주문 접수 사례**
- 정상: 202 → `orders` · `order_item` · `trip` · `trip_item` · `trip_dispatch` · 이력, `ws_event` `order_changed`(RECEIVED) · `trip_changed`(RECEIVED, items 채움)
- 6개 주문 → 운반 건 2개(4 + 2)
- 같은 키 · 같은 본문 → 같은 202, 줄 수 그대로
- 같은 키 · 다른 본문 → 409
- 재고 초과 → 422, 주문 없음, `LOW_STOCK` 알림 1개(두 번이면 횟수 2)
- 9개 → 422 `OVER_MAX_ITEMS`. 모르는 상품 · 계정 → 422 · 404
- 시험 모드 → 503 `MAINTENANCE`
- 다른 연결이 7001 배타를 쥠 → 약 500ms 뒤 503 `BUSY`
- 동시에 10건 → 주문 번호가 겹치지 않음

## 11. 화면 담당 · 관제 담당과 맞출 것

| 누구 | 항목 | 이 문서의 안 |
|---|---|---|
| 화면 | 주문 요청 본문 · 답 · 오류 `code` | 5.2 |
| 화면 | 스냅샷 · WS 첫 메시지 `snapshot`의 모양 | 5.5. 명세에 없어 이 문서가 정한다. 화면 연결 요청 N-05 · N-06 · N-08 반영(2026-11-26) |
| 화면 | 화면 파일 내주기 | 5.6 (N-07) |
| 화면 | 고객 구분 | 이번 주 `account_id`를 실음. 인증은 화면과 함께 정함 |
| 화면 | 알림 사건 `alert_raised` · `alert_resolved`의 `data` | 고도화 때. 참고 안은 7.5 |
| 관제 | `SubmitTrip`의 `trip_index` · `trip_count` · `submitted_at` | 6.2 |
| 관제 | 로봇 상태 | 이번 주 Pode만. 팔은 `RobotStatus`에 `kind`가 생긴 뒤 |

## 12. 정한 사람

**사용자가 정한 것 (2026-11-25)**
- 구현 순서 이름: "1단계-보고 반영 구현", "2단계-주문 접수 REST API/WS 구현"
- 이번 주에 바로 만들 수 있는 설계로, 화면은 뺌
- 고객 구분: 인증 없이 `account_id`를 실음(가 안)
- F-15 뺌(가 안)
- A 안(한 프로세스)
- SB-04 `trip_dispatch` 테이블 추가, V2 접수 부분(7001 공유), `basket_capacity` 이번 주 4
- 넘기기는 접수 순서를 지키고 앞 건이 막히면 뒤 건도 기다림(가 안). 대신 관리자 알림을 남김(6.5)
- 알림은 DB 행과 WARN 로그까지 이번 주에 만들고, WS로 보내기는 고도화 때(`TODO(Q-07)`)

**이 문서에서 제가 정한 것** (검토 때 확인 부탁드립니다)
- 나누는 순서: 요청 품목 순서대로 4개씩, 한 상품이 나뉠 수 있음(5.3)
- 주문 번호: 최댓값 + 1, 중복이면 다시(최대 3번), 시퀀스 안 만듦(5.4)
- 오류 상태 코드와 `code` 값(5.2)
- 재고 초과 때 `LOW_STOCK` 알림을 따로 짧은 트랜잭션으로 씀
- 5번 실패 뒤 30초 쉼(`TODO(SB-12)`)(6.2)
- `DISPATCH_STALLED` 알림의 칸 · 문구 · 자동 해결(6.5)
- LISTEN 끊김 → 스냅샷 다시 보내기(N-01), 느린 WS 연결 100개에서 닫기
- 스냅샷 범위: 끝나지 않은 것 + 최근 24시간 안에 끝난 것
- `ws_event` 트리거를 커밋 때 돌게(DEFERRED) 바꾸고, 표를 다시 읽어 마지막 값을 실음. 바뀐 횟수만큼 사건, 중복 제거 장치 없음(8.2, 검토자 질문 2)
- 시험 계정 셋(8.3), 웹 포트 기본 8000
- 시험 도구 `order` · `ws_watch`(9장)

## 13. 만드는 사람에게 맡기는 것

- 함수 · 모듈 이름, 파일 나누기(`core/` · `ros/` · 새 `web/`), 로그 문구
- FastAPI 라우터 구성, 응답 모델 정의 방식
- 비동기 시험 방식(`TestClient` · `pytest-asyncio`)과 한 바퀴 시험을 `launch_testing`으로 할지
- 새 의존성(fastapi · uvicorn[standard] · httpx) 설치 방법과 `docs/개발_규칙.md`에 적는 문구
