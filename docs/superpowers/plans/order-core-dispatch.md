# fleet 주문 코어 — 배정 단계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fleet 주문 코어에 주문판 처리 · 작업대 호출 · 배정 응답 처리를 넣어, 주문판에 들어온 주문이 Pode에 묶이게(배정) 합니다. 코어 통합 시험으로 확인하고, 연동 테스트 보고서에 쓸 근거를 모읍니다.

**Architecture:** `OrderOrchestrator`의 빈 함수 가운데 `on_order_board` · `on_dispatch_feedback` · `on_dispatch_result`와 조회 함수만 채웁니다. Pode 고르기 · 이동 · 도킹은 이미 있는 교통 층이 하고, 두 층은 `FleetManager`의 명령 전달(`manager_route.py`)로 이미 이어져 있습니다. 확인은 ROS 없이 `FleetManager`에 실제 교통 층을 붙인 코어 통합 시험으로 하고, 실 주행 · 가짜 로봇 실행은 하지 않습니다.

**Tech Stack:** Python 3.12, pytest, ROS 2 Jazzy 환경(빌드된 `fleet_interfaces`만 필요), `fleet_core` d2 시험 지도

**Spec:** `pc/fleet_core/fleet_core/orders/pseudocode.md` 2 · 3장(주문 묶음, WORK로 이동)과 `docs/설계/주문_흐름_1차_인터페이스.md`의 "② 단계 대응표"(`ASSIGNED`는 묶을 때 냄)

## 데이터 흐름 (시험이 확인하는 것)

보고서 항목과 모을 근거는 `reports/스프린트_3회차/주문_배정_연동_테스트_메모.md`에 있습니다. 이 계획은 그 메모의 T-03 ~ T-09를 시험으로 확인할 수 있게 만듭니다.

| # | 데이터 | 보내는 곳 → 받는 곳 | 시험에서 보는 값 |
|---|---|---|---|
| 1 | 주문판(`OrderRecord`) | central → FleetManager → 주문 코어 | 기록 줄 `in: on_order_board` |
| 2 | 작업대 호출 `DispatchCmd(id, WORK)` (로봇 이름 없음) | 주문 코어 → 교통 층 | 기록 줄 `inside: DispatchCmd` |
| 3 | Pode 고르기 → 이동 명령 | 교통 층 → (ROS 쪽) Pode | 출력의 `MoveOrderCmd` · `MoveCmd` · `DockCmd` |
| 4 | 도착 응답 `DispatchResult(id, True, Pode)` | 교통 층 → 주문 코어 | 기록 줄 `inside: DispatchResult` |
| 5 | 배정 알림 `EventOut(INFO, ORDER_BOUND)` · `ReportTripCmd(ASSIGNED)` | 주문 코어 → (ROS 쪽) `/central/events` · ReportTrip | 출력과 기록 줄 `out:` |
| 6 | 배정 조회 `AssignmentView(Pode, 주문, WORK, FILL)` | FleetManager → `/fleet/assignments` | `assignments_view()` |

- 5번의 `ReportTripCmd`는 코어 출력까지만 확인합니다. 노드가 이것을 `ReportTrip` 서비스로 보내는 일은 TT-27이라 이번 범위가 아닙니다(지금 노드는 이 명령을 받아도 아무것도 하지 않음).

## 범위

- 넣는 것: 대기 주문 고르기(주문판 순서), 바구니 용량 검사 · 경고, 작업대 호출 열기 · 거두기, 맡을 Pode 없음 경고, 도착한 Pode에 주문 묶기 · 배정 알림, 조회(`order_of` · `robot_view`).
- 넣지 않는 것: 채우기 · 출고 · 교환 · 보고(`ReportOrderCmd`), 고장 처리(치움 · 격리 · 다시 투입 · 늦은 팔 결과), 강제 출고, 노드의 ReportTrip 전송, 가짜 로봇 · 실물 실행.
- 묶인 뒤 Pode는 작업대에 붙잡힌 채 기다립니다(채우기가 없으므로). 다음 단계는 pseudocode 4장 채우기입니다.
- 보고서 작성 · 형식은 이 계획 밖입니다. 개발을 마친 뒤 따로 논의합니다.

