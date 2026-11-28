# central 재시작 복구 · 디스커버리 연결 · 1차 정리 · 실행 안내 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** central이 연결 설정 → 주문판 갱신 → 실행 중 세 단계로 켜지며 브리지 DB에서 끝나지 않은 운반 건을 받아 주문판을 다시 세우고, 브리지는 그 단계를 보고 넘기기를 멈추거나 이어 가며, 팀원이 1차 연동 시험을 띄울 수 있는 HTML 실행 안내를 만든다.

**Architecture:** 판단은 ROS 없는 코드에 둔다(central `lifecycle.py`, 브리지 `core/board_trips.py` · `core/central_watch.py`). 노드는 연결만 한다. 인터페이스는 새 메시지 `CentralState`와 새 서비스 이름 `/bridge/list_board_trips`(기존 타입 `ListOpenTrips`)뿐이며 fleet은 바꾸지 않는다. 커밋은 설계 6.7절의 묶음대로 나눠, SB-06 정정이 뒤집히면 `git revert`로 되돌릴 수 있게 한다.

**Tech Stack:** ROS 2 Jazzy · rclpy · Fast DDS 디스커버리 서버(`tools/net/net_ds.sh`), PostgreSQL · psycopg · FastAPI(브리지), pytest, 정적 HTML.

**Spec:** `docs/superpowers/specs/2026-11-28-central-restart-design.md`

## Global Constraints

- 작업 위치: 고정 저장소 `/home/ane/dev_ws/roscamp-repo-2`, 브랜치 `feat/central-restart-2026-11-28`. worktree를 만들지 않는다. `git stash`(인자 없음)를 쓰지 않는다. 남의 커밋 안 된 변경은 건드리지 않는다. `reports/Integration Test Report Example.pdf`는 커밋하지 않는다.
- 커밋은 계획의 커밋 단계에서만, 푸시는 하지 않는다(사용자가 요청할 때만).
- 주석 · 문서 · 커밋 메시지는 한국어, 주석은 무엇을 왜. 커밋 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 인터페이스를 바꾸면 `contract.yaml` · 명세 · 통신 규칙을 같은 커밋에서(IR-90). contract **1.0 → 1.1**.
- fleet 코드(`pc/fleet_manager`, `pc/fleet_core`)는 바꾸지 않는다.
- central은 DB를 갖지 않는다(IR-55).
- 다시 부르기 값: 응답 한도 3초, 간격 1 → 2 → 4 → 8 → 10초(최대 10초) ±20%, `TODO(Q-09)`. central 상태 신선함 한도 3초, `TODO(Q-09)`.
- SB-06 · SB-13 · Q-02는 원래 상태 표시("확정" · "열림")를 그대로 두고 "2026-11-28 정정 제안" 메모만 단다(사용자 결정 2026-11-28).
- 시험 실행: ROS 노드 시험은 개인 도메인 + LOCALHOST로(`FLEET_DOMAIN_ID=87 FLEET_LOCAL=1 source tools/net/net_ds.sh`). 브리지 시험은 `docker compose -f design/db/compose.yaml up -d` 뒤 `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q`. 모든 대기에 `timeout`을 둔다.
- 알려진 실패 1건(`test_ros_workcell.py::test_an_arm_error_shows_only_after_the_result_went_out`)은 이 계획과 무관하다. 그 밖의 실패는 0이어야 한다.

## Review Focus

1. **central이 SYNCING 중 받은 `SubmitTrip`을 RUNNING 뒤 다시 받음:** 같은 `trip_id`가 목록에도 있고 `SubmitTrip`으로도 오면 주문판에 한 번만 있어야 한다. → Task 4 `test_resubmit_of_listed_trip_is_accepted_once`.
2. **목록이 비어 있음 / 목록 답이 `accepted=false`:** 빈 목록이면 빈 주문판으로 RUNNING, `accepted=false`면 SYNCING에 남아 다시 부른다(빈 주문판을 내지 않음). → Task 4 `test_rejected_list_keeps_syncing_and_backs_off`.
3. **브리지가 켜질 때 central이 이미 RUNNING (처음 보는 `boot_id`):** 알림 없음, 넘기기 바로 시작. → Task 5 `test_first_boot_id_only_remembered`.
4. **central 상태가 끊김(3초 넘게 안 옴) 중 쌓인 줄:** 넘기기를 멈추고 `attempts`를 올리지 않는다. 다시 오면 이어서 보낸다. → Task 5 `test_stale_state_blocks_dispatch_without_counting`.
5. **끝난 건 · 거절된 건이 목록에 섞임:** `SHIPPED` · `PARTIAL` · `CANCELLED` · 거절 줄은 목록에 없어야 한다(되살아나면 fleet이 다시 묶음). → Task 3 `test_final_and_rejected_trips_are_left_out`.

