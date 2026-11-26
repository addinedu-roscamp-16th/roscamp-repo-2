# fleet 보고 연결 (TT-27) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fleet_manager_node`가 `Hello`로 epoch를 받고 주문 코어의 보고를 `ReportTrip`(브리지) → `ReportOrder`(central) 순서로 보내며, central은 `HELD`를 주문판에 남긴다.

**Architecture:** 순서 · 번호 · 다시 보내기 규칙을 ROS 없는 모듈 `fleet_manager/report_queue.py`에 모으고, 노드는 그 모듈이 내준 요청을 서비스로 부르고 응답을 되돌려 주기만 한다. central은 `board.py`에서 `HELD`를 끝 상태에서 빼고 "조치 대기"로 표시한다.

**Tech Stack:** Python 3.12, ROS 2 Jazzy rclpy, pytest, colcon

**Spec:** `docs/superpowers/specs/2026-11-26-fleet-report-trip-design.md`

## Global Constraints

- 브랜치 `feat/fleet-report-trip` (`dev` a9b4737), 고정 저장소 `/home/ane/dev_ws/roscamp-repo-2`, worktree 만들지 않음.
- `interfaces/` · `contract.yaml`은 바꾸지 않음. 서비스 이름은 contract 키 `hello` · `report_trip` · `report_order`로 읽음 (IR-01).
- 다시 보내기: 응답 한도 3초(`REPORT_REPLY_LIMIT_S`), 간격 1 → 2 → 4 → 8 → 10초(처음 `report_retry_s` 1.0, 최대 `report_retry_max_s` 10.0, 두 배씩), ±20% 지연(`RETRY_JITTER`), 포기하지 않음. 간격은 응답 한도가 지나거나 실패 응답을 받은 **뒤부터** 셈.
- `Hello` · `ReportOrder` 다시 보내기 코드에 `TODO(Q-09)` 주석.
- `Hello.Request.node = "fleet"`, `boot_id` = 노드가 켜질 때 만든 UUID 하나.
- `report_n`: 노드 전체 카운터 하나. 보고를 처음 보낼 때(바꿔 끼운 보고 포함) +1. 같은 보고를 다시 보낼 때는 같은 번호.
- 끝 상태 · 보류 순서: `ReportTrip` `accepted=true` 뒤 `ReportOrder`. 대상 `SHIPPED` · `PARTIAL` · `CANCELED` · `HELD`. `ReportTrip`에는 `CANCELED` → `CANCELLED`, `hold_reason` → `reason`.
- `ReportOrder` `accepted=false`는 다시 보내지 않고 `EventOut("ERROR", "ORDER_REPORT_REFUSED", ...)` (지금 문구 그대로).
- central 끝 상태 = `SHIPPED` · `PARTIAL` · `CANCELED`. `HELD`는 주문판에 남김.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 커밋은 사용자가 실행 방법을 고르며 승인한 범위 안에서만.
- 시험 명령 (ROS 환경): `source /opt/ros/jazzy/setup.bash && source install/setup.bash` 뒤 `python3 -m pytest pc/fleet_core/test pc/fleet_manager/test -q`, `colcon test --packages-select central && colcon test-result --verbose`.

## Review Focus

1. 브리지가 꺼진 채로 fleet이 켜짐 → `Hello`가 늘어나는 간격으로 계속 나가고, 그동안 쌓인 보고는 epoch를 받은 뒤 순서대로 나감 (Task 2 `test_reports_wait_for_the_epoch`).
2. 같은 운반 건에 `ASSIGNED` 응답을 기다리는 중 `FILLING` · `TRANSPORTING`이 연달아 옴 → 기다리는 것은 마지막 하나만, 새 번호로 (Task 2 `test_a_waiting_report_is_replaced_by_the_newest`).
3. `ReportTrip(SHIPPED)` 응답이 늦어 다시 보낸 뒤 앞 요청의 답이 옴 → `ReportOrder`가 한 번만 줄에 오름 (Task 2 `test_a_late_answer_after_expiry_is_ignored`).
4. `HELD` 뒤 `SHIPPED` → central에 `HELD`가 아직 안 갔으면 `SHIPPED`가 바꿔 끼워지고 주문판에서 빠짐 (Task 2 `test_order_reports_of_one_trip_replace_the_waiting_one`, Task 5 `test_held_then_final_removes`).
5. 서비스 준비 안 됨(`service_is_ready()` 거짓) 동안은 간격이 늘지 않고 준비되면 바로 보냄 (Task 2 `test_not_ready_sends_nothing_and_does_not_back_off`).

