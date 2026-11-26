# 서비스 브리지 1단계 구현 계획

> 새 이름: 1단계-보고 반영 구현 (옛 이름: 첫 조각)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 명령 하나로 넣은 운반 건이 central 주문판 → (가짜) fleet → `Hello` · `ReportTrip` → DB → `ReportOrder` → 주문판에서 빠짐까지 한 바퀴 돌게 한다.

**Architecture:** 판단 코드(`service_bridge/core`, `central/board.py`)는 ROS 없이 두고, ROS 노드는 받은 메시지를 바꿔 그 함수만 부른다. DB 쓰기는 `apply_report` · `apply_hello` 두 함수가 한 트랜잭션씩 맡는다. 웹(FastAPI)은 2단계에서 같은 프로세스에 얹으므로 이번에는 만들지 않는다.

**Tech Stack:** ROS 2 Jazzy · rclpy (`MultiThreadedExecutor`), Python 3.12, PostgreSQL 16 (`design/db/compose.yaml`), psycopg 3 + psycopg_pool, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-bridge-slice1-design.md` (이하 "설계"). 함께 볼 것: 주문 DB 설계 5절(허용 전이 표 · 주문 요약 규칙 4.4), 서비스 브리지 기술 조사 4.2 · 4.4.

## Global Constraints

- 작업 장소: 고정 저장소 `/home/ane/dev_ws/roscamp-repo-2`, 새 worktree를 만들지 않음. 브랜치 `feat/bridge-slice1`을 `docs/sb-confirm`에서 만듦(SB 확정 · 설계 문서가 거기 있음). 병합은 `dev`로 PR만.
- 이름 · 타입 · QoS는 contract에서 읽음: `fleet_core.common.contract.load_contract` · `to_ros_qos`, 키 `hello` · `report_trip` · `system_mode` · `submit_trip` · `order_board` · `report_order` (IR-01). 코드에 토픽 · 서비스 이름 문자열을 쓰지 않음.
- contract 경로: `get_package_share_directory("fleet_interfaces") + "/contract.yaml"` (`fleet_manager_node`와 같음).
- DB 주소: 환경 변수 `IPONIC_DB_DSN`, 없으면 `postgresql://postgres:ifp_test@localhost:55432/ifp_test` (설계 6.2 예와 같음).
- 연결 풀: min = max = 4, `timeout=0.5`, `max_waiting=8`, `check=ConnectionPool.check_connection`. 세션 옵션 `-c statement_timeout=1500 -c lock_timeout=500 -c idle_in_transaction_session_timeout=10000`.
- 요청마다 마감 = 받은 시각(`time.monotonic()`) + 2.5초. 교착(40P01) · 잠금 시간 초과(55P03)는 마감 안에서 한 번만 다시. 그 밖의 DB 예외와 두 번째 실패는 `accepted=false`, `reason="DB_ERROR"`.
- 잠금 순서(SB-05): `apply_report`는 `pg_advisory_xact_lock_shared(7001)` → `orders FOR UPDATE` → `trip FOR UPDATE`. `apply_hello`는 orders(`order_id` 순) → trip(`trip_id` 순), 7001 없음.
- `ReportTrip` 응답 값: `result` ∈ {`APPLIED`, `DUPLICATE`, `LATE_REPORT`}, `LATE_REPORT`의 `reason` ∈ {`NOT_ALLOWED`, `NOT_FOUND`, `INVALID_REPORT`}. `accepted=false`는 DB 오류뿐.
- 이력 actor: fleet 보고 · `Hello`의 `HELD`는 `SYSTEM`, `submit_trip`의 접수는 `USER`.
- 운반 건 사유 목록 = `status_history` CHECK의 13개(`order_db_extra.sql` 36-39행). 목록 밖은 상태를 반영하고 이력 `reason` NULL, `detail = {"reported_reason": 값}`, WARN 로그.
- `/system/mode`의 `mode` 매개변수는 `NORMAL` · `TEST`만, 기본값 없음.
- 실행기 스레드 4개. `Hello` · `ReportTrip` 서버는 서로 다른 `MutuallyExclusiveCallbackGroup`. 서버 콜백 안에서 다른 서비스를 부르거나 기다리지 않음(IR-31).
- 시험 네트워크: 개인 도메인 + `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`(IR-81). 도메인 6 금지.
- 줄 길이 100(`ruff.toml`). 문서 · 주석 · 로그는 한국어, 식별자는 영어.
- `setup.py`에 `tests_require=['pytest']` (개발 규칙).