## Global Constraints

- 바꾸지 않는 접점: `OrderOrchestrator(stations, params, robots, clock)`와 입력 · 조회 함수의 이름 · 시그니처(pseudocode 1장). 범위 밖 함수(`on_arm_report`, `step`, `on_robot_removed`, `on_robot_reinstated`, `on_robot_isolated`)는 출력 없이 둡니다. `on_force_ship`은 그대로 `(False, "NOT_IMPLEMENTED")`.
- WORK 스테이션 = `stations` 가운데 `action == FILL`(`"fill"`)인 것의 id. 시험 지도 · `test/stations.py`에서는 `"WORK"`.
- 용량 = `params.basket_capacity`(지금 4). 품목 수 합이 용량보다 **크면** 배정하지 않습니다(4는 받음).
- 호출 id는 주문 코어 안의 일련번호로 1부터 올립니다(pseudocode 2장). 다른 goal 번호와는 따로입니다.
- 내는 값은 아래 글자 그대로:
  - `EventOut("WARN", "ORDER_OVER_CAPACITY", f"{order_id}: {n} items exceed the basket capacity of {cap}")`
  - `EventOut("WARN", "NO_ROBOT_AVAILABLE", f"{ids} wait and no robot can take them")` (`ids`는 대기 주문 id 목록의 `repr`, 예 `['T-1', 'T-2']`)
  - `EventOut("INFO", "ORDER_BOUND", order_id, robot)`
  - `ReportTripCmd(order_id, robot, TRIP_ASSIGNED, {}, {})`
- 운행 모드(`RUN` · `PAUSE` · `STOP`)는 저장만 하고 호출 열기 · 묶기에 쓰지 않습니다(pseudocode 4장).
- 인터페이스(`contract.yaml` · srv · msg)는 바꾸지 않습니다.
- 주석은 팀 형식(파일 첫 줄 `# ---- … ----`, 문단마다 역할 한 줄, 이유만 덧붙임, 날짜 · 이력 없음). `uvx ruff check pc/fleet_core` 통과(줄 100자).
- 문서에 날짜를 적지 않습니다.
- 시험 명령(ROS 환경을 연 셸): `source /opt/ros/jazzy/setup.bash && source ~/iponic_ws/install/setup.bash`, 그 뒤 `python3 -m pytest pc/fleet_core/test -q`, `python3 -m pytest pc/fleet_manager/test -q`. 시작 전 기준: fleet_core 1225 passed · 7 xfailed, fleet_manager 63 passed.
- 브랜치 `feat/order-core-dispatch`(`dev`에서). 커밋은 작업마다 하고, 푸시 · PR은 사용자 확인 뒤.
- 교통 층 · manager는 고치지 않습니다. 고칠 일이 생기면 멈추고 사용자에게 알립니다(관제 쪽 코드).
- `fleet_core`는 관제 쪽과 같이 쓰는 코드라 PR 리뷰를 관제 담당(박정욱)에게 요청합니다.

## Review Focus

1. central이 같은 주문판을 1초마다 다시 보냄 → 호출이 늘어나지 않아야 함 (Task 1 `test_the_same_board_again_opens_nothing_more`)
2. Pode가 오는 사이 주문판이 줄어듦 → 가장 나중 호출부터 거두고, 도착한 Pode에는 지금 주문판의 맨 앞 대기 주문을 묶어야 함 (Task 1 `test_a_shorter_board_cancels_the_newest_call_first`, Task 2 `test_the_first_waiting_order_is_bound_whatever_call_answers`)
3. 묶인 주문이 주문판에서 빠졌다가 다시 들어옴 → 묶인 채로 남고 다시 호출하지 않아야 함 (Task 2 `test_a_bound_order_stays_bound_and_is_not_called_again`)
4. 용량을 넘는 주문이 보통 주문과 섞임 → 경고 한 번, 보통 주문은 그대로 배정 (Task 3 `test_an_order_over_capacity_is_warned_and_the_other_is_still_bound`)
5. 실패한 호출 · 모르는 로봇의 도착 답 → 호출을 다시 열고 주문을 잃지 않아야 함 (Task 2 `test_a_failed_call_is_reopened_with_a_new_id`, `test_an_answer_from_an_unknown_robot_reopens_the_call`)