---

## 파일 구조

| 파일 | 할 일 | 작업 |
|---|---|---|
| `interfaces/fleet_interfaces/msg/CentralState.msg` | 새 메시지 | 2 |
| `interfaces/fleet_interfaces/CMakeLists.txt`, `contract.yaml` | 메시지 등록, 1.1 | 2 |
| `docs/설계/인터페이스_명세.md`, `docs/설계/통신_규칙.md` | 3.2 · 3.3 · 3.4, IR-70 · IR-71 | 2 |
| `pc/service_bridge/service_bridge/core/board_trips.py` | 목록 읽기(ROS 없음) | 3 |
| `pc/service_bridge/service_bridge/ros/convert.py` | `BoardTrip` → `OpenTrip` | 3 |
| `pc/service_bridge/service_bridge/tools/board_trips_stub.py` | 브리지 없이 central 보기 | 3 |
| `pc/central/central/lifecycle.py` | 단계 판단 · 목록 반영 · 다시 부르기 간격 | 4 |
| `pc/central/central/board.py` | `load()` 더함 | 4 |
| `pc/central/central/central_node.py` | 단계 연결, `/central/state` 발행 | 4 |
| `pc/service_bridge/service_bridge/core/central_watch.py` | 상태 판단 · 재시작 알림 · 실패 기록 풀기 | 5 |
| `pc/service_bridge/service_bridge/core/dispatch.py` | `record_not_ready` | 5 |
| `pc/service_bridge/service_bridge/ros/dispatcher.py`, `ros/bridge_node.py` | 구독 · 멈춤 · 깨우기 | 5 |
| `core/snapshot.py`, `web/app.py`, `web/static/admin/…` | `work_state.central`, 화면 표시 | 5 |
| `tools/net/test_central_restart.sh` | 연결 시험 S1 ~ S4 | 7 |
| `docs/1차_연동_실행_안내.html` | 실행 안내 | 8 |

---

### Task 1: 연결 문서 · 1차 정리 (6.7 묶음 1)

SB-06 결정과 상관없이 남는 문서 정리와 표시. 코드 동작은 바뀌지 않는다.

**Files:**
- Modify: `docs/설계/주문_흐름_1차_인터페이스.md` (머리 상태 줄, 3.3 · 4.2 F-11 줄, 4.3 할 일 1, 9장)
- Modify: `docs/설계/백엔드_검토_목록.md:189`
- Modify: `pc/service_bridge/README.md`, `docs/개발_규칙.md` (브리지 개발 환경 절)
- Modify: `pc/central/central/central_node.py` (`_on_system_mode`), `pc/service_bridge/service_bridge/web/hub.py` (첫 메시지 자리)

**Interfaces:** 없음.

