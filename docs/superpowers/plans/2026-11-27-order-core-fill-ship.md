# 주문 코어 1차 (채우기 · 출고 · 교환 · 보고) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `OrderOrchestrator`의 빈 `step` · `on_arm_report`를 채워, 묶인 주문이 채우기 → 출고 이동 → 교환 → 끝 상태 보고까지 돌고 contract 1.0 팔 보고 5종과 서비스 정지를 처리하게 합니다.

**Architecture:** `pc/fleet_core/fleet_core/orders/orchestrator.py` 한 파일 안에 로봇 기록 자료형(`_RobotRecord`, `_Bound`)을 두고 `pseudocode.md` 3장 1 · 4 ~ 7단계와 설계 4.4 · 4.5 · 4.8을 채웁니다. 바깥 접점(생성자 · 입력 함수 · `take_output` · `order_of` · `robot_view` · `shipped`)은 바꾸지 않고 `service_paused` 속성 하나만 더합니다.

**Tech Stack:** Python 3.12, pytest (ROS 없음)

**Spec:** `docs/superpowers/specs/2026-11-27-order-core-fill-ship-design.md` (함께 읽음: `pc/fleet_core/fleet_core/orders/pseudocode.md`)

## Global Constraints

- 고정 저장소 `/home/ane/dev_ws/roscamp-repo-2`, 브랜치 `feat/TT-27-fleet-dev-merge-2026-11-27`, worktree 없음. 푸시하지 않음.
- 바깥 접점 이름 · 인자를 바꾸지 않음. 더하는 것은 `service_paused`(읽기 속성, bool)뿐.
- 출력은 `common/commands.py`의 자료형만 씀. 새 명령 · 보고 자료형을 만들지 않음.
- 진행 보고 `ReportTripCmd`의 `missing`은 `order.missing`(실제로 못 담은 것)만. 남은 상품을 `missing`으로 싣지 않음.
- 담은 수는 `ArmFillResult.placed`를 씀(`placed_confirmed` 아님).
- 값: `basket_capacity`=4, `dispatch_retry_wait_s`=2.0, `dispatch_retry_limit`=3 (`load_params()`에서 읽음, 숫자를 코드에 박지 않음).
- 작업대 id는 `self._work`(FILL 스테이션), 출고대 id는 SWAP 스테이션에서 같은 방식으로 찾음(시험 `STATIONS`의 `"WORK"` · `"OUTBOUND"`).
- **주석은 구체적으로**(사용자 요청). 분기마다 근거(`pseudocode.md` 단계 번호, 설계 4.x, 팔 관제 합의 번호)를 적고, 일부러 하지 않는 일도 적음. "처리한다"류 주석 금지. 시험 docstring 한 줄에 무엇을 막는 시험인지 씀.
- 문서 · 주석 · 커밋은 한국어, 커밋 끝 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 시험 명령(저장소 루트): `source /opt/ros/jazzy/setup.bash && source install/setup.bash && python3 -m pytest pc/fleet_core/test -q -p no:cacheprovider`

## Review Focus

1. 재시작(`ArmFillPlaced`) 뒤 [복구] 전에 `step`이 남은 상품을 다시 묻지 않음 → 두 번 담지 않음 (Task 4 `test_no_fill_is_asked_while_waiting_for_resume`)
2. 마지막 목표의 진행 보고 뒤 결과가 오면 `placed`를 두 번 더하지 않음(진행은 지금 목표 몫, 결과가 오면 진행을 비움) (Task 2 `test_progress_is_not_counted_twice_when_the_result_comes`)
3. 같은 주문의 `FILLING` 보고는 첫 `ArmFillCmd`에서 한 번만, 재요청(`ArmDeclined` 뒤 · 실패 재시도 뒤)에서 다시 내지 않음 (Task 2 `test_filling_is_reported_once_per_order`)
4. 끝 상태 보고 뒤 같은 id가 주문판에 남아 있어도 다시 배정하지 않음 (Task 3 `test_a_shipped_order_left_on_the_board_is_not_bound_again`)
5. `held` 로봇이 작업대 호출 성공으로 다시 오더라도 묶거나 놓아 주지 않음 (Task 1 `test_a_held_robot_is_neither_bound_nor_released`)

---

### Task 1: 로봇 기록과 묶기 · 놓아 주기 · 주문판 정리