## 파일

| 파일 | 하는 일 |
|---|---|
| `pc/fleet_core/fleet_core/orders/orchestrator.py` (고침) | 주문판 · 호출 · 배정 처리 |
| `pc/fleet_core/fleet_core/orders/pseudocode.md` (고침) | "지금 주문 코어는 뼈대" 문장을 구현된 범위로 고침 |
| `pc/fleet_core/test/test_orders_skeleton.py` (고침) | 아직 빈 입력만 "출력 없음"으로 남김 |
| `pc/fleet_core/test/test_orders_dispatch.py` (새로) | 주문 코어 단위 시험 |
| `pc/fleet_core/test/test_order_flow.py` (새로) | 코어 통합 시험(FleetManager + 교통 층 + 주문 코어) |
| `reports/스프린트_3회차/주문_배정_연동_테스트_메모.md` (고침) | 끝에 "모은 근거" 절을 더함 |

---

### Task 1: 주문판 처리 — 대기 주문 · 용량 · 작업대 호출

**Files:**
- Modify: `pc/fleet_core/fleet_core/orders/orchestrator.py` (`__init__`, `on_order_board`, `take_output`)
- Modify: `pc/fleet_core/test/test_orders_skeleton.py`
- Create: `pc/fleet_core/test/test_orders_dispatch.py`

**Interfaces:**
- Consumes: `OrderRecord(order_id: str, items: dict[str, int])`, `DispatchCmd(goal_id, station_id, robot_id="")`, `CancelDispatchCmd(goal_id)`, `EventOut(level, code, detail, robot="")`, `FILL` (모두 `fleet_core.common.commands`), `load_params().basket_capacity`
- Produces: Task 2가 쓰는 내부 상태 — 주문판 목록, 열린 호출 id 목록(연 순서), 로봇 → 묶인 `OrderRecord`, 대기 주문을 구하는 함수 하나와 호출 수를 맞추는 함수 하나(이름은 구현자가 정함)

- [ ] **Step 1: 브랜치 확인**

Run: `git branch --show-current`
Expected: `feat/order-core-dispatch`

- [ ] **Step 2: 실패하는 시험 작성** — `test_orders_dispatch.py`

```python
from fleet_core.common.commands import CancelDispatchCmd, DispatchCmd, EventOut, OrderRecord
from fleet_core.common.params import load_params
from fleet_core.orders.orchestrator import OrderOrchestrator
from stations import STATIONS

T1, T2 = OrderRecord("T-1", {"A": 1}), OrderRecord("T-2", {"B": 2})


def make(robots=("r1", "r2")):
    return OrderOrchestrator(STATIONS, load_params(), list(robots), lambda: 0.0)


def test_each_waiting_order_opens_one_unnamed_work_call():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == [DispatchCmd(1, "WORK"), DispatchCmd(2, "WORK")]


def test_the_same_board_again_opens_nothing_more():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == []


def test_a_shorter_board_cancels_the_newest_call_first():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_order_board("RUN", [T1])
    assert o.take_output() == [CancelDispatchCmd(2)]


def test_the_run_mode_does_not_hold_calls_back():
    o = make()
    o.on_order_board("PAUSE", [T1])
    assert o.take_output() == [DispatchCmd(1, "WORK")]


def test_four_items_fit_the_basket():
    o = make()
    o.on_order_board("RUN", [OrderRecord("T-4", {"A": 4})])
    assert o.take_output() == [DispatchCmd(1, "WORK")]


def test_an_order_over_capacity_warns_once_and_opens_no_call():
    o = make()
    big = OrderRecord("T-9", {"A": 3, "B": 2})
    o.on_order_board("RUN", [big])
    assert o.take_output() == [
        EventOut("WARN", "ORDER_OVER_CAPACITY", "T-9: 5 items exceed the basket capacity of 4")]
    o.on_order_board("RUN", [big])
    assert o.take_output() == []


def test_an_order_that_left_the_board_can_warn_again():
    o = make()
    big = OrderRecord("T-9", {"A": 5})
    o.on_order_board("RUN", [big])
    o.take_output()
    o.on_order_board("RUN", [])
    o.on_order_board("RUN", [big])
    assert [e.code for e in o.take_output()] == ["ORDER_OVER_CAPACITY"]
```