## 시험 고르는 기준 (2026-11-25 사용자 요청: 필수 항목만)

완료 기준 2 · 3, 확정한 SB 결정(SB-01 · SB-03 · SB-05 · SB-06), fleet이 끝없이 다시 보내게 만드는 경우, 아래 Review Focus만 시험합니다. 같은 길을 두 번 확인하는 시험과 시험 도구 자체의 세부 시험은 두지 않습니다.

## Review Focus

1. `placed`에 같은 상품이 두 번 오는 보고(`[A:1, A:1]`) → 합쳐서 A 2로 본다 (Task 6 `test_report_in_sums_same_kind`).
2. 마감이 7001 배타 잠금을 잡고 있는 동안 온 보고 → 두 번 시도 뒤 2.5초 안에 `accepted=false` (Task 3 `test_exclusive_7001_gives_db_error_within_deadline`).
3. DB가 아직 안 떠 있을 때 브리지를 켬 → `/system/mode`는 나가고 `Hello` · `ReportTrip` 서버는 DB가 될 때까지 생기지 않음 (Task 6 `test_no_servers_until_db_ready`).
4. 빈 `boot_id`의 `Hello` → epoch를 올리지 않고 `accepted=false`, `INVALID_REQUEST` (Task 4 `test_empty_boot_id_rejected`).
5. 같은 `trip_id`로 내용이 다른 `SubmitTrip` → 처음 응답 그대로, 주문판 안 바뀜 (Task 5 `test_same_trip_id_other_items_keeps_first`).

---

### Task 1: 패키지 뼈대 · 개발 환경 · 시험 틀

**Files:**
- Create: `pc/service_bridge/package.xml`, `pc/service_bridge/setup.py`, `pc/service_bridge/setup.cfg`, `pc/service_bridge/resource/service_bridge`, `pc/service_bridge/service_bridge/__init__.py`, `pc/service_bridge/service_bridge/core/__init__.py`, `pc/service_bridge/service_bridge/core/types.py`, `pc/service_bridge/service_bridge/core/db.py`
- Create: `pc/service_bridge/test/conftest.py`, `pc/service_bridge/test/test_db.py`
- Modify: `docs/개발_규칙.md` (venv · psycopg 절차 한 절)

**Interfaces:**
- Produces (`core/types.py`, 모두 `@dataclass(frozen=True)`):
  - `ReportIn(trip_id: str, epoch: int, n: int, status: str, reason: str, robot_id: str, placed: dict[str, int], missing: dict[str, int])`
  - `ReportOut(accepted: bool, result: str = "", reason: str = "")`
  - `HelloIn(node: str, boot_id: str)` · `HelloOut(accepted: bool, epoch: int = 0, reason: str = "")`
- Produces (`core/db.py`): `dsn_from_env() -> str`, `make_pool(dsn: str, *, open: bool = True) -> ConnectionPool`, `SESSION_OPTIONS: str`
- Produces (`test/conftest.py`): 픽스처 `pool`(시험마다 새 DB에 붙은 `ConnectionPool`), `conn`(autocommit 연결), `seeded`(Task 7의 `seed`가 생기기 전까지는 `design/db/tests/helpers.py`의 `make_account` · `make_product`로 A · B · C · D 재고 20과 USER 계정을 넣고 `{"account_id": int, "products": {kind: product_id}}`를 돌려줌). 틀 DB 이름은 `sb_tmpl`(design/db 시험의 `ifp_tmpl`과 겹치지 않게), 방식은 `design/db/tests/conftest.py`와 같음.

- [ ] **Step 1: 개발 환경 준비**

```bash
python3 -m venv --system-site-packages ~/venv/iponic && ~/venv/iponic/bin/pip install 'psycopg[binary,pool]' pytest
```

`$IPONIC_WS/src/`에 이 저장소가 있어야 colcon이 찾습니다(없으면 `ln -s /home/ane/dev_ws/roscamp-repo-2 "$IPONIC_WS/src/roscamp-repo-2"`). 이 두 가지를 `docs/개발_규칙.md` "빌드" 절 아래 "주문 처리(브리지) 개발 환경" 소절로 적습니다.

- [ ] **Step 2: 실패하는 시험 쓰기** — `test/test_db.py`