---

### Task 1: 다시 보내기 간격 설정

**Files:**
- Modify: `pc/fleet_core/fleet_core/common/params.py` (필드 `report_retry_max_s`, 검사)
- Modify: `pc/fleet_core/fleet_core/data/fleet_params.yaml:33` (`report_retry_s: 1.0`, 주석, `report_retry_max_s: 10.0` 줄 추가)
- Test: `pc/fleet_core/test/test_params_validate.py`

**Interfaces:**
- Produces: `FleetParams.report_retry_s: float` (뜻: 처음 간격, 1.0), `FleetParams.report_retry_max_s: float = 10.0` (`report_max_in_flight` 옆, 기본값 있는 칸들 사이)

- [ ] **Step 1: 실패하는 시험**

```python
def test_report_retry_defaults_follow_ir40():
    params = load_params(None)
    assert params.report_retry_s == 1.0
    assert params.report_retry_max_s == 10.0

def test_report_retry_max_is_not_below_the_first(tmp_path):
    with pytest.raises(ValueError, match="report_retry_max_s"):
        load_params(write(tmp_path, {**SHIPPED, "report_retry_s": 4.0, "report_retry_max_s": 2.0}))
```

- [ ] **Step 2:** `python3 -m pytest pc/fleet_core/test/test_params_validate.py -q` → 두 시험 FAIL
- [ ] **Step 3:** 필드 · yaml 값 · `load_params` 검사(`report_retry_max_s >= report_retry_s`, 메시지에 이름) 추가. yaml 주석: "답이 없으면 이만큼 뒤부터 두 배씩(±20%), report_retry_max_s에서 멈춤 (IR-40)".
- [ ] **Step 4:** `python3 -m pytest pc/fleet_core/test -q` → 전부 PASS (기존 1242 passed · 7 xfailed 수준)
- [ ] **Step 5:** 커밋 `feat(fleet_core): 보고 다시 보내기 간격 1 → 10초 설정 (IR-40)`

### Task 2: 보고 대기열 `report_queue.py`

**Files:**
- Create: `pc/fleet_manager/fleet_manager/report_queue.py`
- Test: `pc/fleet_manager/test/test_report_queue.py` (rclpy · fleet_interfaces 필요 없음. `fleet_core`만 import)

**Interfaces:**
- Consumes: `ReportTripCmd`, `ReportOrderCmd`, 상수 `CANCELED` · `HELD` (`fleet_core.common.commands`), `RETRY_JITTER` (`fleet_core.common.params`)
- Produces:

```python
HELLO, TRIP, ORDER = "hello", "trip", "order"

@dataclass(frozen=True)
class TripReport:          # ReportTrip 요청 칸 (ROS 타입 아님)
    trip_id: str
    robot: str
    status: str            # ASSIGNED · FILLING · TRANSPORTING · SHIPPED · PARTIAL · CANCELLED · HELD
    reason: str
    placed: dict[str, int]
    missing: dict[str, int]
    epoch: int
    n: int
    stamp: float           # 보고를 처음 보낼 때의 now

@dataclass(frozen=True)
class Send:
    key: int               # 요청 번호 (큐 안에서 유일)
    kind: str              # HELLO | TRIP | ORDER
    payload: object        # HELLO: boot_id(str), TRIP: TripReport, ORDER: ReportOrderCmd

class ReportQueue:
    def __init__(self, boot_id: str, first_s: float, max_s: float, reply_limit_s: float,
                 max_in_flight: int, rand=random.random): ...
    epoch: int | None                                  # 읽기 전용 속성
    def add(self, cmd) -> None                          # ReportTripCmd | ReportOrderCmd
    def due(self, now: float, ready: dict[str, bool]) -> list[Send]
    def expire(self, now: float) -> list[int]          # 응답 한도를 넘긴 요청 key (노드가 remove_pending_request)
    def answer(self, key: int, now: float, accepted: bool | None, epoch: int = 0) -> ReportOrderCmd | None
        # accepted None = 답 없음 · 예외. 돌려주는 값 = central이 거절한 ReportOrderCmd (노드가 ORDER_REPORT_REFUSED)
```