**Files:**
- Modify: `pc/fleet_core/fleet_core/orders/orchestrator.py` (`__init__`, `on_order_board`, `on_dispatch_result`, `order_of`, `robot_view`, `_waiting_orders`)
- Test: `pc/fleet_core/test/test_orders_fill_ship.py` (새 파일, `test_orders_dispatch.py`의 `make()` · `STATIONS` 방식)

**Interfaces:**
- Produces:
  - `@dataclass class _Bound: order_id: str; items: dict[str, int]; placed: dict[str, int]; missing: dict[str, int]; progress: dict[str, int]; reported_filling: bool`
  - `@dataclass class _RobotRecord: stage: str = ""; basket: str = EMPTY; order: _Bound | None = None; last_order_id: str = ""; outbound_goal: int | None = None; dispatch_failures: int = 0; dispatch_retry_at: float | None = None; dispatch_reason: str = ""; asked: str = ""; bound: int = 0; held: str = ""`
  - `self._robots: dict[str, _RobotRecord]`, `self._final: set[str]`, `self._holds: dict[str, tuple[str, str]]`(주문 id → (로봇, 사유)), `self._outbound: str`(SWAP 스테이션 id)
  - `asked` 값 상수: `ASK_FILL="fill"`, `ASK_SWAP="swap"`, `ASK_PAUSED="paused"`, `ASK_RESUME="resume_wait"`
  - `_bind(robot, order: OrderRecord) -> None`, `_can_bind(rec) -> bool`
- `self._bound`는 지우고 `_robots[...].order`로 옮김. `order_of` · `robot_view`의 반환 모양은 그대로(`placed`는 이제 `order.placed` 사본).

- [ ] **Step 1: 실패하는 시험**
  - `test_a_free_robot_reaching_work_is_bound_and_reported_assigned`: 묶은 뒤 `robot_view("r1") == {"stage": "FILLING", "basket": "EMPTY", "order": "T-1", "placed": {}}`, 출력에 `EventOut("INFO","ORDER_BOUND","T-1","r1")`, `ReportTripCmd("T-1","r1","ASSIGNED",{},{})`
  - `test_a_robot_reaching_work_with_nothing_waiting_is_released`: 주문판 비운 뒤 호출 결과 성공 → `ReleaseCmd("r1")` 포함
  - `test_a_held_robot_is_neither_bound_nor_released`: `_robots["r1"].held = "ARM_FAULT"`로 둔 뒤 성공 결과 → `ReleaseCmd` 없음, 묶이지 않음
  - `test_final_and_held_ids_not_on_the_new_board_are_forgotten`: `_final={"T-1"}`, `_holds={"T-2": ("r2","ROBOT_FAULT")}`, 주문판 `[T2]` → `_final == set()`, `_holds`에 `T-2` 남음
  - `test_final_or_held_orders_are_not_waiting`: `_final`에 `T-1` → 주문판 `[T1]`에 WORK 호출을 열지 않음

- [ ] **Step 2: 실패 확인** — `python3 -m pytest pc/fleet_core/test/test_orders_fill_ship.py -q` → 새 시험 FAIL
- [ ] **Step 3: 구현** — 자격 = 아는 로봇 · `order is None` · `held == ""` · `stage == ""` · `basket == EMPTY`(설계 4.3). `held` 로봇의 주문이 주문판에서 빠지면 `order`만 비움(설계 4.6).
- [ ] **Step 4: 통과 확인** — 시험 명령 전체 → PASS (`test_orders_dispatch.py` 포함)
- [ ] **Step 5: 커밋** — `feat(orders): 로봇 기록 · 묶을 자격 · 묶을 주문이 없으면 놓아 줌 · 주문판 정리 (설계 4.1 · 4.3 · 4.6)`

---

### Task 2: 채우기 (`step` FILLING, 채우기 결과 · 진행)

**Files:**
- Modify: `orchestrator.py` (`step`, `on_arm_report`의 `ArmDeclined` · `ArmFillResult` · `ArmFillProgress`)
- Test: `test_orders_fill_ship.py`

**Interfaces:**
- Consumes: Task 1의 `_RobotRecord` · `_Bound` · `_bind`
- Produces: `_remaining(b: _Bound) -> dict[str, int]`(items − placed − missing, 0 이하 뺌), `_finish_fill(robot) -> None`(마무리), `_report_trip(robot, status, reason="") -> None`(`ReportTripCmd(order_id, robot, status, dict(placed+progress), dict(missing), reason)`), `_start_outbound(robot) -> None`(Task 3이 몸통을 채움, 여기서는 `DispatchCmd(새 id, OUTBOUND, robot)` + `outbound_goal` 설정까지)