```python
def test_pool_session_has_timeouts(pool):
    with pool.connection() as c:
        assert c.execute("show lock_timeout").fetchone()[0] == "500ms"
        assert c.execute("show statement_timeout").fetchone()[0] == "1500ms"
        assert c.execute("show idle_in_transaction_session_timeout").fetchone()[0] == "10s"
```

- [ ] **Step 3: 실패 확인**

Run: `docker compose -f design/db/compose.yaml up -d && ~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_db.py -q`
Expected: FAIL (`ModuleNotFoundError: service_bridge.core.db`)

- [ ] **Step 4: 구현** — `package.xml`은 `pc/central/package.xml` 모양, `exec_depend`에 `rclpy` · `fleet_interfaces` · `fleet_core` · `ament_index_python`. `conftest.py`는 `pc/service_bridge`를 `sys.path`에 넣어 colcon 없이도 `core` 시험이 돌게 함.

- [ ] **Step 5: 통과 확인** — 같은 명령, Expected: `1 passed`

- [ ] **Step 6: 커밋**

```bash
git add pc/service_bridge docs/개발_규칙.md
git commit -m "feat(bridge): service_bridge 패키지 뼈대, DB 연결 풀, 시험 틀"
```

---

### Task 2: 허용 전이 표 · 주문 요약 규칙 (순수 함수)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/transitions.py`
- Test: `pc/service_bridge/test/test_transitions.py`

**Interfaces:**
- Produces:
  - `STATUSES: frozenset[str]` (9개), `ACTIVE: frozenset[str]` = {`ASSIGNED`, `FILLING`, `FILLING_PAUSE`, `TRANSPORTING`}, `TERMINAL: frozenset[str]` = {`SHIPPED`, `PARTIAL`, `CANCELLED`}
  - `TRIP_REASONS: frozenset[str]` (Global Constraints의 13개)
  - `is_allowed(current: str, new: str) -> bool` — 설계 6.4의 5 · 주문 DB 설계 "허용 전이" 표. 같은 상태는 언제나 `True`.
  - `order_summary(trip_statuses: list[str]) -> str` — 주문 DB 설계 4.4의 규칙 1 ~ 4.

- [ ] **Step 1: 실패하는 시험 쓰기** — 표를 그대로 매개변수화합니다.

```python
ALLOWED = {
    "RECEIVED": {"ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL",
                 "HELD", "CANCELLED"},
    "ASSIGNED": {"FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD",
                 "CANCELLED", "RECEIVED"},
    "FILLING": {"FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD", "CANCELLED",
                "RECEIVED"},
    "FILLING_PAUSE": {"FILLING", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD", "CANCELLED",
                      "RECEIVED"},
    "TRANSPORTING": {"SHIPPED", "PARTIAL", "HELD"},
    "HELD": {"SHIPPED", "PARTIAL", "RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING"},
    "SHIPPED": set(), "PARTIAL": set(), "CANCELLED": set(),
}

@pytest.mark.parametrize("cur", sorted(ALLOWED))
@pytest.mark.parametrize("new", sorted(ALLOWED))
def test_transition_table(cur, new):
    assert is_allowed(cur, new) == (cur == new or new in ALLOWED[cur])

@pytest.mark.parametrize("trips, expected", [
    (["SHIPPED", "CANCELLED"], "PARTIAL"),          # 규칙 1
    (["RECEIVED", "RECEIVED"], "RECEIVED"),         # 규칙 2
    (["HELD", "TRANSPORTING"], "HELD"),             # 규칙 3
    (["SHIPPED", "RECEIVED"], "ASSIGNED"),          # 규칙 4: 시작한 주문의 남은 접수됨은 배정됨
    (["TRANSPORTING", "FILLING"], "FILLING"),       # 규칙 4: 가장 늦은 단계
    (["TRANSPORTING"], "TRANSPORTING"),             # 운반 건 하나는 그대로
])
def test_order_summary(trips, expected):
    assert order_summary(trips) == expected

def test_trip_reasons_match_db_check():
    sql = (REPO / "design/db/order_db_extra.sql").read_text(encoding="utf-8")
    block = sql.split("ck_status_history_reason")[1].split(";")[0]
    assert TRIP_REASONS == set(re.findall(r"'([A-Z_]+)'", block))
```