규칙 (설계 5.1): 칸(slot)은 `(TRIP, trip_id)` · `(ORDER, trip_id)` 마다 하나. 칸에는 "보내는 중" 하나와 "기다리는 최신" 하나. 새 보고는 보내는 중인 것이 있으면 기다리는 것을 바꿔 끼움. 실패(답 없음 · 한도 넘김 · `TRIP`/`HELLO`의 `accepted=false`) 뒤 다음 보낼 시각 = 실패 시각 + 간격 × 지연 배수, 간격 단계 +1. 실패한 칸에 기다리는 최신 보고가 있으면 다음 번에는 그것을 새 번호로 보냄(단계 이어 씀). 성공하면 칸의 단계 0. `HELLO`는 epoch가 생길 때까지 칸 하나로 같은 `boot_id`를 다시 보냄. `epoch`가 없으면 `TRIP` · `ORDER`는 내보내지 않음. `TRIP` 성공이고 원래 명령이 `ReportOrderCmd`면 `(ORDER, trip_id)` 칸에 그 명령을 넣음. 답을 기다리는 `TRIP` + `ORDER` 수 ≤ `max_in_flight`. `ready[kind]`가 거짓이면 그 종류는 내보내지 않고 단계도 그대로. 모르는 key의 `answer`는 무시하고 `None`.

간격 계산: `min(first_s * 2 ** step, max_s) * (1 - RETRY_JITTER + 2 * RETRY_JITTER * rand())`.

- [ ] **Step 1: 실패하는 시험** (공통 도우미: `q = ReportQueue("boot-1", 1.0, 10.0, 3.0, 4, rand=lambda: 0.5)` — 지연 배수 1.0, `READY = {HELLO: True, TRIP: True, ORDER: True}`, `def ready_queue(**kw)`: 큐를 만들고 첫 `due`의 hello에 `answer(key, 0.0, True, 7)`까지 마친 큐를 돌려줌. 아래 `...`는 이 도우미. 단 처음 두 시험은 hello 전 상태라 새 `ReportQueue(...)`를 씀)