- [ ] **Step 1: 실패하는 시험** (묶은 상태에서 시작하는 도움 함수 `bound(o, order=T1)`)
  - `test_step_asks_for_the_remaining_items_and_reports_filling`: `step("r1")` → `[ReportTripCmd("T-1","r1","FILLING",{},{}), ArmFillCmd("r1","WORK","T-1",{"A":1})]`, `asked == "fill"`
  - `test_step_waits_while_a_fill_is_asked`: 두 번째 `step` → 출력 없음
  - `test_filling_is_reported_once_per_order`: `ArmDeclined("r1")` 뒤 `step` → `ArmFillCmd`만, `FILLING` 보고 없음
  - `test_progress_reports_placed_so_far_and_only_real_missing`: 주문 `{"A":2,"B":1}`, `ArmFillProgress("r1",{"A":1})` → `ReportTripCmd(..., "FILLING", {"A":1}, {})`
  - `test_progress_is_not_counted_twice_when_the_result_comes`: 진행 `{"A":1}` 뒤 `ArmFillResult("r1", True, {"A":2,"B":1})` → `order.placed == {"A":2,"B":1}`, `progress == {}`
  - `test_a_failed_fill_adds_what_was_placed_and_reports_the_failure`: `ArmFillResult("r1", False, {"A":1}, "NOT_READY")` → `placed == {"A":1}`, 출력에 `ArmFailedCmd("r1")`, `asked == ""`
  - `test_a_full_fill_goes_to_outbound_and_reports_transporting`: 성공 결과 뒤 `step` → `ArmResetCmd("r1")`, `DispatchCmd(n, "OUTBOUND", "r1")`, `ReportTripCmd(...,"TRANSPORTING",{"A":1},{})` 차례, `basket == "FULL"`
  - `test_a_partial_fill_still_ships`: `{"A":2}` 중 `{"A":1}`만 성공 → `missing == {"A":1}`, OUTBOUND로 감
  - `test_nothing_placed_is_canceled_and_released`: 성공 결과 `placed={}` → `ReportOrderCmd("T-1","r1","CANCELED",{}, {"A":1}, <"nothing could be placed, missing {'A': 1}">)`, `ReleaseCmd("r1")`, `_final`에 `T-1`, `stage == ""`
- [ ] **Step 2: 실패 확인** — 위 명령 → FAIL
- [ ] **Step 3: 구현** — 순서: 마무리는 `ArmResetCmd` 뒤 보고(설계 7절). CANCELED `detail` 글은 `f"nothing could be placed, missing {missing}"`.
- [ ] **Step 4: 통과 확인** — 시험 명령 전체 → PASS
- [ ] **Step 5: 커밋** — `feat(orders): 채우기 step · 채우기 결과 · 진행 보고 (pseudocode 4단계, 설계 4.2 · 4.4)`

---

### Task 3: 출고 이동 · 교환 · 끝 상태 보고 · 빈 바구니

**Files:**
- Modify: `orchestrator.py` (`on_dispatch_result`의 OUTBOUND, `step`의 재시도 · SWAPPING · 쉼, `on_arm_report`의 `ArmSwapResult`)
- Test: `test_orders_fill_ship.py`

**Interfaces:**
- Consumes: Task 2의 `_start_outbound`
- Produces: `_report_final(robot, status, detail="") -> None`(`ReportOrderCmd` + `_final` · `_holds` · `order` · `held` 정리, `shipped += 1`은 SHIPPED · PARTIAL 때만)

