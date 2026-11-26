# 2단계-주문 접수 REST API/WS 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 쪽 입구와 출구를 만든다.
- 입구: HTTP 주문 접수 → 운반 건 나누기 → DB → 브리지가 central에 넘기기
- 출구: DB 상태 변화와 로봇 상태 → WS

**Architecture:**
- 한 프로세스(A 안)로 만든다. 메인 스레드는 FastAPI · uvicorn, 뒷 스레드는 1단계 `BridgeNode`의 rclpy 실행기다.
- 판단 코드는 ROS · 웹을 모르는 `core/`에 둔다. 웹은 `web/`, ROS는 `ros/`에 둔다.
- 웹 ↔ ROS는 프로세스 안에서 두 가지만 주고받는다.
  - 웹 → ROS: 넘기기 깨우기(`GuardCondition`)
  - ROS → 웹: 로봇 상태(`call_soon_threadsafe`)

**Tech Stack:** Python 3.12, rclpy (Jazzy), FastAPI · uvicorn[standard], psycopg 3 · psycopg_pool, PostgreSQL 16, pytest · httpx(`TestClient`)

**Spec:** `docs/superpowers/specs/2026-11-25-order-intake-api-ws-design.md` (2026-11-25 승인). 각 작업은 설계의 절 번호를 단다. 실행하는 사람은 설계와 이 계획을 함께 읽는다.

## Global Constraints

- **문서 · 답변 · 코드 주석**
  - 한국어로 쓴다.
  - 주석 머리는 1단계와 같은 `# ---- … ----` 모양이다.
- **DB**
  - 세션 시간 한도는 `lock_timeout` 500ms · `statement_timeout` 1.5초 · `idle_in_transaction_session_timeout` 10초다(`core/db.py`의 `SESSION_OPTIONS` 그대로).
  - 브리지 풀 4개(1단계)와 **웹 풀 4개(동기 `ConnectionPool`, `make_pool` 재사용)** 를 따로 둔다. LISTEN은 비동기 연결 하나를 쓴다.
- **잠금 순서:** 주문 접수 트랜잭션의 첫 문장은 `SELECT pg_advisory_xact_lock_shared(7001)`이다. 순서는 7001 → orders → trip.
- **설정 값**
  - `basket_capacity` · `max_order_items`는 `app_setting`에서 읽는다. 이번 주 값은 4 · 8이다.
- **넘기기 시간 값**
  - `SubmitTrip` 시간 한도는 2초다.
  - 1 ~ 4번째 실패 뒤에는 1초 ±20% 뒤 같은 건을 다시 보낸다.
  - 5번째 실패부터는 넘기기 전체가 30초 쉰다(`TODO(SB-12)`).
- **넘기기 순서:** 접수 순서(`trip.created_at`, 같으면 `trip_id`)대로 하나씩 보낸다. 맨 앞 건이 확정 답을 받기 전에는 뒤 건을 보내지 않는다.
- **알림:** `DISPATCH_STALLED` · `LATE_REPORT` · `LOW_STOCK` 알림은 DB 행과 WARN 로그까지 만든다. WS로는 보내지 않는다(`TODO(Q-07)`).
- **WS**
  - 겉모양은 `{type, seq(사건만), ts, data}`다.
  - 연결하면 첫 메시지는 `type: "snapshot"`이다.
  - 연결마다 보낼 줄이 100개 쌓이면 그 연결을 닫는다(코드 1013).
  - `robots`는 0.2초마다, 새 값이 있을 때만 보낸다.
- **고객 말 (설계 7.3):** `RECEIVED` → 접수됨. `ASSIGNED` · `FILLING` · `FILLING_PAUSE` · `HELD` → 준비 중. `TRANSPORTING` → 출고 중. `SHIPPED` → 출고 완료. `PARTIAL` → 일부 출고. `CANCELLED` → 취소됨.
- **실행 설정:** 웹 주소는 `IPONIC_WEB_HOST`(기본 `0.0.0.0`) · `IPONIC_WEB_PORT`(기본 `8000`). 운영 모드는 1단계처럼 `-p mode:=NORMAL|TEST`(기본값 없음)로 정한다.
- **ROS 콜백 규칙:** 콜백 안에서 DB 잠금을 쥔 채 기다리거나 잠들지 않는다(IR-31). `SubmitTrip`은 `call_async`로 부른다.
- **커밋 규칙**
  - 커밋은 사용자가 이 계획의 실행을 맡긴 범위에서만 한다.
  - 경로를 하나씩 적어 스테이징하고, `git add -A`는 쓰지 않는다.
  - `git -c user.name=ane -c user.email=brightbig01@gmail.com commit`, 끝 줄은 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - 푸시하지 않는다.