```python
def test_hello_is_sent_first_and_resent_with_the_same_boot_id():
    q = ...; [s] = q.due(0.0, READY)
    assert (s.kind, s.payload) == (HELLO, "boot-1")
    assert q.expire(3.0) == [s.key]
    assert q.due(3.9, READY) == [] and q.due(4.0, READY)[0].payload == "boot-1"

def test_reports_wait_for_the_epoch():
    q = ...; q.add(ReportTripCmd("T-1", "pode1", "ASSIGNED", {}, {}))
    [h] = q.due(0.0, READY)
    assert h.kind == HELLO and q.epoch is None
    q.answer(h.key, 0.0, True, 7)
    assert q.epoch == 7
    [s] = q.due(0.1, READY)
    assert s.kind == TRIP and (s.payload.epoch, s.payload.n, s.payload.status) == (7, 1, "ASSIGNED")

def test_backoff_doubles_to_ten_seconds_after_each_failure():
    # 답 없음 → 3초 한도 → 1, 2, 4, 8, 10, 10초 뒤 다시 보냄. 다시 보낼 때 n은 같음
    sent_at = []  # due가 TRIP을 낸 시각 목록
    ...
    assert gaps == [3 + 1, 3 + 2, 3 + 4, 3 + 8, 3 + 10, 3 + 10]
    assert {s.payload.n for s in sends} == {1}

def test_a_trip_refusal_is_retried_like_no_answer():
    # answer(key, t, False) 뒤 1초에 같은 n으로 다시

def test_a_waiting_report_is_replaced_by_the_newest():
    # ASSIGNED 보내는 중에 FILLING, TRANSPORTING 추가 → ASSIGNED 성공 뒤 다음 TRIP은 TRANSPORTING, n=2
    assert (nxt.payload.status, nxt.payload.n) == ("TRANSPORTING", 2)

def test_numbers_count_across_trips():
    # T-1 ASSIGNED, T-2 ASSIGNED 둘 다 보냄 → n 1, 2 (운반 건과 상관없이)

def test_one_trip_has_one_request_in_flight():
    # T-1에 보내는 중인 요청이 있으면 due는 T-1을 또 내지 않음

def test_final_report_goes_to_central_only_after_the_bridge_accepts():
    q.add(ReportOrderCmd("T-1", "pode1", SHIPPED, {"A": 1}, {}, ""))
    [t] = q.due(...); assert t.kind == TRIP and t.payload.status == "SHIPPED"
    assert q.due(..) == []                      # 아직 ORDER 없음
    q.answer(t.key, now, True)
    [o] = q.due(..); assert o.kind == ORDER and o.payload.status == SHIPPED

@pytest.mark.parametrize("status, hold, trip_status, reason", [
    (CANCELED, "", "CANCELLED", ""), (HELD, HOLD_ROBOT_FAULT, HELD, HOLD_ROBOT_FAULT)])
def test_order_status_is_spelled_for_the_bridge(...):
    # TRIP payload.status · reason 확인, 뒤 ORDER payload는 원래 명령 그대로

def test_central_refusal_is_returned_and_not_retried():
    assert q.answer(o.key, now, False) == cmd
    assert q.due(now + 60, READY) == []

def test_order_without_answer_is_retried():
    # ORDER 답 없음 → 한도 뒤 1초에 다시

def test_order_reports_of_one_trip_replace_the_waiting_one():
    # HELD의 ORDER가 보내는 중일 때 SHIPPED TRIP 성공 → ORDER 칸 기다리는 것 = SHIPPED. HELD 답 없음 → 다음 ORDER는 SHIPPED

def test_in_flight_limit_counts_trip_and_order():
    # max_in_flight=1: T-1 TRIP 보내는 중이면 T-2는 다음 due에서야

def test_not_ready_sends_nothing_and_does_not_back_off():
    # ready[TRIP]=False 동안 due 빈 목록, 준비되면 그 시각에 바로, 단계 0(실패 뒤 간격 1초)

def test_a_late_answer_after_expiry_is_ignored():
    # 한도 넘긴 요청 key에 answer(True) → None, ORDER 줄에 오르지 않음. 다시 보낸 요청의 성공으로만 한 번
```

- [ ] **Step 2:** `python3 -m pytest pc/fleet_manager/test/test_report_queue.py -q` → import 오류로 FAIL
- [ ] **Step 3:** `report_queue.py` 구현. `Hello`와 `ORDER` 다시 보내기 자리에 `# TODO(Q-09): IR-40 표에 Hello · ReportOrder 줄이 없어 Report* 값을 임시로 씀`.
- [ ] **Step 4:** 같은 명령 → 전부 PASS
- [ ] **Step 5:** 커밋 `feat(fleet_manager): 보고 대기열 — Hello 뒤 ReportTrip, 번호 · 순서 · 바꿔 끼우기, 늘어나는 간격`

### Task 3: 요청 · 응답 변환 (adapters)

**Files:**
- Modify: `pc/fleet_manager/fleet_manager/adapters.py` (import `Hello`, `ReportTrip`)
- Test: `pc/fleet_manager/test/test_order_report_adapters.py`