- [ ] **Step 1: 1차 문서를 설계 8절 표 첫 두 줄대로 고친다.** 상태 줄에 contract 1.0 · ReportCommand 받는 쪽 · `FILLING_PAUSE` · 주문 코어 1차 완료 · 취소 사유 `NOTHING_TO_FILL`을 적고, 4.3 할 일 1에 완료 표시, "central은 `/fleet/robots` 구독만" 문구를 뺀다(코드는 원래 구독하지 않음). 남은 것: Phony `/arm_ship/swap_basket` 서버, Phody `set_pause`, 화면 완료 기준 확인. 9장에 "Q-20이 정해지면 `net_ds.sh`의 `DS_CENTRAL`을 채운다". 6.4의 central 재시작 줄은 여기서 손대지 않는다(Task 2).
- [ ] **Step 2: 백엔드 검토 목록 189행을 SB-06 원문("알아채면 미확정 건은 다시 보내지 않고 관리자 알림")대로 바로잡는다.**
- [ ] **Step 3: 디스커버리 연결 안내(설계 7절 첫 두 항목).** README 실행 표 앞과 개발 규칙 브리지 절에 "브리지 · central을 띄우는 터미널마다 `net_ds.sh`를 source(`iponic_real` / `iponic_local`), 브리지는 venv를 켠 뒤 같은 터미널에서". README에 "컨테이너에 넣으면 host 망(디스커버리 안내 4절 9항)".
- [ ] **Step 4: TODO 표시.** `central_node._on_system_mode`에 `# TODO(IR-73): 모드가 다르면 SubmitTrip 거절은 아직 안 함`, `web/hub.py` 첫 메시지 자리에 `# TODO(IR-51): since_seq로 놓친 사건 다시 보내기는 아직 안 함`. 해당 Q가 있으면 Q 번호를 쓴다(`docs/설계/설계_검토_사항.md`에서 확인).
- [ ] **Step 5: 회귀 확인.** Run: `colcon test --packages-select central` (개인 도메인) · 브리지 시험. Expected: 실패 0.
- [ ] **Step 6: 커밋** `docs: 1차 문서 상태 · 디스커버리 연결 안내 정리, 줄인 것 TODO 표시`

### Task 2: 인터페이스 contract 1.1 (6.7 묶음 2)

**Files:**
- Create: `interfaces/fleet_interfaces/msg/CentralState.msg` (설계 5.1 그대로)
- Modify: `interfaces/fleet_interfaces/CMakeLists.txt` (`"msg/CentralState.msg"`), `contract.yaml`
- Modify: `docs/설계/인터페이스_명세.md` (머리 기준 버전, 3.2 · 3.3 · 3.4), `docs/설계/통신_규칙.md` (IR-70 · IR-71), `docs/설계/주문_흐름_1차_인터페이스.md` 6.4

**Interfaces:**
- Produces: contract 키 `central_state` → `/central/state` (`fleet_interfaces/msg/CentralState`, LATCHED, `not_in_fake: true`), `list_board_trips` → `/bridge/list_board_trips` (`fleet_interfaces/srv/ListOpenTrips`, `not_in_fake: true`). 상수 `CentralState.STATE_CONNECTING|STATE_SYNCING|STATE_RUNNING`.

- [ ] **Step 1: 실패하는 확인을 쓴다** (`pc/central/test/test_contract_central.py`):

```python
def test_contract_has_central_state_and_board_trips():
    c = load_contract(get_package_share_directory("fleet_interfaces") + "/contract.yaml")
    assert c.name("central_state") == "/central/state"
    assert c.name("list_board_trips") == "/bridge/list_board_trips"
    from fleet_interfaces.msg import CentralState
    assert CentralState.STATE_RUNNING == "RUNNING"
```

- [ ] **Step 2: 돌려서 실패를 본다.** Run: `colcon build --packages-select fleet_interfaces central && colcon test --packages-select central --pytest-args -k contract`. Expected: FAIL(키 없음).
- [ ] **Step 3: 메시지 · contract를 더한다.** `version: 1.1`, 변경 이력 `# 1.1 (2026-11-28): central 운영 단계 /central/state(CentralState), central 주문판 복구 /bridge/list_board_trips(ListOpenTrips) — 브리지 · central만 씀, fleet 변경 없음`. 서비스 줄은 `list_open_trips` 바로 아래.
- [ ] **Step 4: 명세 · 통신 규칙 · 1차 문서 6.4.** 명세 3.3 표에 `list_board_trips` 줄과 `ListOpenTrips` 설명 "central은 `list_board_trips`로 같은 타입을 부른다", 3.2 · 3.4의 "central 주문판 미정" 문구를 설계 6.1로. IR-70 · IR-71에 central 켜질 때 순서(CONNECTING 조건 → 목록 → RUNNING 뒤에만 주문판 발행). 문구 끝에 "(2026-11-28 제안 구현, SB-06 정정 제안과 함께 결정)".
- [ ] **Step 5: 통과 확인.** 같은 명령. Expected: PASS. 그리고 `colcon build`(관제 PC 전체) 성공.
- [ ] **Step 6: 커밋** `feat(interfaces): contract 1.1 — /central/state · /bridge/list_board_trips`