- [ ] **Step 3: 실패 확인**

Run: `python3 -m pytest pc/fleet_core/test/test_orders_dispatch.py -q`
Expected: 7개 FAIL (출력이 `[]`)

- [ ] **Step 4: `on_order_board(run_mode, orders) -> None`과 `take_output() -> list` 구현** (`orchestrator.py`)

주문판을 통째로 바꾸고, 용량 기록은 새 주문판에 남은 · 여전히 넘는 id만 남깁니다. 대기 주문 = 주문판 순서대로, 묶이지 않았고 용량 안인 주문. 열린 호출 수가 대기 주문 수보다 적으면 새 id로 `DispatchCmd(id, WORK)`, 많으면 가장 나중에 연 것부터 `CancelDispatchCmd(id)`. `take_output()`은 쌓은 목록을 주고 비웁니다.

- [ ] **Step 5: 뼈대 시험 고치기** (`test_orders_skeleton.py`)

`test_no_input_gives_any_output`의 이름을 `test_inputs_outside_this_scope_give_no_output`로 바꾸고, 목록에서 `on_order_board` · `on_dispatch_feedback` · `on_dispatch_result` 세 줄을 뺍니다. 나머지 시험은 그대로 둡니다.

- [ ] **Step 6: 통과 확인**

Run: `python3 -m pytest pc/fleet_core/test/test_orders_dispatch.py pc/fleet_core/test/test_orders_skeleton.py pc/fleet_core/test/test_input_reject.py -q`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
git add pc/fleet_core/fleet_core/orders/orchestrator.py pc/fleet_core/test/test_orders_dispatch.py pc/fleet_core/test/test_orders_skeleton.py
git commit -m "feat(orders): 주문판 처리 — 대기 주문, 바구니 용량 경고, 작업대 호출 열기 · 거두기"
```

### Task 2: 배정 응답 처리 — 도착한 Pode에 주문 묶기

**Files:**
- Modify: `pc/fleet_core/fleet_core/orders/orchestrator.py` (`on_dispatch_feedback`, `on_dispatch_result`, `order_of`, `robot_view`)
- Test: `pc/fleet_core/test/test_orders_dispatch.py` (이어서)

**Interfaces:**
- Consumes: Task 1의 내부 상태, `NO_ROBOT`, `TRIP_ASSIGNED`, `ReportTripCmd(trip_id, robot, status, placed, missing, reason="")`
- Produces: `order_of(robot) -> str`(묶인 주문 id, 없으면 `""`), `robot_view(robot) -> dict`(묶이면 `{"stage": "FILLING", "basket": "EMPTY", "order": 주문 id, "placed": {}}`, 아니면 지금과 같음, 모르는 로봇은 `{}`)

- [ ] **Step 1: 실패하는 시험 추가** — `test_orders_dispatch.py`에 이어서 (import에 `NO_ROBOT`, `TRIP_ASSIGNED`, `ReportTripCmd` 추가)

```python
def bound_to_r1():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    return o