**Interfaces:**
- Consumes: `TripReport` (Task 2)
- Produces: `hello_request(boot_id: str) -> Hello.Request` (`node="fleet"`), `hello_answer(response) -> tuple[bool, int]`, `report_trip_request(report: TripReport) -> ReportTrip.Request` (`report_epoch` · `report_n` · `robot_id` · `stamp=to_time(report.stamp)` · `placed`/`missing`는 `to_order_items`), `report_trip_accepted(response) -> bool`. 기존 `report_request` · `report_accepted`(ReportOrder)은 그대로.

- [ ] **Step 1: 실패하는 시험**

```python
def test_hello_request_names_the_fleet():
    req = adapters.hello_request("boot-1")
    assert (req.node, req.boot_id) == ("fleet", "boot-1")

def test_report_trip_request_carries_numbers_and_items():
    r = TripReport("T-1", "pode1", "SHIPPED", "", {"chocolate_bar": 2}, {}, 7, 3, 12.5)
    req = adapters.report_trip_request(r)
    assert (req.trip_id, req.robot_id, req.status, req.report_epoch, req.report_n) == ("T-1", "pode1", "SHIPPED", 7, 3)
    assert [(i.kind, i.count) for i in req.placed] == [("chocolate_bar", 2)]
    assert (req.stamp.sec, req.stamp.nanosec) == (12, 500_000_000)

def test_hello_answer_reads_epoch():
    assert adapters.hello_answer(Hello.Response(accepted=True, epoch=7)) == (True, 7)
```

- [ ] **Step 2:** `python3 -m pytest pc/fleet_manager/test/test_order_report_adapters.py -q` → FAIL
- [ ] **Step 3:** 함수 네 개 추가
- [ ] **Step 4:** 같은 명령 → PASS
- [ ] **Step 5:** 커밋 `feat(fleet_manager): Hello · ReportTrip 요청 변환`

### Task 4: 노드 연결

**Files:**
- Modify: `pc/fleet_manager/fleet_manager/fleet_manager_node.py` (import · `__init__` 125줄 근처 · 213줄 근처 클라이언트 · `_dispatch` 428줄 · `_flush_reports` · `_report_answered`)
- Modify: `pc/fleet_manager/fleet_manager/goal_tracker.py` (`_Report` · `ReportOutbox` 지움, `_factor`는 남김)
- Test: `pc/fleet_manager/test/test_report_flush.py` (다시 씀, 마지막 `robots_hz` 시험은 그대로)

**Interfaces:**
- Consumes: `ReportQueue`, `Send`, `HELLO`/`TRIP`/`ORDER` (Task 2), adapters (Task 3), `params.report_retry_s` · `report_retry_max_s` · `report_max_in_flight` (Task 1)
- Produces (노드 속성, 시험이 씀): `self._reports: ReportQueue`, `self._clients: dict[str, client]` (`HELLO` → `/bridge/hello`, `TRIP` → `/bridge/report_trip`, `ORDER` → `/central/report_order`, 모두 inbox 콜백 그룹), `self._pending: dict[int, Future]`, `_flush_reports(now)`, `_report_answered(send: Send, future)`

동작:
- `__init__`: `boot_id = str(uuid.uuid4())`, `ReportQueue(boot_id, params.report_retry_s, params.report_retry_max_s, REPORT_REPLY_LIMIT_S, params.report_max_in_flight)`.
- `_dispatch`: `case ReportTripCmd() | ReportOrderCmd(): self._reports.add(command)`.
- `_flush_reports(now)`: `expire` key마다 그 종류의 클라이언트에서 `remove_pending_request(self._pending.pop(key))`. `due(now, {k: c.service_is_ready() for k, c in self._clients.items()})`의 요청마다 adapters로 만들어 `call_async`, `_pending[key]`에 넣고 `add_done_callback(partial(self._post, self._report_answered, send))`.
- `_report_answered(send, future)`: `_pending.pop(send.key, None)`. 예외면 경고 로그 후 `answer(key, now, None)`. `HELLO`면 `hello_answer`로 `(accepted, epoch)`, 처음 epoch를 받으면 INFO `bridge epoch {epoch} (boot_id {boot_id})`. `TRIP` · `ORDER`는 `*_accepted`. `answer`가 `ReportOrderCmd`를 돌려주면 지금과 같은 `ORDER_REPORT_REFUSED` 이벤트.