### Task 3: 브리지 목록 서버 · `board_trips_stub` (6.7 묶음 3)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/board_trips.py`, `pc/service_bridge/service_bridge/tools/board_trips_stub.py`
- Modify: `pc/service_bridge/service_bridge/ros/convert.py`, `ros/bridge_node.py` (`_probe_db`의 서버 목록)
- Test: `pc/service_bridge/test/test_board_trips.py`, `test_bridge_node.py`에 한 건

**Interfaces:**
- Consumes: Task 2의 `list_board_trips` 키.
- Produces:
  - `@dataclass(frozen=True) class BoardTrip`: `trip_id: str, order_id: str, trip_index: int, trip_count: int, items: dict[str,int], status: str, reason: str, robot_id: str, placed: dict[str,int], missing: dict[str,int], submitted_at: datetime, report_epoch: int, report_n: int`
  - `@dataclass(frozen=True) class BoardTripsOut`: `accepted: bool, trips: list[BoardTrip], reason: str`
  - `list_board_trips(pool: ConnectionPool, deadline: float) -> BoardTripsOut` (DB 오류 → `BoardTripsOut(False, [], "DB_ERROR")`; `apply_report`와 같은 deadline 사용법)
  - `convert.open_trip(t: BoardTrip) -> OpenTrip`
  - 도구: `python -m service_bridge.tools.board_trips_stub [--trips FILE.json]` — `/bridge/list_board_trips`에 답하고 `/system/mode`(`TEST`)를 LATCHED로 한 번 발행. FILE은 `[{"trip_id", "items": {"kind": n}, "status", "submitted_at"(ISO)}]`.

- [ ] **Step 1: 실패하는 시험** (`seeded` 고정물과 `tools/trips.py`의 기존 도움 함수로 줄을 만든다):
  - `test_lists_open_and_unacked_trips_in_created_order`: 넘긴 건(ACCEPTED) 1, 아직 안 넘긴 건 1 → 두 건, `created_at, trip_id` 순, `items`·`trip_index`/`trip_count`·`submitted_at` 값 일치.
  - `test_final_and_rejected_trips_are_left_out`: `SHIPPED` · `PARTIAL` · `CANCELLED` 건과 `last_result='INVALID_REQUEST'`인 확정 줄 → 목록에 없음.
  - `test_held_trip_is_listed_with_status_held`: `status == "HELD"`, `reason`이 DB 사유.
  - `test_trip_without_dispatch_row_is_left_out`.
  - `test_db_error_gives_not_accepted`: 닫힌 풀 → `accepted is False`, `reason == "DB_ERROR"`.
- [ ] **Step 2: 실패 확인.** Run: `~/venv/iponic/bin/python -m pytest pc/service_bridge/test/test_board_trips.py -q`. Expected: FAIL(모듈 없음).
- [ ] **Step 3: 구현.** 한 번의 `REPEATABLE READ` 읽기 트랜잭션. 끝 상태 · 거절 조건은 설계 6.3 그대로. `placed` · `missing` · `report_epoch/n`은 지금 DB에 있는 칸에서(없으면 빈 값 · 0, 어느 칸인지 `design/db/README.md` 확인).
- [ ] **Step 4: 통과 확인.** 같은 명령. Expected: PASS.
- [ ] **Step 5: 노드 연결 시험** `test_bridge_node.py::test_list_board_trips_server_answers_after_db` — DB가 붙은 뒤 `/bridge/list_board_trips`가 `accepted=True`와 시드 건 수를 돌려줌. 실패 확인 → `_probe_db`의 `self._services`에 서버 추가(`MutuallyExclusiveCallbackGroup`, 콜백은 `list_board_trips(self._pool, deadline)` → `convert.open_trip`) → 통과.
- [ ] **Step 6: `board_trips_stub` 작성 · 손 확인.** `timeout 10 python -m service_bridge.tools.board_trips_stub &` 뒤 `ros2 service call /bridge/list_board_trips fleet_interfaces/srv/ListOpenTrips "{epoch: 0}"` → `accepted=True, trips=[]`.
- [ ] **Step 7: 커밋** `feat(bridge): 끝나지 않은 운반 건 목록 서버 /bridge/list_board_trips, board_trips_stub 도구`