- [ ] **Step 1: 실패하는 시험**
  - `test_reaching_outbound_starts_the_swap`: OUTBOUND 결과 성공 → `step` → `ArmSwapCmd("r1","OUTBOUND","T-1",True,True)`, `asked == "swap"`
  - `test_taking_the_full_basket_reports_shipped`: `ArmSwapResult("r1", True, False)` → `ReportOrderCmd("T-1","r1","SHIPPED",{"A":1},{},"")`, `shipped == 1`, `basket == "NONE"`, 그다음 `ArmFailedCmd`(빈 바구니를 아직 못 놓음)
  - `test_putting_the_empty_basket_releases_the_robot`: `ArmSwapResult("r1", True, True)` → SHIPPED 뒤 `ArmResetCmd("r1")`, `ReleaseCmd("r1")`, `basket == "EMPTY"`, `stage == ""`
  - `test_missing_items_report_partial`
  - `test_a_failed_outbound_call_is_retried_after_the_wait`: 실패 결과 → 시각 1.9에 `step` 출력 없음, 2.0에 `EventOut(WARN, DISPATCH_RETRY, …)` + 새 `DispatchCmd`
  - `test_too_many_outbound_failures_fault_the_robot`: 실패 4번 → `RobotFaultCmd("r1", <이유>)` + `EventOut("ERROR","DISPATCH_FAILED", …)`, 그 뒤 재시도 없음
  - `test_a_robot_without_a_basket_fetches_an_empty_one`: `basket = "NONE"`, 주문 없음 → `step` → `DispatchCmd(n,"OUTBOUND","r1")`
  - `test_a_shipped_order_left_on_the_board_is_not_bound_again`: 출고 뒤 같은 주문판 → WORK 호출을 열지 않음
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 시계는 생성자의 `clock`. `make()`는 시각을 바꿀 수 있는 시계를 넘기도록 이 파일 안에서 따로 만듦. `RobotFaultCmd` 이유는 `dispatch_reason`.
- [ ] **Step 4: 통과 확인** — 시험 명령 전체 → PASS
- [ ] **Step 5: 커밋** — `feat(orders): 출고 이동 · 재시도 · 교환 · 끝 상태 보고 · 빈 바구니 받기 (pseudocode 1 · 5 · 6 · 7단계)`

---

### Task 4: 팔 관제 합의 보고 5종과 팔 고장

**Files:**
- Modify: `orchestrator.py` (`on_arm_report`의 `ArmFillPaused` · `ArmFillPlaced` · `ArmFillResumed` · `ArmStationBlocked` · `ArmFault` · `ArmLost` · `ArmLate`, `_match_calls`)
- Test: `test_orders_fill_ship.py`, 통합은 `pc/fleet_core/test/orders_and_arm.py`의 `OrdersAndArm`

**Interfaces:**
- Consumes: Task 1 ~ 3
- Produces: `self._blocked: set[str]`(막힌 스테이션 id)

- [ ] **Step 1: 실패하는 시험**
  - `test_a_paused_fill_reports_filling_pause_and_is_not_asked_again`: `ArmFillPaused("r1","workcell","RECOGNITION_ERROR","…")` → `ReportTripCmd(…,"FILLING_PAUSE",placed,missing,"RECOGNITION_ERROR")`, `asked == "paused"`, 그 뒤 `step` 출력 없음
  - `test_no_fill_is_asked_while_waiting_for_resume`: `ArmFillPlaced("r1",{"A":1})` → `placed += {"A":1}`, 출력에 운반 건 보고 없음, `step` 출력 없음
  - `test_resume_asks_only_for_the_rest`: 주문 `{"A":2}`, Placed `{"A":1}` 뒤 `ArmFillResumed("r1")` → `step` → `ArmFillCmd("r1","WORK","T-1",{"A":1})`
  - `test_an_arm_fault_holds_the_order_on_the_robot`: `ArmFault("r1","fill","workcell","boom")` → `ReportOrderCmd("T-1","r1","HELD",placed,items-placed,"boom","ARM_FAULT")`, `_holds["T-1"] == ("r1","ARM_FAULT")`, `held == "ARM_FAULT"`, `order_of("r1") == "T-1"`, `step` 출력 없음
  - `test_a_blocked_workcell_cancels_and_reopens_its_calls`: 주문판 `[T1]`(호출 1 열림) → `ArmStationBlocked("WORK","arm_left ERROR")` → `CancelDispatchCmd(1)` → `ArmStationBlocked("WORK","")` → `DispatchCmd(2,"WORK")`
  - `test_a_late_arm_result_only_warns`: `ArmLate(9,"fill",{"A":1})` → `EventOut("WARN","ARM_LATE_RESULT", …)`, 수량 그대로
  - 통합 `test_orders_and_arm_run_one_order_to_shipped`: `OrdersAndArm`으로 묶기 → `step` → `on_fill_result(goal, True, {"A":1}, {})` → `step` → OUTBOUND 결과 → `step` → `on_swap_result(goal, True, True, True)` → 출력에 `ReportOrderCmd(…"SHIPPED"…)`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — `ArmFillPlaced.held`면 수량만 더하고 기다림(이어서 `ArmFault`). `ArmLost`는 `asked`만 비움.