- [ ] **Step 1: 실패하는 시험** (지금 파일의 `_ReportClient` · `SimpleNamespace` 노드 방식 유지, 클라이언트 셋)

```python
def test_assigned_goes_to_the_bridge_after_hello():
    node = report_node()
    FleetManagerNode._dispatch(node, ReportTripCmd("T-1", "pode1", "ASSIGNED", {}, {}), 0.0)
    FleetManagerNode._flush_reports(node, 0.0)
    assert [len(c.sent) for c in node._clients.values()] == [1, 0, 0]   # hello만
    answer(node._clients[HELLO].sent[0], Hello.Response(accepted=True, epoch=7))
    FleetManagerNode._flush_reports(node, 0.1)
    [req] = node._clients[TRIP].requests
    assert (req.trip_id, req.status, req.report_epoch, req.report_n) == ("T-1", "ASSIGNED", 7, 1)

def test_shipped_reaches_central_after_the_bridge_accepts(): ...
def test_a_refused_order_report_raises_the_event():  # events == ["ORDER_REPORT_REFUSED"]
def test_a_request_past_the_reply_limit_is_removed_from_its_client():  # 3.0초에 removed == sent[:1]
def test_a_failed_reply_keeps_the_report_for_sending_again():  # future 예외 → 1초 뒤 다시 (Task 2 간격)
```

- [ ] **Step 2:** `python3 -m pytest pc/fleet_manager/test/test_report_flush.py -q` → FAIL
- [ ] **Step 3:** 노드 수정, `ReportOutbox` 지움
- [ ] **Step 4:** `python3 -m pytest pc/fleet_manager/test -q` → 전부 PASS. `grep -rn ReportOutbox pc/` → 결과 없음. `uvx ruff check pc/fleet_manager` → 통과
- [ ] **Step 5:** 커밋 `feat(fleet_manager): Hello · ReportTrip 클라이언트, 끝 상태는 ReportTrip 뒤 ReportOrder (TT-27)`

### Task 5: central의 `HELD` 처리

**Files:**
- Modify: `pc/central/central/board.py` (`FINAL_STATUSES`, `HELD`, `report`, `held()`)
- Modify: `pc/central/central/central_node.py:52-63` (`_report`)
- Test: `pc/central/test/test_board.py`, `pc/central/test/test_central_node.py`

**Interfaces:**
- Produces: `FINAL_STATUSES = frozenset({"SHIPPED", "PARTIAL", "CANCELED"})`, `HELD = "HELD"`, `Board.report(order_id, status) -> bool` (끝 상태 · `HELD`면 `True`, 그 밖 `False`), `Board.held() -> set[str]` (조치 대기 중인 trip_id)

- [ ] **Step 1: 실패하는 시험**

```python
@pytest.mark.parametrize("st", ["SHIPPED", "PARTIAL", "CANCELED"])
def test_final_report_removes_once(st): ...            # 기존에서 HELD만 뺌

def test_held_stays_on_the_board_and_is_marked():
    b = Board(); b.submit("t1", {"A": 1}, NOW)
    assert b.report("t1", "HELD") is True and b.report("t1", "HELD") is True
    assert [e.trip_id for e in b.entries()] == ["t1"] and b.held() == {"t1"}

def test_held_then_final_removes():
    ...; b.report("t1", "HELD"); assert b.report("t1", "PARTIAL") is True
    assert b.entries() == [] and b.held() == set()

def test_held_for_an_unknown_trip_is_accepted():
    assert Board().report("zz", "HELD") is True and Board().held() == set()
```