- [ ] **Step 2: 실패 확인** — `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_transitions.py -q`, Expected: FAIL (import)
- [ ] **Step 3: 구현** — 시험의 `ALLOWED`와 같은 표를 상수로 둠. `order_summary`의 "끝난 건"은 `TERMINAL`, 규칙 4의 순서는 `ASSIGNED < FILLING = FILLING_PAUSE < TRANSPORTING`.
- [ ] **Step 4: 통과 확인** — Expected: 모두 통과 (81 + 6 + 1)
- [ ] **Step 5: 커밋** — `git commit -m "feat(bridge): 허용 전이 표(SB-03)와 주문 요약 규칙"`

---

### Task 3: `apply_report` (F-09)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/reports.py`, `pc/service_bridge/service_bridge/core/retry.py`
- Test: `pc/service_bridge/test/test_reports.py`

**Interfaces:**
- Consumes: `ReportIn` · `ReportOut` (Task 1), `is_allowed` · `order_summary` · `TRIP_REASONS` · `TERMINAL` (Task 2), 픽스처 `pool` · `conn` · `seeded` (Task 1)
- Produces:
  - `retry.run_tx(pool: ConnectionPool, fn: Callable[[psycopg.Connection], T], deadline: float) -> T` — `pool.connection()` 한 번 = 트랜잭션 한 번. `DeadlockDetected` · `LockNotAvailable`이면 `time.monotonic() < deadline`일 때 한 번만 다시. 두 번째 예외는 그대로 올림.
  - `apply_report(pool: ConnectionPool, rep: ReportIn, deadline: float) -> ReportOut` — `run_tx`로 설계 6.4의 1 ~ 8을 하고, 모든 `psycopg.Error` · `PoolTimeout`을 잡아 `ReportOut(False, reason="DB_ERROR")`.
  - 시험 도우미 `make_trip(conn, account_id, products, items: dict[str, int]) -> str`(trip_id)는 `test_reports.py` 안에 둠(Task 7에서 `tools/trips.insert_trip`으로 바꿈).

- [ ] **Step 1: 실패하는 시험 쓰기** — 공통: `E = 7`, `rep(n, status, **kw)`는 `ReportIn(trip, E, n, status, "", "pode1", {}, {})` 기본값에 `kw`를 덮어씀. `D = time.monotonic() + 2.5`.

```python
def test_normal_flow_applies_four_reports(pool, conn, seeded):        # 완료 기준 2
    t = make_trip(conn, ..., {"A": 1, "C": 1})
    for n, st in enumerate(["ASSIGNED", "FILLING", "TRANSPORTING", "SHIPPED"], 1):
        placed = {"A": 1, "C": 1} if st in ("TRANSPORTING", "SHIPPED") else {}
        assert apply_report(pool, rep(n, st, placed=placed), D()) == ReportOut(True, "APPLIED")
    row = conn.execute("select status, report_epoch, report_n, finished_at is not null, "
                       "placed_at is not null, first_assigned_at is not null from trip "
                       "where trip_id=%s", (t,)).fetchone()
    assert row == ("SHIPPED", 7, 4, True, True, True)
    assert count(conn, "status_history where trip_id=%s", t) == 5       # 접수 1 + 보고 4
    assert order_status(conn, t) == "SHIPPED"
    assert count(conn, "ws_event") > 0

def test_same_report_twice_is_duplicate_and_changes_nothing(...):     # 완료 기준 3
    # (7,1) ASSIGNED 두 번 → 두 번째 ReportOut(True, "DUPLICATE"), trip.updated_at · 이력 수 그대로

def test_smaller_number_is_duplicate(...):        # (8,1) 뒤의 (7,3) → DUPLICATE, 상태 그대로
def test_terminal_then_other_status_is_late_report_once(...):
    # SHIPPED (7,4) 뒤 (7,5) FILLING → ReportOut(True, "LATE_REPORT", "NOT_ALLOWED"), 같은 요청 또 → 알림 1개
    # alert: code LATE_REPORT, level WARN, source fleet, event_id "LATE:{t}:7:5"
def test_unknown_trip_is_late_report_not_found(...):   # alert.trip_id IS NULL, detail에 trip_id
@pytest.mark.parametrize("kw", [dict(placed={"A": 2}), dict(placed={"Z": 1}),
                                dict(missing={"A": -1}), dict(n=2**31)])
def test_invalid_values_are_late_report(kw, ...):     # A 주문 1 → ReportOut(True, "LATE_REPORT", "INVALID_REPORT")
def test_unknown_reason_applies_and_keeps_value(...):
    # FILLING + reason "DISTANCE_STOP" → APPLIED, 이력 reason NULL, detail->>'reported_reason' = 'DISTANCE_STOP'
def test_db_down_gives_db_error():
    # 닫힌 풀(또는 없는 포트 DSN, open=False) → ReportOut(False, reason="DB_ERROR")
def test_exclusive_7001_gives_db_error_within_deadline(pool, conn_factory, seeded):  # Review Focus 2
    # 다른 연결이 begin; select pg_advisory_xact_lock(7001) 을 잡은 채 →
    # 결과 ReportOut(False, reason="DB_ERROR"), 걸린 시간 < 2.5초, 잠금을 풀면 같은 요청이 APPLIED
```