def test_an_answered_call_binds_the_first_waiting_order_to_that_pode():
    o = bound_to_r1()
    assert o.take_output() == [EventOut("INFO", "ORDER_BOUND", "T-1", "r1"),
                               ReportTripCmd("T-1", "r1", TRIP_ASSIGNED, {}, {})]
    assert o.order_of("r1") == "T-1"
    assert o.robot_view("r1") == {"stage": "FILLING", "basket": "EMPTY", "order": "T-1",
                                  "placed": {}}


def test_the_first_waiting_order_is_bound_whatever_call_answers():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_result(2, True, "r2")
    assert o.order_of("r2") == "T-1"
    o.take_output()
    o.on_dispatch_result(1, True, "r1")
    assert o.order_of("r1") == "T-2"


def test_a_bound_order_stays_bound_and_is_not_called_again():
    o = bound_to_r1()
    o.take_output()
    o.on_order_board("RUN", [T2])
    assert o.take_output() == []
    assert o.order_of("r1") == "T-1"
    o.on_order_board("RUN", [T1, T2])
    assert o.take_output() == []


def test_a_failed_call_is_reopened_with_a_new_id():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, False, "", "NO_SUCH_ROBOT")
    assert o.take_output() == [DispatchCmd(2, "WORK")]
    assert o.order_of("r1") == "" and o.order_of("r2") == ""


def test_an_answer_from_an_unknown_robot_reopens_the_call():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(1, True, "ghost")
    assert o.take_output() == [DispatchCmd(2, "WORK")]


def test_an_answer_to_a_call_that_is_not_open_is_ignored():
    o = make()
    o.on_order_board("RUN", [T1])
    o.take_output()
    o.on_dispatch_result(7, True, "r1")
    assert o.take_output() == [] and o.order_of("r1") == ""


def test_no_robot_feedback_names_the_waiting_orders():
    o = make()
    o.on_order_board("RUN", [T1, T2])
    o.take_output()
    o.on_dispatch_feedback(1, "", NO_ROBOT)
    assert o.take_output() == [EventOut("WARN", "NO_ROBOT_AVAILABLE",
                                        "['T-1', 'T-2'] wait and no robot can take them")]
    o.on_dispatch_feedback(1, "r1", "MOVING")
    assert o.take_output() == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest pc/fleet_core/test/test_orders_dispatch.py -q`
Expected: 새 7개 FAIL

- [ ] **Step 3: 구현** (`orchestrator.py`)

`on_dispatch_feedback(goal_id, robot_id, phase)`: `phase == NO_ROBOT`일 때만 경고. `on_dispatch_result(goal_id, success, robot_id, reason="")`: 열린 호출이 아니면 무시. 열린 호출이면 목록에서 빼고, 성공이고 아는 로봇이며 그 로봇에 묶인 주문이 없을 때 맨 앞 대기 주문을 묶고 `ORDER_BOUND` · `ReportTripCmd(ASSIGNED)`를 이 순서로 냅니다. 그 뒤 성공 · 실패와 상관없이 호출 수를 다시 맞춥니다.

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest pc/fleet_core/test/test_orders_dispatch.py pc/fleet_core/test/test_orders_skeleton.py pc/fleet_core/test/test_input_reject.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add pc/fleet_core/fleet_core/orders/orchestrator.py pc/fleet_core/test/test_orders_dispatch.py
git commit -m "feat(orders): 배정 응답 — 도착한 Pode에 주문을 묶고 ORDER_BOUND · ASSIGNED를 냄"
```

### Task 3: 코어 통합 시험 — 주문판에서 Pode 배정까지

**Files:**
- Create: `pc/fleet_core/test/test_order_flow.py`
- Modify: `pc/fleet_core/fleet_core/orders/pseudocode.md` (머리의 "지금 팀 main의 주문 코어는 뼈대다" 단락)