- **시험 명령**
  - core · 웹 시험: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q`
  - DB 시험: `~/venv/iponic/bin/python -m pytest design/db/tests -q`
  - 둘 다 시험 PostgreSQL(`design/db/compose.yaml`, 포트 55432)이 떠 있어야 한다.
  - core · 웹 시험은 먼저 `source /opt/ros/jazzy/setup.bash && source "$IPONIC_WS/install/setup.bash"`(기본 `~/iponic_ws`)를 한다. 안 하면 `test_bridge_node.py`의 모듈 수준 건너뛰기 때문에 전체가 `1 skipped`로 끝난다.
- **가상환경:** 공용 `~/venv/iponic` 하나만 쓴다. 패키지는 `pc/requirements.txt` 한 곳에 버전과 함께 적는다. 새 패키지가 필요하면 이 파일을 고치고 `~/venv/iponic/bin/pip install -r pc/requirements.txt`로 설치한다.

## Review Focus

1. **같은 키로 동시에 두 요청:** 화면이 시간 초과 뒤 곧바로 다시 보내 두 요청이 겹칠 수 있다. 주문은 하나만 생기고, 두 답 모두 같은 `order_id`여야 한다. `orders.idem_key` UNIQUE 위반(23505)이면 저장된 주문을 읽어 처음 답을 돌려준다 → Task 2 `test_same_key_concurrent`.
2. **WS 연결 중에 브라우저가 닫힘:** 보내던 중 연결이 끊겨도 다른 연결과 LISTEN 처리가 멈추지 않아야 한다 → Task 6 `test_dead_client_does_not_block_others`.
3. **브리지가 켜질 때 남은 `trip_dispatch`:** 넘기다가 브리지를 다시 켜도 남은 건이 접수 순서대로 다시 나가야 한다. 30초 쉼 중이었으면(`next_attempt_at`이 미래) 그 시각까지 기다린다 → Task 3 `test_rest_survives_restart`.
4. **상품 수량이 `basket_capacity`보다 큰 한 줄:** 예를 들어 A 7개. 운반 건 둘(4 + 3)로 나뉘어야 하고 빈 운반 건이 생기면 안 된다 → Task 2 `test_split_single_line_over_capacity`.
5. **고객 WS에 남의 주문:** 두 고객이 동시에 붙어 있을 때 `order_changed`가 주인에게만 가야 한다 → Task 6 `test_customer_sees_only_own_orders`.

---

### Task 0: 공용 가상환경 패키지 목록 (완료, 이 세션에서 직접)

**Files:**
- Create: `pc/requirements.txt` (psycopg[binary,pool] 3.3.9 · psycopg-pool 3.3.3 · fastapi 0.165.0 · uvicorn[standard] 0.54.0 · httpx 0.28.1 · pytest>=7.4)
- Modify: `docs/개발_규칙.md` ("주문 처리(브리지) 개발 환경"의 설치 줄을 `pip install -r pc/requirements.txt`로)

확인: `~/venv/iponic`에 설치한 뒤 기존 시험 `pc/service_bridge/test` 117 passed, `design/db/tests` 118 passed.

### Task 1: DB — `trip_dispatch` 테이블, 알림 인덱스, `ws_event` 트리거 (설계 8.1 · 8.2 · 6.5)

**Files:**
- Modify: `design/db/order_db.dbml` (`trip_dispatch` Table, Ref → trip)
- Modify: `design/db/order_db.sql` (`CREATE TABLE "trip_dispatch"`)
- Modify: `design/db/order_db_extra.sql`
  - CHECK, 부분 인덱스 둘
  - 트리거: `fn_notify_change`를 바꾸고 orders · trip · alert를 `CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED`로
- Modify: `design/db/tests/test_schema.py` (`TABLES`에 `trip_dispatch`)
- Modify: `design/db/tests/test_notify.py`
- Create: `design/db/tests/test_trip_dispatch.py`
- Modify: `design/db/README.md` (테이블 수 · 트리거 설명 한 줄씩)

**Interfaces:**
- Produces:
  - `trip_dispatch(trip_id text PK FK→trip, attempts integer NOT NULL DEFAULT 0 CHECK >= 0, next_attempt_at timestamptz, unknown_since timestamptz, last_result text, central_acked_at timestamptz)`
  - 인덱스: `ux_trip_dispatch_pending ON trip_dispatch (trip_id) WHERE central_acked_at IS NULL`, `ux_alert_dispatch_stalled_open ON alert (code, trip_id) WHERE status = 'OPEN' AND code = 'DISPATCH_STALLED'`(UNIQUE)
  - `ws_event` 줄:
    - `type 'order_changed'`, `data {order_id, status, account_id}`
    - `type 'trip_changed'`, `data {trip_id, order_id, status, robot_id, items: [{product_id, qty_ordered, qty_placed, qty_missing}]}` (items는 `product_id` 순)
    - `type 'alert'`, `data {id, status}` (지금 그대로)
  - NOTIFY payload `{seq, t, id, status}`. `id`는 order_changed면 `order_id`, trip_changed면 `trip_id`다.

- [ ] **Step 1: 실패하는 시험 쓰기**

`test_notify.py`의 기대값을 바꾸고 아래를 더한다(이름 · 단언).
```python
def test_trip_status_change_notifies(conn, conn_factory):
    # ... set_trip_status(conn, tid, "FILLING", placed={pid: 1})
    assert _no_seq(got) == [{"t": "trip_changed", "id": tid, "status": "FILLING"}]
    typ, data = conn.execute("select type, data from ws_event where seq = %s", (got[0]["seq"],)).fetchone()
    assert typ == "trip_changed" and data["items"] == [
        {"product_id": pid, "qty_ordered": 1, "qty_placed": 1, "qty_missing": 0}]

def test_insert_in_one_tx_carries_items(conn_factory):
    # 한 트랜잭션: orders → trip(RECEIVED) → trip_item 순서로 넣고 커밋
    # → trip_changed 한 줄, data["items"]가 비지 않음. order_changed data에 account_id

def test_rollback_writes_no_event(conn_factory): ...  # ws_event 줄 수 그대로, NOTIFY 없음