- [ ] **Step 2: 실패 확인** — `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_reports.py -q`, Expected: FAIL (import)
- [ ] **Step 3: `retry.run_tx` 구현** (`core/retry.py`)
- [ ] **Step 4: `apply_report` 구현** (`core/reports.py`) — 순서는 설계 6.4 그대로: `trip`에서 `order_id` 읽기(잠금 없음) → 7001 공유 → orders · trip `FOR UPDATE` → 번호 비교(`(trip.report_epoch, trip.report_n)`이 NULL이면 무엇보다 작음, 튜플 비교) → 값 검사 → `is_allowed` → 반영 또는 `LATE_REPORT`. `placed_at`은 그 보고의 `placed`가 비어 있지 않고 `placed_at`이 NULL일 때, `first_assigned_at`은 새 상태가 `ASSIGNED`이고 NULL일 때, `finished_at`은 새 상태가 `TERMINAL`일 때. 주문 요약이 바뀌면 `orders.status` · (끝이면) `finished_at`, `trip_id` 없는 이력 한 줄. 알림은 `insert … on conflict (event_id) do nothing`.
- [ ] **Step 5: 통과 확인** — Expected: 모두 통과
- [ ] **Step 6: 커밋** — `git commit -m "feat(bridge): apply_report — 번호 비교, 허용 전이, LATE_REPORT 알림 (F-09)"`

---

### Task 4: `apply_hello` (F-06)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/hello.py`
- Test: `pc/service_bridge/test/test_hello.py`

**Interfaces:**
- Consumes: `HelloIn` · `HelloOut`, `ACTIVE`, `order_summary`, `retry.run_tx`, `apply_report` (시험에서만)
- Produces: `apply_hello(pool: ConnectionPool, hello: HelloIn, deadline: float) -> HelloOut` — DB 예외 처리는 `apply_report`와 같은 감싸기를 씀(Task 3에서 시험했으므로 여기서 다시 시험하지 않음)

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_epoch_starts_at_1_and_same_boot_id_keeps_it(pool):
    assert apply_hello(pool, HelloIn("fleet", "b1"), D()) == HelloOut(True, 1)
    assert apply_hello(pool, HelloIn("fleet", "b1"), D()) == HelloOut(True, 1)
def test_restart_holds_active_and_filters_old_epoch(pool, conn, seeded):
    # epoch 1로 t1 TRANSPORTING (1,3), t2 RECEIVED, t3 SHIPPED → Hello("fleet","b2") = HelloOut(True, 2)
    # t1: HELD, report_epoch 2, report_n 0, 이력 reason FLEET_RESTART actor SYSTEM. t2 · t3 그대로
    # 그 뒤 ReportIn(t1, 1, 9, "SHIPPED") → DUPLICATE, ReportIn(t1, 2, 1, "ASSIGNED") → APPLIED
@pytest.mark.parametrize("hello, reason", [(HelloIn("central", "b1"), "UNKNOWN_NODE"),
                                           (HelloIn("fleet", ""), "INVALID_REQUEST")])
def test_bad_hello_rejected(pool, hello, reason):    # Review Focus 4. HelloOut(False, reason=reason), bridge_session 비어 있음
```

- [ ] **Step 2: 실패 확인** — `… pytest pc/service_bridge/test/test_hello.py -q`, Expected: FAIL (import)
- [ ] **Step 3: 구현** — 설계 6.3의 1 ~ 6. `HELD`로 쓸 운반 건은 `status = any(ACTIVE)` 전부, orders(`order_id` 순) → trip(`trip_id` 순) `FOR UPDATE`, 번호는 `(새 epoch, 0)`.
- [ ] **Step 4: 통과 확인** — Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(bridge): apply_hello — epoch, 같은 boot_id, FLEET_RESTART 보류 (F-06)"`