### Task 4: central 단계 (6.7 묶음 4)

**Files:**
- Create: `pc/central/central/lifecycle.py`
- Modify: `pc/central/central/board.py`, `pc/central/central/central_node.py`
- Modify: `docs/개발_규칙.md` "손으로 한 바퀴 시험" 블록, `pc/service_bridge/README.md` 실행 표의 `central` · "운반 건 직접 넘기기" 줄 (central 손 확인 → "브리지와 함께, 또는 `board_trips_stub`과 함께")
- Test: `pc/central/test/test_lifecycle.py`, `pc/central/test/test_central_node.py`

**Interfaces:**
- Consumes: Task 2 contract 키 · `CentralState`, Task 3 `board_trips_stub`(노드 시험에서 같은 방식의 시험용 서버).
- Produces:
  - `Board.load(entries: list[tuple[str, dict, object, bool]]) -> None` — `(trip_id, items, created_at, held)`를 순서대로 넣고 각 `trip_id`의 답을 `(True, "")`로 기억, `held`면 HELD 표시. 빈 `items`나 이미 아는 `trip_id`는 건너뜀.
  - `class Lifecycle` (ROS 없음, 시각은 인자로):
    - `__init__(self, board: Board, boot_id: str, rand: Callable[[], float] = random.random)`
    - `state: str` (`"CONNECTING"|"SYNCING"|"RUNNING"`), `boot_id: str`
    - `ready_to_sync(self, service_ready: bool, mode_seen: bool) -> bool` — 둘 다 참이면 SYNCING으로 바꾸고 True
    - `on_list(self, accepted: bool, trips: list[tuple[str, dict, object, str]]) -> bool` — `(trip_id, items, submitted_at, status)`. accepted면 `board.load` 뒤 RUNNING, True. 아니면 False
    - `next_retry_sec(self) -> float` — 실패할 때마다 1 → 2 → 4 → 8 → 10 → 10…(±20%)
    - `accepts_work(self) -> bool` — RUNNING일 때만 참
  - 노드: `/central/state` 발행(바뀔 때 + 1초마다), RUNNING 전에는 `/central/orders` 발행 안 함, `SubmitTrip` → `accepted=False, reason="NOT_READY"`, `ReportOrder` → `accepted=False`.

- [ ] **Step 1: 실패하는 시험** (`test_lifecycle.py`):
  - `test_starts_connecting_and_needs_both_conditions`: `ready_to_sync(True, False)`·`(False, True)` → False · CONNECTING, `(True, True)` → SYNCING.
  - `test_list_loads_in_order_and_runs`: 두 건 → `board.entries()` 순서 그대로, `state == "RUNNING"`.
  - `test_held_status_is_marked`: `status="HELD"` 건 → `board.held() == {trip_id}`.
  - `test_empty_list_runs_with_empty_board`.
  - `test_rejected_list_keeps_syncing_and_backs_off`: `on_list(False, [])` → False, SYNCING, `next_retry_sec` 연속 값이 `rand=lambda: 0.5`일 때 `[1, 2, 4, 8, 10, 10]`.
  - `test_resubmit_of_listed_trip_is_accepted_once`: 목록 반영 뒤 `board.submit(같은 trip_id, …)` → `(True, "")`, 항목 수 그대로.
  - `test_not_running_does_not_accept_work`.