def test_alert_event_shape_unchanged(conn): ...       # type 'alert', data {id, status}
```
`test_trip_dispatch.py`:
```python
def test_attempts_must_be_non_negative(conn): ...      # attempts = -1 → CheckViolation
def test_dispatch_row_needs_trip(conn): ...            # 없는 trip_id → ForeignKeyViolation
def test_one_open_dispatch_stalled_per_trip(conn): ... # 같은 trip_id로 OPEN 두 줄 → UniqueViolation, RESOLVED 뒤 새로 열기는 됨
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest design/db/tests/test_notify.py design/db/tests/test_trip_dispatch.py design/db/tests/test_schema.py -q`
Expected: FAIL (`trip_dispatch` 없음, type이 `trip`)

- [ ] **Step 3: DDL · 트리거 고치기**

- `fn_notify_change`의 상태 비교는 지금처럼 그 문장의 `OLD.status` · `NEW.status`로 한다. `data`는 트리거 안에서 표를 **다시 읽어** 만든다.
  - orders: `orders.account_id`
  - trip: `trip` · `trip_item`
- 세 트리거를 `CREATE CONSTRAINT TRIGGER tg_… AFTER INSERT OR UPDATE ON … DEFERRABLE INITIALLY DEFERRED FOR EACH ROW`로 바꾼다.
- `stock_close`는 지금 트리거 그대로 둔다.

- [ ] **Step 4: 시험 통과 확인**

Run: `~/venv/iponic/bin/python -m pytest design/db/tests -q`
Expected: PASS (모두)

- [ ] **Step 5: 1단계 시험이 그대로인지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q -k "not loop"`
Expected: PASS. 실패하면 `ws_event` 모양을 기대하는 곳만 새 모양에 맞춘다.

- [ ] **Step 6: 커밋**

```bash
git add design/db/order_db.dbml design/db/order_db.sql design/db/order_db_extra.sql design/db/README.md design/db/tests/test_schema.py design/db/tests/test_notify.py design/db/tests/test_trip_dispatch.py
git commit -m "feat(db): trip_dispatch 테이블, DISPATCH_STALLED 알림 인덱스, ws_event를 커밋 때 마지막 값으로"
```

---

### Task 2: core — 주문 접수 (설계 5.2 · 5.3 · 5.4)

**Files:**
- Modify: `pc/service_bridge/service_bridge/core/types.py`
- Create: `pc/service_bridge/service_bridge/core/intake.py`
- Modify: `pc/service_bridge/service_bridge/tools/seed.py` (계정 셋)
- Create: `pc/service_bridge/test/test_intake.py`

**Interfaces:**
- Consumes: Task 1의 `trip_dispatch`
- Produces (`core/types.py`):
  ```python
  @dataclass(frozen=True)
  class OrderLine: product_id: int; qty: int
  @dataclass(frozen=True)
  class OrderRequest: account_id: int; items: tuple[OrderLine, ...]
  @dataclass(frozen=True)
  class TripPlan: seq: int; items: tuple[OrderLine, ...]
  @dataclass(frozen=True)
  class PlaceOk: order_id: str; trip_ids: tuple[str, ...]; replayed: bool = False
  @dataclass(frozen=True)
  class PlaceError: status: int; code: str; message: str; detail: dict = field(default_factory=dict)
  ```
- Produces (`core/intake.py`):
  - `split_trips(items: Sequence[OrderLine], capacity: int) -> list[TripPlan]`
  - `check_shape(req: OrderRequest) -> PlaceError | None`: 400 `BAD_REQUEST`(품목 없음 · 수량 < 1 · 같은 상품 두 번)
  - `place_order(conn: psycopg.Connection, req: OrderRequest, idem_key: str) -> PlaceOk | PlaceError`: 트랜잭션은 이 함수가 연다. `LockNotAvailable`(55P03)은 그대로 올린다(웹이 503 `BUSY`로 바꿈).
  - `account_exists(conn, account_id: int) -> bool`
- Produces (`tools/seed.py`): `seed(conn) -> dict`가 지금 키에 `"accounts": {"tester": id, "tester2": id, "admin": id}`를 더한다. `"account_id"`는 `tester`로 그대로 둔다.

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_split_in_request_order():
    plans = split_trips([OrderLine(1, 3), OrderLine(2, 3)], 4)
    assert plans == [TripPlan(1, (OrderLine(1, 3), OrderLine(2, 1))), TripPlan(2, (OrderLine(2, 2),))]

def test_split_single_line_over_capacity():          # Review Focus 4
    assert [p.items for p in split_trips([OrderLine(1, 7)], 4)] == [(OrderLine(1, 4),), (OrderLine(1, 3),)]

def test_split_boundaries():                          # 4개 → 1건, 5개 → 2건, 8개 → 2건
    ...

def test_check_shape_rejects(): ...                   # 빈 품목 · qty 0 · 같은 product_id 두 번 → status 400, code "BAD_REQUEST"

def test_place_order_writes_everything(conn, seeded):
    # 6개 주문 → PlaceOk, trip_ids == (f"{oid}-1", f"{oid}-2")
    # orders.idem_key == key, order_item 줄, trip(RECEIVED) 2줄, trip_item, trip_dispatch 2줄(attempts 0, acked NULL)
    # status_history: 운반 건 2 + 주문 1, actor 'USER'
    # ws_event: order_changed 1줄 + trip_changed 2줄(items 비지 않음)