- [ ] **Step 4: 통과 확인** — 시험 명령 전체 → PASS (`test_arm_rules.py` 포함)
- [ ] **Step 5: 커밋** — `feat(orders): 담기 일시 중지 · 재시작 · [복구] · 작업대 막힘 · 팔 고장 보류 (팔 관제 합의, 설계 4.4 · 4.5)`

---

### Task 5: 서비스 정지와 의사코드 갱신

**Files:**
- Modify: `orchestrator.py` (`service_paused`, `_match_calls`, `on_dispatch_result`, `step`의 쉼), `pseudocode.md`(1장 표에 `service_paused`, 4장 정지 규칙, 머리의 "아직 뼈대" 문장, 3장 4단계 진행 보고 · 1.0 보고), `orchestrator.py` 파일 머리 주석
- Test: `test_orders_fill_ship.py`, `pc/fleet_core/test/test_orders_dispatch.py`

**Interfaces:**
- Produces: `@property service_paused -> bool` (= 마지막 `run_mode == "PAUSE"`)

- [ ] **Step 1: 실패하는 시험 · 바뀌는 시험**
  - `test_orders_dispatch.py::test_the_run_mode_does_not_hold_calls_back`를 `test_a_service_pause_opens_no_work_call`로 바꿈: `on_order_board("PAUSE",[T1])` → 출력 `[]`. (설계 4.8이 이 시험의 옛 기대를 바꿈)
  - `test_service_paused_follows_the_run_mode`: PAUSE → True, RUN → False, STOP → False
  - `test_a_pause_cancels_open_work_calls_and_run_reopens_them`
  - `test_a_robot_reaching_work_during_a_pause_is_released_unbound`
  - `test_a_bound_order_finishes_during_a_pause`: 묶은 뒤 PAUSE → `step`이 `ArmFillCmd`를 냄
  - `test_no_empty_basket_is_fetched_during_a_pause`
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현 · 문서** — `pseudocode.md` 4장 첫 줄 "주문 코어는 run_mode를 저장만 하고 …"를 설계 4.8 규칙으로 바꿈. `manager.py`는 고치지 않음.
- [ ] **Step 4: 통과 확인** — `python3 -m pytest pc/fleet_core/test pc/fleet_manager/test -q -p no:cacheprovider` (fleet_manager 시험은 `ROS_DOMAIN_ID=87 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`) → PASS
- [ ] **Step 5: 커밋** — `feat(orders): 서비스 정지 — 묶인 운반 건은 끝까지, 새 일은 막음 (설계 4.8, 팔 관제 합의 ⑦), 의사코드 갱신`

---

### Task 6: 전체 시험과 통합 확인

- [ ] **Step 1:** `colcon build --symlink-install --packages-select fleet_core fleet_manager` 뒤 fleet_core · fleet_manager · central · fake_robots 시험 전체(도메인 87, LOCALHOST). Expected: 지금 알려진 실패 1건(`test_ros_workcell.py::test_an_arm_error_shows_only_after_the_result_went_out`) 밖에는 PASS.
- [ ] **Step 2 (사람 확인, 설계 6절):** 시험 DB(`setup_db`) + `python -m service_bridge.main --ros-args -p mode:=NORMAL` + `ros2 run central central` + `ros2 launch iponic_bringup pc.launch.py use_fake:=true fake_central:=false scenario:=real_two_orders map_file:=v7 robots:="pode1 pode2" fake_robots:="pode1 pode2"`(모두 같은 개인 도메인, `FLEET_LOCAL=1`).
  - `fake_central:=false`면 시나리오의 주문을 가짜 central이 넣지 않으므로, 주문은 `python -m service_bridge.tools.order --account <id> --items <product_id>:1`로 두 건 넣음(시나리오가 주문 없이도 뜨는지 먼저 확인하고, 안 되면 주문 없는 시나리오를 고름).
  - Expected: 두 운반 건이 DB에서 `ASSIGNED` → `FILLING` → `TRANSPORTING` → `SHIPPED`로 바뀜(`ws_watch --admin`). 실행기는 결과를 보고하고, 자동으로 못 하면 그 사실과 까닭을 적음.