- [ ] **Step 2: 실패 확인.** Run: `colcon test --packages-select central --pytest-args -k lifecycle`. Expected: FAIL.
- [ ] **Step 3: `Board.load` · `Lifecycle` 구현.** 간격 표 `(1, 2, 4, 8, 10)` 와 `# TODO(Q-09): IR-40 Report* 임시 값` 주석.
- [ ] **Step 4: 통과 확인.** Expected: PASS.
- [ ] **Step 5: 노드 시험을 바꾼다/더한다** (`test_central_node.py`): 기존 시험은 시험용 목록 서버 + `/system/mode` 발행으로 RUNNING을 만든 뒤 돈다. 새로:
  - `test_no_board_before_running_and_state_every_second`: 목록 서버 없이 3초 → `/central/orders` 0건, `/central/state`는 `CONNECTING`으로 2건 이상, `boot_id` 32자 hex.
  - `test_submit_before_running_is_not_ready`.
  - `test_runs_after_list_and_publishes_listed_board`: 목록 2건(1건 HELD) → state `RUNNING`, 첫 주문판 2건 순서 일치.
- [ ] **Step 6: 노드 연결 구현.** `boot_id = uuid.uuid4().hex`. 1초 타이머에서: CONNECTING이면 `ready_to_sync(client.service_is_ready(), mode_seen)`, 5초마다 `브리지를 찾는 중` 로그. SYNCING이면 요청이 없을 때 `call_async(ListOpenTrips.Request(epoch=0))`, 3초 넘으면 버리고 `next_retry_sec()` 뒤 다시. 답 → `on_list` → RUNNING이면 주문판 발행 + 로그 `주문판 갱신 끝: N건 (HELD M건) boot_id=…`.
- [ ] **Step 7: 통과 확인.** Run: `colcon test --packages-select central`. Expected: 실패 0.
- [ ] **Step 8: 문서 두 곳의 central 손 확인 줄을 고친다**(설계 6.6).
- [ ] **Step 9: 커밋** `feat(central): 연결 설정 → 주문판 갱신 → 실행 중 단계, /central/state 발행`

### Task 5: 브리지가 central 지켜보기 · 화면 (6.7 묶음 5)

**Files:**
- Create: `pc/service_bridge/service_bridge/core/central_watch.py`
- Modify: `core/dispatch.py`, `ros/dispatcher.py`, `ros/bridge_node.py`, `main.py`, `core/snapshot.py`, `web/app.py`, `web/static/admin/` (모드 표시 옆), `web/static/test/` 사례
- Test: `test/test_central_watch.py`, `test/test_dispatch.py`, `test/test_dispatcher_node.py`, `test/test_snapshot_events.py`

**Interfaces:**
- Consumes: Task 2 `central_state` 키.
- Produces:
  - `class CentralWatch` (ROS 없음, 시각은 인자로, 스레드 안전 `threading.Lock`):
    - `on_state(self, boot_id: str, state: str, now: float) -> str | None` — 바뀐 옛 `boot_id`를 돌려줌(처음 본 값이면 None)
    - `view(self, now: float) -> str` — `"CONNECTING"|"SYNCING"|"RUNNING"|"LOST"` (3초 넘게 없거나 아직 없음 → `LOST`)
    - `can_dispatch(self, now: float) -> bool` — `view(now) == "RUNNING"`
  - `record_central_restart(conn, old_boot_id: str, new_boot_id: str) -> int` (`core/central_watch.py`) — 한 트랜잭션: `alert` INFO `CENTRAL_RESTART`(설계 6.4 문구 · detail), 미확정 줄 `attempts=0, next_attempt_at=NULL, unknown_since=NULL`, 그 줄들의 열린 `DISPATCH_STALLED` → `RESOLVED`. 푼 줄 수를 돌려줌.
  - `record_not_ready(conn, trip_id: str) -> None` (`core/dispatch.py`) — `last_result='NOT_READY'`만, `attempts` 그대로.
  - `Dispatcher.__init__(…, can_dispatch: Callable[[], bool] = lambda: True)`
  - `BridgeNode(…, on_central: Callable[[str], None] | None = None)` — 보이는 값(`view`)이 바뀔 때 부름. `main.build`가 앱의 `work_state`로 잇는다.
  - 스냅숏 · WS `work_state.data`: `{"system_mode": …, "central": "CONNECTING"|"SYNCING"|"RUNNING"|"LOST"}`.