def test_same_key_same_body_replays(conn, seeded):    # 두 번째 PlaceOk.replayed is True, 같은 order_id, 줄 수 그대로
def test_same_key_other_body_conflicts(conn, seeded): # PlaceError(409, "IDEMPOTENCY_CONFLICT")
def test_same_key_concurrent(pool, seeded):           # Review Focus 1: 스레드 둘이 같은 키로 동시에 → 같은 order_id, orders 1줄
def test_out_of_stock_rejects_and_counts_low_stock(conn, seeded):
    # 재고 20인 상품을 21개 → PlaceError(422, "OUT_OF_STOCK", detail={"items": [{"product_id": pid, "qty": 21, "stock_qty": 20}]})
    # orders 0줄, alert LOW_STOCK OPEN 1줄. 한 번 더 → 같은 줄, detail의 거절 횟수 2
def test_over_max_items(conn, seeded): ...            # 9개 → 422 "OVER_MAX_ITEMS"
def test_unknown_product(conn, seeded): ...           # 422 "UNKNOWN_PRODUCT"
def test_unknown_account(conn, seeded): ...           # 404 "UNKNOWN_ACCOUNT"
def test_waits_for_close_lock(pool, conn, seeded):
    # 다른 연결이 트랜잭션 안에서 pg_advisory_xact_lock(7001)을 쥠 → place_order가 약 0.5초 뒤 LockNotAvailable
def test_order_ids_unique_under_concurrency(pool, seeded): ...  # 스레드 10개 동시 → order_id 10개 모두 다름
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_intake.py -q`
Expected: FAIL (`core.intake` 없음)

- [ ] **Step 3: `core/types.py` · `core/intake.py` · `seed.py` 구현**

- `place_order`의 순서는 설계 5.2 "처리 순서" 1 ~ 6이다.
- 주문 번호
  - `O-YYYYMMDD-NNNN`을 오늘 최댓값 + 1로 매긴다.
  - `orders` INSERT는 SAVEPOINT(`conn.transaction()` 안의 중첩)로 감싼다.
  - 23505인데 제약이 `orders_pkey`면 번호를 다시 매긴다(최대 3번). 제약이 `idem_key`면 Review Focus 1 처리로 간다.
- 재고 초과: 큰 트랜잭션을 롤백한 뒤 **새 트랜잭션**에서 `LOW_STOCK`을 연다. 열려 있으면 `detail` 거절 횟수 · 마지막 거절 시각만 올린다(인덱스 `ux_alert_low_stock_open`).
- 같은 키 비교: 저장된 `orders.account_id`와 `order_item`의 `{product_id: qty}`를 요청과 비교한다. 순서는 보지 않는다.

- [ ] **Step 4: 시험 통과 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_intake.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/core/types.py pc/service_bridge/service_bridge/core/intake.py pc/service_bridge/service_bridge/tools/seed.py pc/service_bridge/test/test_intake.py
git commit -m "feat(service_bridge): 주문 접수 core — 나누기, 7001 공유, trip_dispatch, 같은 키 같은 결과"
```

---

### Task 3: core — 운반 건 넘기기 기록과 멈춤 알림 (설계 6.2 · 6.5)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/dispatch.py`
- Create: `pc/service_bridge/test/test_dispatch.py`

**Interfaces:**
- Consumes: Task 1 표, Task 2 `place_order`(시험 데이터)
- Produces (`core/dispatch.py`):
  ```python
  FAST_TRIES = 5; RETRY_SEC = 1.0; JITTER = 0.2; REST_SEC = 30.0
  @dataclass(frozen=True)
  class DispatchItem:
      trip_id: str; order_id: str; trip_index: int; trip_count: int
      items: dict[str, int]            # kind -> count (OrderItem)
      submitted_at: datetime           # trip.created_at
      attempts: int; next_attempt_at: datetime | None
  def head_item(conn) -> DispatchItem | None          # 맨 앞 빈 줄 하나 (created_at, trip_id 순). next_attempt_at과 상관없이 맨 앞
  def record_ack(conn, trip_id: str, accepted: bool, reason: str) -> None
  def record_failure(conn, trip_id: str, result: str) -> int   # 바뀐 attempts를 돌려줌
  def retry_delay(attempts: int, rand: Callable[[], float] = random.random) -> float | None
  ```
  - `record_ack`: `central_acked_at = now()`, `last_result = 'ACCEPTED'`(거절이면 `reason`), `attempts + 1`. 열린 `DISPATCH_STALLED`가 있으면 `RESOLVED` · `resolved_at = now()`로 바꾼다.
  - `record_failure`: `attempts + 1`, `last_result`. 바뀐 attempts가 5 이상이면 다음 일을 한다.
    - `unknown_since = coalesce(unknown_since, now())`, `next_attempt_at = now() + 30초`
    - `DISPATCH_STALLED`를 열거나 고친다. 칸은 설계 6.5 표이고, `detail.waiting_trips` = 맨 앞 건 뒤의 빈 줄 수다.
    - WARN 로그를 남긴다.
  - `retry_delay`: attempts < 5이면 `RETRY_SEC * (1 + JITTER * (2 * rand() - 1))`, 아니면 `None`(30초 쉼은 `next_attempt_at`으로).

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_head_is_oldest_pending(conn, seeded): ...        # 주문 두 개(-1, -2 포함) → 접수 순서 맨 앞, 확정된 건은 건너뜀
def test_head_item_fields(conn, seeded): ...             # trip_index == seq, trip_count == 주문의 운반 건 수, items는 kind 키
def test_ack_marks_and_resolves_alert(conn, seeded):
    # 5번 실패 → DISPATCH_STALLED OPEN 1줄 → record_ack(accepted=True) → central_acked_at NOT NULL, last_result 'ACCEPTED', 알림 RESOLVED