`test_central_node.py`: `HELD` 요청 → `accepted=True`, 주문판에 그대로, 로그에 "관리자 조치 대기" (기존 시험의 로그 확인 방식을 따름).

- [ ] **Step 2:** `colcon build --packages-select central && colcon test --packages-select central && colcon test-result --verbose` → 새 시험 FAIL
- [ ] **Step 3:** 구현. `central_node._report`: 상태가 끝 상태도 `HELD`도 아니면 지금처럼 경고. `HELD`면 INFO `관리자 조치 대기: order_id=... hold_reason=...`. 파일 머리 주석의 "끝 상태" 설명을 고침.
- [ ] **Step 4:** 같은 명령 → 전부 PASS
- [ ] **Step 5:** 커밋 `feat(central): HELD는 주문판에 남기고 관리자 조치 대기로 표시 (CEN-03)`

### Task 6: 문서

**Files:**
- Modify: `docs/설계/주문_흐름_1차_인터페이스.md` (4.3 표 2번 줄, 4.4 ③ 마지막 줄, 4.3 표 F-10 줄, 6.2 표 F-10 줄, 머리 상태 줄에 "수정 2026-11-26 (TT-27: `HELD`도 `ReportTrip` 뒤 `ReportOrder`, central은 `HELD`를 주문판에 남김)")
- Modify: `pc/central/README.md` 또는 `pc/fleet_manager/README.md`에 `ReportOrder` · `HELD` 설명이 있으면 같은 뜻으로 고침 (`grep -n -E "ReportOrder|HELD" pc/central/README.md pc/fleet_manager/README.md`로 확인)
- Add: 설계 문서 · 이 계획 파일

- [ ] **Step 1:** 위 문장 고침. 표 칸 안에 `|` 글자를 쓰지 않음.
- [ ] **Step 2:** `git diff --stat` 로 문서만 바뀐 것 확인
- [ ] **Step 3:** 커밋 `docs: TT-27 설계 · 계획, 1차 인터페이스 HELD 보고 순서`

### Task 7: 전체 시험과 T-10 재시험

**Files:**
- Modify: `reports/스프린트_3회차/주문_배정_연동_테스트_메모.md` (T-10 줄 결과, T-10 사유, T-01 확인 방법 바뀜, 이번 실행 기록)

- [ ] **Step 1:** `colcon build --symlink-install --packages-select fleet_core fleet_manager central service_bridge` → 오류 없음
- [ ] **Step 2:** `python3 -m pytest pc/fleet_core/test pc/fleet_manager/test -q`, `colcon test --packages-select central && colcon test-result --verbose`, `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q` → 모두 통과 (service_bridge는 `dev`에서도 실패하는 `test_fifth_failure_rests_and_alerts` 1건 제외)
- [ ] **Step 3 (사용자와 함께, 개인 도메인 · `LOCALHOST`):** `setup_db` → `ros2 run service_bridge bridge --ros-args -p mode:=NORMAL` → `ros2 run central central` → `ros2 launch iponic_bringup pc.launch.py use_fake:=true fake_central:=false scenario:=real_two_orders robots:="pode1 pode2" fake_robots:="pode1 pode2"` → 고객 화면 `http://localhost:8000/customer/?account=<setup_db가 출력한 tester id>`에서 주문 한 건.
  Expected: fleet 로그 `bridge epoch N`, 브리지 로그 `APPLIED`, 고객 화면 접수됨 → 준비 중, `SELECT status, report_epoch, report_n FROM trip` → `ASSIGNED`, N, 1 이상
- [ ] **Step 4:** 메모 T-10 줄을 결과로 바꾸고 근거(로그 줄, DB 줄)를 적음. T-01 확인 방법에 "11-26 재시험은 고객 화면 → 웹 API → DB → 브리지 → central" 추가.
- [ ] **Step 5:** 커밋 `test: T-10 재시험 — fleet ReportTrip(ASSIGNED)이 DB에 반영됨`