- [ ] **Step 1: 실패하는 core 시험.**
  - `test_central_watch.py`: `test_first_boot_id_only_remembered`(None), `test_changed_boot_id_returns_old`, `test_view_is_lost_before_any_and_after_3s`(2.9초 RUNNING, 3.1초 LOST), `test_stale_state_blocks_dispatch_without_counting`(`can_dispatch` 거짓).
  - DB: `test_restart_records_info_alert_and_resets_unacked`: 미확정 줄 `attempts=5`·쉼·열린 `DISPATCH_STALLED` → 호출 뒤 `attempts=0`, 쉼 NULL, 알림 RESOLVED, `CENTRAL_RESTART` INFO 1건(detail `old_boot_id`·`new_boot_id`), 확정 줄은 그대로.
  - `test_dispatch.py::test_not_ready_keeps_attempts`.
- [ ] **Step 2: 실패 확인.** Expected: FAIL.
- [ ] **Step 3: 구현.** 신선함 한도 `STALE_SEC = 3.0  # TODO(Q-09)`.
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: 노드 시험** (`test_dispatcher_node.py`):
  - `test_no_submit_until_central_running`: `/central/state`를 SYNCING으로 발행하는 동안 `SubmitTrip` 요청 0건 · `attempts` 0, RUNNING을 발행하면 1초 안에 보냄.
  - `test_not_ready_reply_does_not_stall`: 가짜 central이 `NOT_READY`를 6번 → `attempts` 0, `DISPATCH_STALLED` 없음.
  - `test_boot_id_change_writes_one_alert`.
- [ ] **Step 6: 노드 연결 구현.** 구독은 `on_robots`와 같은 방식(자기 콜백 그룹). `_handle_reply`에서 `NOT_READY` → `record_not_ready` 뒤 `retry_delay(0)`. `_send_head` 앞에서 `can_dispatch()`가 거짓이면 아무것도 적지 않고 돌아감. RUNNING이 되면 `wake()`.
- [ ] **Step 7: 화면.** `admin_snapshot`의 `work_state`에 `central`, 바뀔 때 WS `work_state` 다시 보냄. 관리자 화면 모드 표시 옆 "central: 연결 중 / 주문판 갱신 중 / 실행 중 / 연결 안 됨". `run_headless.sh` 사례 네 값. Run: `bash pc/service_bridge/service_bridge/web/static/test/run_headless.sh`. Expected: 실패 0.
- [ ] **Step 8: 전체 브리지 시험.** Expected: 실패 0.
- [ ] **Step 9: 커밋** `feat(bridge): central 상태 지켜보기 — RUNNING일 때만 넘김, NOT_READY 안 셈, 재시작 안내 알림, 화면 표시`

### Task 6: SB-06 · SB-13 · Q-02 제안 메모 (6.7 묶음 6)

**Files:** `docs/설계/서비스_브리지_기술_조사.md` (SB-06 · SB-13), `docs/설계/설계_검토_사항.md` (Q-02), `docs/설계/백엔드_검토_목록.md:189`

- [ ] **Step 1: 설계 6.5 문구대로 메모를 단다.** "확정 (2026-11-24)" · "열림" 표시는 그대로. 189행에는 원문 뒤에 "(2026-11-28 정정 제안: …)"를 덧붙인다.
- [ ] **Step 2: 확인.** Run: `git diff --stat` → 이 세 파일만.
- [ ] **Step 3: 커밋** `docs: SB-06 정정 제안 · SB-13 · Q-02 제안 구현 메모 (다음 주 논의에서 결정)`

### Task 7: 연결 시험 스크립트 (6.7 묶음 7)

**Files:** Create `tools/net/test_central_restart.sh`

- [ ] **Step 1: 스크립트 작성.** `test_two_servers.sh`와 같은 방식으로 `127.0.0.1`의 시험 포트 두 개(포트 · 도메인은 개인 범위에서 골라 머리 주석에), 시험 DB(`setup_db`), 브리지(`mode:=NORMAL`), central. 모든 대기는 `timeout`, 띄운 프로세스는 `trap`으로 PID를 끔(`pgrep -f` 대기 금지). 시나리오 S1 ~ S4(설계 7절)를 차례로, 각 줄 `S1 통과`/`S1 실패: 까닭`, 하나라도 실패면 종료 코드 1. 운반 건은 `tools.order`로 만든다. 확인은 super client `ros2 topic echo --once`, DB는 `psql`.
- [ ] **Step 2: 실행.** Run: `timeout 600 bash tools/net/test_central_restart.sh`. Expected: `S1 통과` ~ `S4 통과`, 종료 코드 0. 끝난 뒤 남은 프로세스 없음(`ps`로 확인).
- [ ] **Step 3: 커밋** `test(net): central 재시작 · 디스커버리 서버 연결 시험 스크립트`