def test_four_failures_no_rest(conn, seeded): ...        # 4번 → attempts 4, next_attempt_at NULL, 알림 없음
def test_fifth_failure_rests_and_alerts(conn, seeded):
    # 5번째 → next_attempt_at ≈ now()+30s, unknown_since NOT NULL, alert(WARN, DISPATCH_STALLED, source 'service_bridge', trip_id, order_id)
    # detail == {"attempts": 5, "last_result": "TIMEOUT", "unknown_since": ..., "waiting_trips": 1}(뒤에 한 건 있을 때)
def test_more_failures_keep_one_alert(conn, seeded): ...  # 7번 → 알림 1줄, detail.attempts 7, unknown_since 처음 값 그대로
def test_rest_survives_restart(conn, seeded): ...        # Review Focus 3: 5번 실패 뒤 head_item → 같은 건, next_attempt_at이 미래
def test_retry_delay(): assert retry_delay(0, lambda: 0.5) == 1.0 and retry_delay(4, lambda: 0.0) == 0.8 and retry_delay(5) is None
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_dispatch.py -q`
Expected: FAIL (`core.dispatch` 없음)

- [ ] **Step 3: `core/dispatch.py` 구현** (각 함수가 자기 트랜잭션 하나. 잠금은 `trip_dispatch` 그 줄 `FOR UPDATE`만)

- [ ] **Step 4: 시험 통과 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_dispatch.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/core/dispatch.py pc/service_bridge/test/test_dispatch.py
git commit -m "feat(service_bridge): 운반 건 넘기기 기록, 5번 실패 뒤 30초 쉼과 DISPATCH_STALLED 알림"
```

---

### Task 4: core — 고객 말, 스냅샷, 사건 나누기 (설계 5.5 · 7.2 · 7.3 · 7.4)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/customer.py`
- Create: `pc/service_bridge/service_bridge/core/snapshot.py`
- Create: `pc/service_bridge/service_bridge/core/events.py`
- Create: `pc/service_bridge/test/test_snapshot_events.py`

**Interfaces:**
- Consumes: Task 1의 `ws_event` 모양, Task 2 `place_order`(시험 데이터)
- Produces:
  - `customer.STATUS_TEXT: dict[str, str]` (Global Constraints의 표 그대로), `customer.status_text(status: str) -> str`
  - `snapshot.stock(conn) -> dict`: `{"items": [{"product_id", "stock_qty"}]}` (product_id 순)
  - `snapshot.customer_snapshot(conn, account_id: int, mode: str) -> dict`: `{"orders": [{"order_id", "status_text"}], "stock": {...}, "notice": {"kind": "MAINTENANCE", "message": "점검 중"} | None}`
  - `snapshot.admin_snapshot(conn, mode: str, robots: list[dict]) -> dict`: `{"orders": [{"order_id", "status"}], "trips": [trip_changed data], "robots": robots, "work_state": {"system_mode": mode}, "stock": {...}}`
  - 두 스냅샷 모두 끝나지 않은 주문 + `finished_at`이 24시간 안인 주문이다. `created_at` 순.
  - `events.read_event(conn, seq: int) -> tuple[str, dict] | None`
  - `events.Outgoing(audience: Literal["admin", "customer"], account_id: int | None, message: dict)`
  - `events.route(event_type: str, seq: int, data: dict, ts: str) -> list[Outgoing]`
    - `order_changed`: 관리자 `{order_id, status}`, 그 계정 고객 `{order_id, status_text}`. `account_id`는 메시지에서 뺀다.
    - `trip_changed`: 관리자만, data 그대로.
    - 그 밖: `[]`.
  - `events.envelope(type: str, data: dict, ts: str, seq: int | None = None) -> dict`

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_status_text_covers_all_nine(): ...          # orders 상태 9개 모두 매핑, "FILLING_PAUSE" → "준비 중"
def test_route_order_changed():
    out = route("order_changed", 7, {"order_id": "O-1", "status": "FILLING", "account_id": 3}, "T")
    assert out == [Outgoing("admin", None, {"type": "order_changed", "seq": 7, "ts": "T", "data": {"order_id": "O-1", "status": "FILLING"}}),
                   Outgoing("customer", 3, {"type": "order_changed", "seq": 7, "ts": "T", "data": {"order_id": "O-1", "status_text": "준비 중"}})]
def test_route_trip_changed_admin_only(): ...
def test_route_alert_dropped(): assert route("alert", 1, {"id": "5", "status": "OPEN"}, "T") == []
def test_customer_snapshot_only_own_orders(conn, seeded): ...   # tester2 주문은 안 보임, TEST 모드면 notice MAINTENANCE
def test_admin_snapshot_shape(conn, seeded): ...                # trips 각 줄에 items, work_state.system_mode
def test_snapshot_excludes_old_finished(conn, seeded): ...      # finished_at 25시간 전 → 빠짐
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_snapshot_events.py -q`
Expected: FAIL

- [ ] **Step 3: 세 모듈 구현**

- [ ] **Step 4: 시험 통과 확인** (같은 명령, PASS)

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/core/customer.py pc/service_bridge/service_bridge/core/snapshot.py pc/service_bridge/service_bridge/core/events.py pc/service_bridge/test/test_snapshot_events.py
git commit -m "feat(service_bridge): 고객 말 · 스냅샷 · WS 사건 나누기 core"
```