---

### Task 5: central 간이판 (F-04 서버 · F-05 · F-10)

**Files:**
- Create: `pc/central/central/board.py`, `pc/central/central/central_node.py`
- Modify: `pc/central/setup.py` (진입점 `central = central.central_node:main`, `tests_require`), `pc/central/package.xml` (`fleet_core` · `ament_index_python`), `pc/central/README.md` (이번 단계의 서비스 · 발행 목록으로 고침)
- Test: `pc/central/test/test_board.py`, `pc/central/test/test_central_node.py`

**Interfaces:**
- Produces (`board.py`, ROS 없음):
  - `BoardEntry(trip_id: str, items: dict[str, int], created_at: object)` (frozen dataclass)
  - `class Board`: `submit(trip_id: str, items: dict[str, int], created_at: object) -> tuple[bool, str]`, `report(order_id: str, status: str) -> bool`, `entries() -> list[BoardEntry]`
- Produces (`central_node.py`): `class CentralNode(Node)` — 노드 이름 `central`; `main()`

- [ ] **Step 1: 실패하는 시험 쓰기** — `test_board.py`

```python
def test_submit_keeps_arrival_order_and_first_answer():   # t1, t2 → [t1, t2]
def test_same_trip_id_other_items_keeps_first():     # Review Focus 5: 두 번째 submit도 (True, ""), items는 처음 것
def test_invalid_request_is_remembered():
    assert b.submit("", {"A": 1}, now) == (False, "INVALID_REQUEST")
    assert b.submit("t1", {}, now) == (False, "INVALID_REQUEST")
    assert b.submit("t2", {"A": 0}, now) == (False, "INVALID_REQUEST")
    assert b.submit("t1", {"A": 1}, now) == (False, "INVALID_REQUEST")   # 기억한 응답
@pytest.mark.parametrize("st", ["SHIPPED", "PARTIAL", "CANCELED", "HELD"])
def test_final_report_removes_once(st):              # report → True, entries 비고, 다시 report → True
def test_report_unknown_accepted_non_final_rejected():
    # report("zz", "SHIPPED") → True / report("t1", "FILLING") → False, 주문판 그대로
```

`test_central_node.py`(rclpy, `pytest.importorskip("rclpy")`, `test_system_mode.py`와 같은 방식): 노드를 만들면 `/central/orders` 발행자의 durability가 TRANSIENT_LOCAL이고 `/central/submit_trip` · `/central/report_order` 서비스가 있다.

- [ ] **Step 2: 실패 확인** — `cd "$IPONIC_WS" && colcon build --packages-select central && colcon test --packages-select central && colcon test-result --verbose`, Expected: FAIL (import)
- [ ] **Step 3: 구현** — `CentralNode`: 단일 스레드 실행기, `SubmitTrip` → `Board.submit`(`items`는 `OrderItem` 목록을 kind별 합), 접수되면 바로 발행. `ReportOrder` → `Board.report`, 빠지면 발행. 1초 타이머로 다시 발행. `OrderBoard(run_mode=MODE_RUN, orders=[Order(order_id=trip_id, items, created_at)], stamp=now)`. 모르는 `order_id`는 WARN 로그. `/system/mode` 구독은 바뀔 때 로그 한 줄.
- [ ] **Step 4: 통과 확인** — 같은 명령, Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(central): 간이 central — 운반 건 접수, 주문판 발행, 끝난 건 빼기 (F-04 · F-05 · F-10)"`

---

### Task 6: 브리지 노드 (F-06 · F-08 서버, F-13 발행)

**Files:**
- Create: `pc/service_bridge/service_bridge/ros/__init__.py`, `ros/convert.py`, `ros/bridge_node.py`, `service_bridge/main.py`
- Modify: `pc/service_bridge/setup.py` (진입점 `bridge = service_bridge.main:main`)
- Test: `pc/service_bridge/test/test_convert.py`, `pc/service_bridge/test/test_bridge_node.py`