### Task 8: 1차 연동 실행 안내 HTML · 검토 (6.7 묶음 8)

**Files:**
- Create: `docs/1차_연동_실행_안내.html`
- Modify: `docs/개발_규칙.md`, `pc/service_bridge/README.md` (HTML 링크 한 줄씩)

**Interfaces:** Task 3 ~ 7의 명령 · 이름(`board_trips_stub`, `/central/state`, `work_state.central`, `test_central_restart.sh`).

- [ ] **Step 1: HTML 작성.** 설계 8.1의 아홉 항목을 그 순서로. 한 파일, 바깥 자원 없음, `:root` 색 토큰 + `prefers-color-scheme: dark`, 375px에서 가로 밀림 없음(`pre`는 자체 가로 스크롤), 명령 블록마다 복사 단추(스크립트 없이도 내용은 다 보임). 머리에 "기준: `feat/central-restart-2026-11-28` 커밋 `<짧은 해시>` · 2026-11-28, 원본: 개발 규칙 · 브리지 README, 다르면 원본을 따름". 구성 그림은 인라인 SVG. 명령은 개발 규칙 · README에서 옮겨 적고 새로 지어내지 않는다.
- [ ] **Step 2: 따라 해 보기 검토** — 새 하위 에이전트(이 대화 없음, sonnet 이상)에게 HTML 경로만 주고: 새 셸에서 혼자 시험 구성(도메인 87 · `FLEET_LOCAL=1` · 시험 DB · `fake_fleet`)으로 처음부터 주문 한 바퀴까지 따라 하고, `.bashrc`는 고치지 않고 같은 줄을 셸에서 실행하며, 막힌 곳 · 틀린 명령 · 빠진 단계를 `<workspace>/task-8-walkthrough.md`에 적게 한다. 띄운 프로세스는 다 끄게 한다.
- [ ] **Step 3: 원본 대조 검토** — 다른 하위 에이전트에게 HTML · 개발 규칙 · README · `contract.yaml` · `bringup/iponic_bringup/launch/pc.launch.py`를 주고 명령 · 이름 · 기본값(포트 8000, `IPONIC_WEB_PORT`, `fake_central` 기본 `true`, `mode:=` 필수 등)을 한 줄씩 대조해 `<workspace>/task-8-crosscheck.md`에 적게 한다. Step 2와 같은 메시지에서 함께 띄운다.
- [ ] **Step 4: 화면 확인.** 브라우저 창에서 `file://…/docs/1차_연동_실행_안내.html`을 데스크톱 폭과 375px(`resize_window`)로 열고 `document.documentElement.scrollWidth <= innerWidth`를 확인, 밝은 · 어두운 화면 스크린숏.
- [ ] **Step 5: 지적 반영.** 고친 지적과 고치지 않은 지적(까닭)을 기록에 남긴다. 명령이 원본과 달라 원본이 틀렸으면 원본도 같은 커밋에서 고친다.
- [ ] **Step 6: 완료 기준 1 확인 (설계 8절 마지막 줄).** 혼자 시험 구성에서 고객 화면으로 주문해 `접수됨 → 출고 완료`를 브라우저로 보고, 결과를 1차 문서에 한 줄 적는다.
- [ ] **Step 7: 커밋** `docs: 1차 연동 실행 안내(HTML) — 빌드 · .bashrc · 실행 순서 · 주의 사항`

### 마무리

- [ ] 전체 회귀: central · service_bridge · fleet_core · fleet_manager 시험(도메인 87). Expected: 알려진 1건 외 실패 0.
- [ ] 브랜치 전체 검토(가장 능력 있는 모델), 지적 반영, 푸시는 사용자 요청 때.