---

### Task 5: ros — 넘기기 노드 부분, 로봇 상태 구독, `stamp` (설계 6.2 · 6.3 · 6.4)

**Files:**
- Create: `pc/service_bridge/service_bridge/ros/dispatcher.py`
- Modify: `pc/service_bridge/service_bridge/ros/bridge_node.py`
- Modify: `pc/service_bridge/service_bridge/ros/convert.py`
- Modify: `pc/service_bridge/service_bridge/core/types.py` (`ReportIn.stamp: str = ""`)
- Modify: `pc/service_bridge/service_bridge/core/reports.py` (`_late`의 detail에 `stamp`. `dataclasses.asdict`라 칸만 더하면 들어감)
- Create: `pc/service_bridge/test/test_dispatcher_node.py`
- Modify: `pc/service_bridge/test/test_convert.py`, `pc/service_bridge/test/test_reports.py`

**Interfaces:**
- Consumes: Task 3 `head_item` · `record_ack` · `record_failure` · `retry_delay`
- Produces:
  - `convert.robots(msg: RobotStatusArray) -> list[dict]`: 칸 `robot_id, fsm_state, fault, battery, x, y, trip_id`(`order_id` → `trip_id`)
  - `convert.submit_request(item: DispatchItem) -> SubmitTrip.Request`
  - `convert.report_in`이 `stamp`를 ISO 문자열(UTC)로 채운다.
  - `Dispatcher(node: Node, pool: ConnectionPool, contract)`
    - `start()`: 1초 타이머 + 가드 조건, 전용 `MutuallyExclusiveCallbackGroup`
    - `wake()`: 어느 스레드에서 불러도 됨
    - 한 번에 하나만 보낸다. 다시 보낼 시각은 메모리의 `_due`(monotonic)로 지킨다. 맨 앞 건의 `next_attempt_at`이 미래면 보내지 않는다.
  - `BridgeNode(pool, on_robots: Callable[[list[dict]], None] | None = None, **kwargs)`
    - `wake_dispatch() -> None`
    - `mode: str`(읽기 전용)
    - DB 첫 연결이 되면 1단계 3 · 4 다음에 `Dispatcher.start()`를 부른다(켜질 때 남은 줄 다시 보내기).
    - `/fleet/robots` 구독(contract 키 `robots`, LATCHED) → `on_robots(convert.robots(msg))`. `SubmitTrip` 클라이언트는 contract 키 `submit_trip`

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
# test_convert.py
def test_robots_renames_order_id(): ...            # RobotStatus(order_id="T-1") → {"trip_id": "T-1", ...} 칸 7개만
def test_report_in_carries_stamp(): ...            # stamp sec=1764000000 → "2025-11-24T16:00:00+00:00" 모양 ISO
# test_reports.py
def test_late_report_detail_has_stamp(conn, seeded): ...   # 허용 안 되는 전이 → alert.detail["stamp"] == ReportIn.stamp
# test_dispatcher_node.py (rclpy, 개인 도메인, 가짜 SubmitTrip 서버 노드)
def test_sends_in_order_one_at_a_time(pool, conn, seeded): ...   # 주문 2개 → 서버가 받은 trip_id 순서 == 접수 순서, 동시 진행 1개 이하
def test_head_blocks_followers(pool, conn, seeded): ...          # 서버가 첫 건에 NOT_READY 5번 → 둘째 건은 한 번도 안 옴, DISPATCH_STALLED 1줄
def test_wake_sends_without_waiting_timer(pool, conn, seeded): ... # place_order 뒤 wake() → 0.5초 안에 서버가 받음
def test_robots_callback(pool): ...                               # /fleet/robots 발행 → on_robots가 list[dict]로 불림
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_convert.py pc/service_bridge/test/test_reports.py pc/service_bridge/test/test_dispatcher_node.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

- 응답 콜백(future done)은 DB 짧은 트랜잭션 하나만 쓴다.
- `SubmitTrip` 답이 2초 안에 없으면 `record_failure(…, "TIMEOUT")`. 서비스가 준비되지 않았으면(`service_is_ready()` 거짓) `"NO_SERVICE"`.

- [ ] **Step 4: 시험 통과 확인** (같은 명령, PASS. 그 뒤 `pc/service_bridge/test` 전체도 PASS)

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/ros/dispatcher.py pc/service_bridge/service_bridge/ros/bridge_node.py pc/service_bridge/service_bridge/ros/convert.py pc/service_bridge/service_bridge/core/types.py pc/service_bridge/service_bridge/core/reports.py pc/service_bridge/test/test_dispatcher_node.py pc/service_bridge/test/test_convert.py pc/service_bridge/test/test_reports.py
git commit -m "feat(service_bridge): SubmitTrip 넘기기(접수 순서, 앞 건 막힘), /fleet/robots 구독, LATE_REPORT에 stamp"
```

---

### Task 6: web — API · WS · LISTEN (설계 5 · 7)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/__init__.py`
- Create: `pc/service_bridge/service_bridge/web/app.py`
- Create: `pc/service_bridge/service_bridge/web/hub.py`
- Create: `pc/service_bridge/service_bridge/web/listener.py`
- Create: `pc/service_bridge/test/test_web.py`