**Interfaces:**
- Consumes: `FleetManager(site, params, robots, clock)`, `record_to(write)`(첫 입력 전에 불러야 함), `on_robot_state`, `on_order_board`, `tick()`, `assignments_view()`, `orchestrator.order_of`; `test_manager.py`의 `report`, `finish`(import해서 씀). 기록 줄 모양: 입력 `{"t", "in": 함수 이름, "args"}`, 안쪽 명령 `{"t", "inside": 클래스 이름, ...칸}`, 바깥 출력 `{"t", "out": 클래스 이름, ...칸}`
- Produces: Task 4가 모을 시험 이름 · 기록 줄 출력

- [ ] **Step 1: 실패하는 시험 작성** — `test_order_flow.py`

```python
"""주문판에서 Pode 배정까지: 실제 교통 층과 주문 코어를 FleetManager로 묶어 데이터가 오가는지 본다."""
import json

from fleet_core.common.commands import AssignmentView, OrderRecord
from fleet_core.common.params import load_params
from fleet_core.fleet_map import load_fleet_map
from fleet_core.manager import FleetManager
from test_manager import finish, report

PODES = {"r1": "P-1", "r2": "P-3"}


def start(robots=PODES):
    lines = []
    core = FleetManager(load_fleet_map("d2"), load_params(), list(robots), lambda: 0.0)
    core.record_to(lines.append)
    for robot, node in robots.items():
        report(core, robot, node)
    return core, lines


def run_until(core, done, limit=300):
    for _ in range(limit):
        finish(core, tuple(core.traffic.robots), core.tick())
        if done():
            return
    raise AssertionError("no pode was bound")


def bound(core):
    return {r: core.orchestrator.order_of(r) for r in core.traffic.robots
            if core.orchestrator.order_of(r)}


def test_an_order_on_the_board_is_bound_to_a_pode_through_the_traffic_layer():
    core, lines = start()
    core.on_order_board("RUN", [OrderRecord("T-1", {"A": 1})])
    run_until(core, lambda: bound(core))
    (pode, order), = bound(core).items()
    assert order == "T-1"
    rows = [json.loads(x) for x in lines[1:]]
    kinds = [r.get("in") or r.get("inside") or r.get("out") for r in rows]
    board = kinds.index("on_order_board")
    call = kinds.index("DispatchCmd", board)
    answer = kinds.index("DispatchResult", call)
    bound_at = next(i for i, r in enumerate(rows) if i > answer and r.get("out") == "EventOut"
                    and r["code"] == "ORDER_BOUND")
    trip = kinds.index("ReportTripCmd", bound_at)
    assert rows[call]["station_id"] == "WORK" and rows[call]["robot_id"] == ""
    assert rows[answer]["success"] is True and rows[answer]["robot_id"] == pode
    assert rows[bound_at]["detail"] == "T-1" and rows[bound_at]["robot"] == pode
    assert rows[trip]["status"] == "ASSIGNED" and rows[trip]["robot"] == pode
    assert rows[trip]["trip_id"] == "T-1"
    assert AssignmentView(pode, "T-1", "WORK", "FILL") in core.assignments_view()
    for r in (rows[board], rows[call], rows[answer], rows[bound_at], rows[trip]):
        print(json.dumps(r, ensure_ascii=False))


def test_two_orders_are_bound_to_two_podes_in_board_order():
    core, _ = start()
    core.on_order_board("RUN", [OrderRecord("T-1", {"A": 1}), OrderRecord("T-2", {"B": 1})])
    run_until(core, lambda: len(bound(core)) == 2)
    assert sorted(bound(core).values()) == ["T-1", "T-2"]


def test_an_order_over_capacity_is_warned_and_the_other_is_still_bound():
    core, lines = start({"r1": "P-1"})
    core.on_order_board("RUN", [OrderRecord("T-9", {"A": 5}), OrderRecord("T-1", {"A": 1})])
    run_until(core, lambda: bound(core))
    assert bound(core) == {"r1": "T-1"}
    rows = [json.loads(x) for x in lines[1:]]
    assert [r["code"] for r in rows if r.get("out") == "EventOut"
            and r["code"] == "ORDER_OVER_CAPACITY"] == ["ORDER_OVER_CAPACITY"]
```