**Interfaces:**
- Consumes: `apply_report` · `apply_hello` · `make_pool` · `dsn_from_env` · 데이터 클래스
- Produces:
  - `convert.report_in(req: ReportTrip.Request) -> ReportIn`, `convert.hello_in(req: Hello.Request) -> HelloIn`, `convert.items(items: list[OrderItem]) -> dict[str, int]` (같은 kind는 합침)
  - `class BridgeNode(Node)`: `__init__(self, pool: ConnectionPool, **kwargs)` — 매개변수 `mode` 필수(`NORMAL` · `TEST`, 그 밖은 `ValueError`). 켜질 때 `/system/mode` 발행 → 0.5초 타이머로 `pool`에 `select 1`이 될 때까지 확인 → 되면 `bridge_session` 로그, 서버 두 개 생성, 타이머 취소.
  - `main()` — `rclpy.init`, `make_pool(dsn_from_env(), open=False)`, `MultiThreadedExecutor(num_threads=4)`, 끌 때 `executor.shutdown(timeout_sec=2.0)` 뒤 `pool.close()`.

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_report_in_sums_same_kind():          # Review Focus 1
    req = ReportTrip.Request(trip_id="t", report_epoch=7, report_n=1, status="FILLING",
                             placed=[OrderItem(kind="A", count=1), OrderItem(kind="A", count=1)])
    assert convert.report_in(req).placed == {"A": 2}

def test_mode_must_be_given_and_valid(pool):  # mode 없음 · "test" → ValueError (rclpy 매개변수로 넣음)
def test_report_trip_round_trip(pool, seeded):
    # 같은 프로세스에서 BridgeNode + 클라이언트 노드(MultiThreadedExecutor) →
    # Hello("fleet","b1") 응답 epoch 1, ReportTrip(ASSIGNED, (1,1)) 응답 accepted True result "APPLIED",
    # 같은 요청 다시 → "DUPLICATE"
def test_no_servers_until_db_ready():         # Review Focus 3, 설계 6.1
    # 없는 포트 DSN의 풀 → 2초 동안 /system/mode(TEST, 늦게 붙은 구독자도 받음)는 오지만
    # /bridge/report_trip 서비스는 없음
```

rclpy 시험은 시험마다 `rclpy.init(domain_id=<빈 개인 도메인>)`, `conftest.py`에서 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`를 넣습니다.

- [ ] **Step 2: 실패 확인** — `cd "$IPONIC_WS" && colcon build --packages-select service_bridge && source install/setup.bash && ~/venv/iponic/bin/python -m pytest /home/ane/dev_ws/roscamp-repo-2/pc/service_bridge/test -q`, Expected: 새 시험 FAIL
- [ ] **Step 3: 구현** — 서버 콜백: `deadline = time.monotonic() + 2.5` → `convert` → core 함수 → 응답 칸 채움. 콜백 전체를 `try/except Exception`으로 감싸 `accepted=false, reason="DB_ERROR"`와 ERROR 로그. 서버 두 개는 각자 `MutuallyExclusiveCallbackGroup`.
- [ ] **Step 4: 통과 확인** — 같은 명령, Expected: 모두 통과
- [ ] **Step 5: 커밋** — `git commit -m "feat(bridge): 브리지 노드 — Hello · ReportTrip 서버, /system/mode 발행"`

---

### Task 7: 시험 도구와 한 바퀴 시험

**Files:**
- Create: `pc/service_bridge/service_bridge/tools/__init__.py`, `tools/seed.py`, `tools/trips.py`, `tools/setup_db.py`, `tools/submit_trip.py`, `tools/fake_fleet.py`
- Modify: `pc/service_bridge/setup.py` (진입점 `setup_db` · `submit_trip` · `fake_fleet`), `pc/service_bridge/test/conftest.py` (`seeded`가 `seed`를 씀), `test_reports.py` · `test_hello.py` (`make_trip` → `insert_trip`)
- Test: `pc/service_bridge/test/test_trips.py`, `pc/service_bridge/test/test_loop.py`