**Interfaces:**
- Consumes: Task 2 `place_order` · `check_shape` · `account_exists`, Task 4 `snapshot.*` · `events.*`
- Produces:
  - `create_app(pool: ConnectionPool, dsn: str, mode: str, wake_dispatch: Callable[[], None]) -> FastAPI`
  - `app.state.robots: RobotsBox`. `RobotsBox.put(robots: list[dict]) -> None`는 어느 스레드에서 불러도 된다(안에서 `call_soon_threadsafe`). `RobotsBox.latest() -> list[dict]`.
  - 경로(설계 5.2 · 5.5 · 7.1):
    - `POST /api/orders` (헤더 `Idempotency-Key`)
    - `GET /api/snapshot?view=customer&account_id=…` · `?view=admin`
    - `WS /ws/customer?account_id=…`(모르는 계정이면 4404로 닫음) · `WS /ws/admin`
  - `current_account(conn, account_id: int | None) -> int`: 고객 구분을 읽는 유일한 곳. 없거나 모르면 404 `UNKNOWN_ACCOUNT`.
  - `Hub`
    - `register(ws, audience, account_id) -> Client`, `unregister(client)`
    - `async deliver(outgoings: list[Outgoing])`
    - `async resend_snapshots()`
    - 연결마다 `asyncio.Queue(maxsize=100)`. 가득 차면 1013으로 닫는다.
  - `run_listener(dsn: str, on_seq: Callable[[int], Awaitable[None]], on_reconnect: Callable[[], Awaitable[None]], stop: asyncio.Event) -> Awaitable[None]`
    - `psycopg.AsyncConnection` autocommit으로 `LISTEN ifp_events`
    - 끊기면 1 → 2 → 5초 간격으로 다시 붙고, 붙은 뒤 `on_reconnect()`

- [ ] **Step 1: 실패하는 시험 쓰기** (`fastapi.testclient.TestClient`, `wake_dispatch`는 부른 횟수를 세는 가짜)

```python
def test_post_order_202(client, seeded): ...          # 202 {"order_id", "status": "RECEIVED", "trip_ids"}, wake 1번
def test_post_order_needs_key(client, seeded): ...    # 키 없음 → 400 {"code": "BAD_REQUEST"}
def test_post_order_maps_errors(client, seeded): ...  # 409 · 404 · 422 code가 core와 같음
def test_test_mode_rejects(client_test_mode, seeded): ... # 503 {"code": "MAINTENANCE"}
def test_busy_is_503(client, conn, seeded): ...       # 7001 배타를 쥔 채 → 503 {"code": "BUSY"}, 헤더 Retry-After: 1
def test_ws_first_message_is_snapshot(client, seeded): ...
def test_customer_sees_only_own_orders(client, seeded): ...  # Review Focus 5: tester · tester2 둘 다 붙고 tester가 주문 → tester만 order_changed(status_text "접수됨")
def test_admin_gets_trip_changed(client, seeded): ...
def test_customer_test_mode_notice(client_test_mode, seeded): ... # 스냅샷 다음 customer_notice MAINTENANCE
def test_stock_sent_after_order(client, seeded): ...  # 주문 뒤 admin · customer 모두 type "stock"
def test_robots_throttled(client, seeded): ...        # put 20번을 0.1초 안에 → admin이 받은 robots ≤ 2, 마지막 값과 같음
def test_listener_reconnect_resends_snapshot(client, seeded): ... # LISTEN 연결을 pg_terminate_backend → 5초 안에 snapshot 다시
def test_slow_client_closed(): ...                    # Hub 단위: 큐 100 넘기면 그 Client만 닫힘
def test_dead_client_does_not_block_others(client, seeded): ... # Review Focus 2: 한 WS를 끊은 채 주문 → 다른 WS는 order_changed 받음
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_web.py -q` (fastapi · uvicorn · httpx는 Task 0에서 설치함)
Expected: FAIL (`service_bridge.web` 없음)

- [ ] **Step 3: 구현**

- DB를 쓰는 경로 함수는 동기 `def`로 쓴다(FastAPI가 스레드 풀에서 실행). WS · LISTEN은 `async`로 쓴다.
- 주문이 202로 끝나면 `wake_dispatch()`를 부르고, `stock`을 관리자 · 고객 모두에게 보낸다.
- 사건 순서: LISTEN이 받은 `seq` 순서대로 하나씩 `read_event` → `route` → `deliver`.
- 알림 사건은 `route`가 `[]`이므로 나가지 않는다. 그 자리에 `TODO(Q-07)` 주석을 남긴다.

- [ ] **Step 4: 시험 통과 확인** (같은 명령, PASS)

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web pc/service_bridge/test/test_web.py
git commit -m "feat(service_bridge): 주문 접수 API · 스냅샷 · WS, LISTEN 사건 전달과 끊김 뒤 스냅샷"
```

---

### Task 7: 실행 · 시험 도구 · 한 바퀴 (설계 6.1 · 9 · 10)

**Files:**
- Modify: `pc/service_bridge/service_bridge/main.py`
- Create: `pc/service_bridge/service_bridge/tools/order.py`
- Create: `pc/service_bridge/service_bridge/tools/ws_watch.py`
- Modify: `pc/service_bridge/setup.py` (진입점 `order` · `ws_watch`)
- Modify: `pc/service_bridge/package.xml` (`python3-fastapi` 등은 rosdep 키가 없으면 주석으로 "venv pip" 표시)
- Create: `pc/service_bridge/test/test_web_loop.py`
- Modify: `pc/service_bridge/README.md` (명령 표에 `order` · `ws_watch`, 웹 포트)

**Interfaces:**
- Consumes: Task 5 `BridgeNode(on_robots=…)` · `wake_dispatch`, Task 6 `create_app` · `RobotsBox`
- Produces:
  - `main()`: 설계 6.1 순서
    - `rclpy.init(signal_handler_options=SignalHandlerOptions.NO)`
    - 실행기를 `threading.Thread(daemon=True)`에서 돌린다.
    - `uvicorn.Server(Config(app, host, port)).run()`을 메인 스레드에서 돌린다.
    - 끝나면 1단계의 끄기 순서를 따른다.
  - `order`: `--account`, `--items product_id:qty,…`, `--key`(없으면 새 UUID), `--url`(기본 `http://localhost:8000`). `urllib.request`로 부르고, 상태 코드와 본문을 출력한다. 202면 0, 그 밖이면 1을 돌려준다.
  - `ws_watch`: `--admin` 또는 `--account N`, `--url`. `websockets`로 붙어 한 줄에 메시지 하나씩 JSON으로 출력한다.