실행 중 판정: 두 번째 시험은 '첫 Pode가 T-1을 받고 다른 Pode는 작업대 대기열에서 기다림'으로 바꿈 — 작업대 도크가 하나이고 채우기(4장)가 없어 묶인 Pode가 도크를 잡고 있음.

- [ ] **Step 2: 통과 확인** (Task 1 · 2를 끝낸 뒤 쓰는 시험이라 처음부터 통과가 맞음. 실패를 먼저 보려면 Task 1 앞 커밋에서 돌리면 FAIL)

Run: `python3 -m pytest pc/fleet_core/test/test_order_flow.py -q -s`
Expected: 3개 PASS, 첫 시험이 기록 줄 다섯 개(주문판 → 호출 → 도착 답 → ORDER_BOUND → ReportTripCmd)를 출력. 하나라도 FAIL이면 교통 층이 아니라 주문 코어 쪽에서 원인을 찾습니다(교통 층은 고치지 않음 — 고칠 일이 생기면 멈추고 보고).

- [ ] **Step 3: `pseudocode.md` 머리 고치기**

"이 문서는 v2.4의 목표 동작이다. 지금 팀 main의 주문 코어는 뼈대다." 단락을 "주문판 처리 · 작업대 호출 · 배정 응답(2 · 3장)은 구현됨, 4장 채우기부터와 5장 고장 처리 · 강제 출고는 아직 뼈대"로 바꿉니다. 강제 출고 두 줄은 그대로 둡니다.

- [ ] **Step 4: 전체 시험 · 검사**

Run: `python3 -m pytest pc/fleet_core/test -q` → Expected: 1225 + 새 시험 수 passed, 7 xfailed, 실패 0
Run: `python3 -m pytest pc/fleet_manager/test -q` → Expected: 63 passed
Run: `uvx ruff check pc/fleet_core` → Expected: `All checks passed!`

- [ ] **Step 5: 커밋**

```bash
git add pc/fleet_core/test/test_order_flow.py pc/fleet_core/fleet_core/orders/pseudocode.md
git commit -m "test(orders): 주문판에서 Pode 배정까지 코어 통합 시험"
```

### Task 4: 보고서 근거 모으기 · PR

**Files:**
- Modify: `reports/스프린트_3회차/주문_배정_연동_테스트_메모.md` (끝에 "모은 근거" 절)

- [ ] **Step 1: 근거 모으기**

Run: `git log --oneline origin/dev..HEAD`
Expected: Task 1 ~ 3 커밋 3개

Run: `python3 -m pytest pc/fleet_core/test/test_order_flow.py -v -s`
Expected: 3개 PASSED, 첫 시험이 기록 줄 다섯 개(주문판 → 호출 → 도착 답 → ORDER_BOUND → ReportTripCmd)를 출력

Task 3 Step 4의 세 결과 줄(fleet_core · fleet_manager 통과 수, ruff)도 함께 씁니다.

- [ ] **Step 2: 메모에 "모은 근거" 절 더하기**

메모의 시험 항목(T-03 ~ T-09)마다 근거가 된 시험 이름을 적고, 기록 줄 다섯 개와 커밋 hash · 제목, 위 세 결과 줄을 그대로 옮깁니다. 해석이나 보고서 문장은 쓰지 않습니다(형식 논의 뒤 씀).

- [ ] **Step 3: 커밋**

```bash
git add reports/스프린트_3회차/주문_배정_연동_테스트_메모.md docs/superpowers/plans/order-core-dispatch.md
git commit -m "docs: 주문 코어 배정 단계 계획과 연동 테스트 근거"
```

- [ ] **Step 4: 사용자 확인 뒤 푸시 · PR** — 리뷰는 관제 담당(박정욱)에게 요청합니다. squash 병합 뒤 `dev` hash를 메모에 더하는 일은 보고서 논의 때 합니다.