**Interfaces:**
- Produces:
  - `seed.seed(conn) -> dict` — 상품 A · B · C · D(재고 0으로 넣고 `stock_ledger` `INBOUND` 20, 사유 `REGULAR`), 계정 `tester`(`USER`). 돌려주는 값은 Task 1 `seeded`와 같은 모양.
  - `trips.insert_trip(conn, account_id: int, items: dict[str, int]) -> tuple[str, str]` — (order_id, trip_id). `order_id = f"O-{YYYYMMDD}-{그날 순번:04d}"`, `trip_id = f"{order_id}-1"`. 품목 합 > `app_setting`의 `basket_capacity`(4)면 `ValueError`. 접수 이력 두 줄(actor `USER`).
  - `setup_db` 명령: `IPONIC_DB_DSN`의 DB를 지우고 다시 만든 뒤 두 SQL과 `seed` 적용.
  - `submit_trip --items A:1,C:1 [--trip-id ID]` 명령: `insert_trip` 커밋 뒤 `SubmitTrip` 호출(2초 한도, 1초 간격, 최대 5번). 결과 한 줄 출력, 접수되면 종료 코드 0.
  - `FakeFleetNode(Node)`: 매개변수 `step_s`(1.0), `duplicate_every_report`(False), `missing`(""; 예 `"B:1"`). 설계 8.3의 1 ~ 5.

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_insert_trip_ids_and_history(conn, seeded):
    o, t = insert_trip(conn, seeded["account_id"], {"A": 1, "C": 1})
    assert re.fullmatch(r"O-\d{8}-0001", o) and t == f"{o}-1"
    assert count(conn, "status_history where order_id=%s", o) == 2

def test_full_loop(pool, seeded):                    # test_loop.py, 완료 기준 2 · 3
    # 같은 프로세스: BridgeNode(mode TEST) + CentralNode + FakeFleetNode(step_s=0.2,
    # duplicate_every_report=True), submit_trip 함수 직접 호출 →
    # 20초 안에: trip SHIPPED, (report_epoch, report_n) == (1, 4), 이력 5줄,
    # central 주문판 비어 있음, 브리지가 DUPLICATE를 4번 답함(가짜 fleet이 센 값)
```

- [ ] **Step 2: 실패 확인** — Task 6 Step 2와 같은 명령, Expected: 새 시험 FAIL
- [ ] **Step 3: 구현** — `fake_fleet`은 서비스 호출을 작업 스레드 하나에서 `call_async` + `threading.Event`로 기다림(실행기 스레드를 막지 않음). 같은 운반 건은 순서대로, 3초 한도, 1 → 2 → 4초(최대 10초) 간격, 포기하지 않음. `SHIPPED` · `PARTIAL`이 `accepted=true`를 받은 뒤 `ReportOrder`(`STATUS_SHIPPED` · `STATUS_PARTIAL`).
- [ ] **Step 4: 통과 확인** — Expected: 모두 통과
- [ ] **Step 5: 손으로 한 바퀴** (터미널 4개, 모두 `iponic_local`)

```bash
docker compose -f design/db/compose.yaml up -d && ros2 run service_bridge setup_db
```
```bash
ros2 run service_bridge bridge --ros-args -p mode:=TEST
```
```bash
ros2 run central central
```
```bash
ros2 run service_bridge fake_fleet
```
```bash
ros2 run service_bridge submit_trip --items A:1,C:1
```
Expected: 10초 안에 `psql … -c "select status, report_epoch, report_n from trip"`이 `SHIPPED | 1 | 4`, `ros2 topic echo --once /central/orders`의 `orders`가 빈 목록.

- [ ] **Step 6: 커밋** — `git commit -m "feat(bridge): 시험 도구(setup_db · submit_trip · fake_fleet)와 한 바퀴 시험"`

---

### Task 8: 문서 정리

**Files:**
- Create: `pc/service_bridge/README.md` (역할, 실행 · 시험 명령, 설계 · 계획 링크, 관제 fleet과 붙일 때 `sim/fake_robots` 런치는 `fake_central:=false`이고 `fake_fleet`은 끔)
- Modify: `tests/service_bridge/README.md` "나중에 할 일"(코드 위치 `pc/central` → `pc/service_bridge`), `docs/설계/주문_흐름_1차_인터페이스.md` 5.3 "코드 위치" 줄(→ `pc/service_bridge`, 2026-11-25), `CLAUDE.md` · `AGENTS.md`의 표에 "서비스 브리지 코드 · 시험 도구 → `pc/service_bridge/README.md`" 한 줄 (두 파일 같이)

- [ ] **Step 1: 고치기** — 위 네 곳
- [ ] **Step 2: 확인** — `grep -n "pc/central에 생기면" tests/service_bridge/README.md` 결과 없음, `diff <(sed 1,5d CLAUDE.md) <(sed 1,5d AGENTS.md)`가 표 부분에서 차이 없음
- [ ] **Step 3: 전체 시험** — Task 6 Step 2 명령과 `colcon test --packages-select central`, Expected: 모두 통과
- [ ] **Step 4: 커밋** — `git commit -m "docs(bridge): 패키지 안내와 코드 위치 반영"`