- [ ] **Step 1: 실패하는 시험 쓰기** (`test_web_loop.py`, 1단계 `test_loop.py`와 같은 틀, 도메인 58)

```python
def test_order_to_shipped_over_ws(pool, conn, seeded):
    # BridgeNode + CentralNode + FakeFleetNode(step_s=0.2, duplicate_every_report=True) 실행기, create_app을 TestClient로
    # 고객 WS 연결 → POST 주문(4개) → 고객 order_changed status_text 순서가
    #   ["접수됨", "준비 중", "출고 중", "출고 완료"] (같은 말 연속은 하나로 접어 비교)  # 목표 1
    # trip 상태 SHIPPED, (report_epoch, report_n) == fake_fleet 마지막 보고                 # 목표 2 (완료 기준 2)
    # 중복 보고는 DUPLICATE, status_history 줄 수가 보고 수와 같음                          # 목표 2 (완료 기준 3)
    # trip_dispatch.central_acked_at NOT NULL
def test_test_mode_blocks_orders(pool, seeded): ...  # mode TEST → POST 503 MAINTENANCE, 고객 WS customer_notice   # 목표 3
```

- [ ] **Step 2: 시험이 실패하는지 확인**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_web_loop.py -q`
Expected: FAIL

- [ ] **Step 3: `main.py` · 도구 · 설정 구현**

- [ ] **Step 4: 시험 통과 확인과 손으로 한 바퀴**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q` → PASS

손 확인(README 명령)
1. 브리지 `mode:=NORMAL`, `central`, `fake_fleet`을 켠다.
2. `ws_watch --account 1`을 켠다.
3. `order --account 1 --items <A의 id>:2`를 부른다.
4. `ws_watch`에 접수됨 → 출고 완료가 보이면 된다.

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/main.py pc/service_bridge/service_bridge/tools/order.py pc/service_bridge/service_bridge/tools/ws_watch.py pc/service_bridge/setup.py pc/service_bridge/package.xml pc/service_bridge/test/test_web_loop.py pc/service_bridge/README.md
git commit -m "feat(service_bridge): 웹과 브리지를 한 프로세스로 실행, 시험 도구 order · ws_watch, 한 바퀴 시험"
```

---

### Task 8: 이름 바꾸기 · 문서 맞춤 (설계 4장 "이름 바꾸기", 12장)

**Files:**
- Modify (구현 순서 뜻의 "조각"만 바꿈)
  - `pc/service_bridge/README.md`, `pc/service_bridge/service_bridge/core/reports.py` · `core/hello.py` 주석
  - `pc/central/README.md`, `pc/central/central/board.py` 주석
  - `docs/superpowers/specs/2026-11-25-bridge-slice1-design.md`, `docs/superpowers/plans/2026-11-25-bridge-slice1.md`: 본문 머리에 "새 이름: 1단계-보고 반영 구현" 한 줄을 넣고, 본문의 "첫 조각 · 두 번째 조각"을 바꾼다. 파일 이름은 그대로 둔다.
  - `docs/설계/백엔드_검토_목록.md`, `docs/superpowers/specs/2026-11-25-bridge-close-cross-review.md`, `docs/superpowers/specs/2026-11-18-order-db-design.md`, `docs/설계/서비스_브리지_기술_조사.md`
- 바꾸지 않음: `pc/fleet_core/**`의 경로 "조각"(다른 뜻), `docs/설계/ROS2_QoS_통신_안내.md`(구현 순서 뜻이 아니면)
- Modify: `docs/superpowers/specs/2026-11-25-order-intake-api-ws-design.md` 상태 줄 "구현됨 (커밋 범위)"

- [ ] **Step 1: 바꿀 곳 목록 확인**

Run: `git grep -n '조각' -- docs pc/service_bridge pc/central CLAUDE.md AGENTS.md`
Expected: 바꿀 줄과 그대로 둘 줄을 나눈 목록. 다른 뜻의 "조각"은 남긴다.

- [ ] **Step 2: 바꾸고 다시 확인**

Run: 같은 명령
Expected: 구현 순서 뜻의 "조각"이 0줄(옛 이름을 알려 주는 "옛 이름 …조각" 줄만 남음)

- [ ] **Step 3: 시험 전체**

Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test design/db/tests -q`
Expected: PASS

- [ ] **Step 4: 커밋**

```bash
git add <Step 2에서 바꾼 파일을 하나씩>
git commit -m "docs: 구현 순서 이름을 1단계 · 2단계로 (옛 조각)"
```
