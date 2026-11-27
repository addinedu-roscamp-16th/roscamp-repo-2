# ---- OrderOrchestrator: 주문판을 받아 로봇에 주문을 묶고 진행하는 자리 ----
"""주문 처리. 주문판 처리(대기 주문, 용량 검사, 작업대 호출), 배정 응답(`on_dispatch_result` ·
`on_dispatch_feedback`), 조회(`order_of` · `robot_view`)만 채워져 있고
나머지(채우기 이후, 고장 처리, 강제 출고) 몸통은 비어 있다.
흐름은 같은 폴더의 pseudocode.md에 있다.

FleetManager가 이 클래스를 하나 만들어 아래 입력 함수를 부르고, `take_output()`으로 명령을
가져간다. 이름과 시그니처는 FleetManager와 맞물려 있으니 바꾸지 않는다.
낼 수 있는 명령은 모두 fleet_core.common.commands에 있다.
"""
from __future__ import annotations

from dataclasses import dataclass

from fleet_core.common.commands import (
    FILL,
    FORCE_NOT_IMPLEMENTED,
    CANCELED,
    NO_ROBOT,
    SWAP,
    TRIP_ASSIGNED,
    TRIP_FILLING,
    TRIP_TRANSPORTING,
    ArmDeclined,
    ArmFailedCmd,
    ArmFillCmd,
    ArmFillProgress,
    ArmFillResult,
    ArmResetCmd,
    ArmSwapCmd,
    ArmSwapResult,
    CancelDispatchCmd,
    DispatchCmd,
    EventOut,
    ReleaseCmd,
    ReportOrderCmd,
    ReportTripCmd,
    RobotFaultCmd,
    SHIPPED,
    PARTIAL,
)

# ---- 다른 모듈이 가져다 쓰는 상태 이름 ----
FILLING = "FILLING"
SWAPPING = "SWAPPING"
EMPTY = "EMPTY"
FULL = "FULL"
NONE = "NONE"

# ---- 로봇이 답을 기다리는 팔 작업 종류(`_RobotRecord.asked`, 설계 4.1) ----
ASK_FILL = "fill"
ASK_SWAP = "swap"
ASK_PAUSED = "paused"  # 서비스 정지로 보류한 작업(설계 4.4)
ASK_RESUME = "resume_wait"  # 재개 뒤 다시 보내기를 기다리는 중(설계 4.4)


@dataclass
class _Bound:
    """로봇에 묶인 주문 한 건의 진행(설계 4.1). 채우기 이후 단계가 placed 등을 채운다."""
    order_id: str
    items: dict[str, int]
    placed: dict[str, int]  # 품목별로 담긴 수
    missing: dict[str, int]  # 품목별로 못 담은 수
    progress: dict[str, int]  # 품목별로 이미 요청한 수
    reported_filling: bool  # FILLING 보고를 이미 냈는지


@dataclass
class _RobotRecord:
    """로봇 한 대의 주문 쪽 기록(설계 4.1, pseudocode.md 2장)."""
    stage: str = ""
    basket: str = EMPTY
    order: _Bound | None = None
    last_order_id: str = ""
    outbound_goal: int | None = None
    dispatch_failures: int = 0
    dispatch_retry_at: float | None = None
    dispatch_reason: str = ""
    asked: str = ""
    bound: int = 0  # 묶을 때마다 +1. 늦은 팔 결과가 같은 묶음인지 가리는 번호
    held: str = ""  # 팔 고장으로 붙잡혔으면 "ARM_FAULT"


class OrderOrchestrator:
    def __init__(self, stations, params, robots, clock):
        """FleetManager가 시작할 때 한 번 만든다.

        stations: 스테이션 id -> 스테이션. action이 FILL인 곳이 WORK, SWAP인 곳이 OUTBOUND다.
        params: FleetParams. 주문 처리는 basket_capacity, ship_partial, dispatch_retry_wait_s,
            dispatch_retry_limit을 쓴다.
        robots: 로봇 이름들.
        clock: 지금 시각(초)을 주는 함수.
        """
        self.params = params
        self.shipped = 0
        self._stations = stations
        self._clock = clock
        self._names = tuple(robots)
        # ---- 주문판과 작업대 호출 ----
        self._work = next(sid for sid, st in stations.items() if st.action == FILL)
        self._run_mode = "RUN"
        self._board = []
        self._over_capacity = set()  # 용량 초과를 이미 알린 주문 id
        self._open_calls = []  # 열어 둔 WORK 호출 id, 연 순서
        self._next_call = 1
        # 설계 4.1: 로봇별 기록. 예전 `_bound`(로봇 -> OrderRecord)는 `_robots[...].order`로 옮겼다.
        self._robots = {name: _RobotRecord() for name in robots}
        self._final = set()  # 최종 보고(SHIPPED/PARTIAL/CANCELED)를 낸 주문 id
        self._holds = {}  # 보류 주문 id -> (로봇, 사유)
        # 교환(SWAP) 스테이션 = OUTBOUND. _start_outbound의 DispatchCmd 목적지이자 ArmSwapCmd 스테이션.
        self._outbound = next((sid for sid, st in stations.items() if st.action == SWAP), "")
        self._out = []

    # ---- 주문판, 배차, 팔 보고 입력 ----
    def on_order_board(self, run_mode, orders) -> None:
        """central의 주문판이 올 때마다 불린다. 검사를 통과한 주문판만 온다.

        run_mode: "RUN", "PAUSE", "STOP". orders: OrderRecord 목록이고 목록 순서가 우선순위다.
        낼 수 있는 출력: DispatchCmd(로봇 이름 없이 WORK를 부르는 열린 호출), CancelDispatchCmd,
        EventOut(WARN, ORDER_OVER_CAPACITY).
        """
        self._run_mode = run_mode
        self._board = list(orders)
        # ---- 설계 4.6 / pseudocode 2단계: 새 주문판에 없는 id는 최종 보고 집합 · 보류 목록에서 지움 ----
        # (안 지우면 같은 id가 다시 올 때 이미 끝난 주문으로 오해하고, 집합도 끝없이 커짐)
        on_board = {o.order_id for o in self._board}
        self._final &= on_board
        self._holds = {k: v for k, v in self._holds.items() if k in on_board}
        # ---- 설계 4.6: held 로봇의 주문이 빠지면 로봇의 주문만 비움 ----
        # 로봇은 붙잡은 채 둔다. 풀기는 2차의 치움 몫이라 여기서 하지 않는다.
        for rec in self._robots.values():
            if rec.held and rec.order is not None and rec.order.order_id not in on_board:
                rec.order = None
        # ---- 용량 초과 기록: 새 주문판에 남아 있고 여전히 넘는 id만 ----
        capacity = self.params.basket_capacity
        over = {o.order_id for o in self._board if sum(o.items.values()) > capacity}
        for order in self._board:
            if order.order_id in over and order.order_id not in self._over_capacity:
                n = sum(order.items.values())
                self._out.append(EventOut(
                    "WARN", "ORDER_OVER_CAPACITY",
                    f"{order.order_id}: {n} items exceed the basket capacity of {capacity}"))
        self._over_capacity = over
        self._match_calls()

    def on_dispatch_feedback(self, goal_id, robot_id, phase) -> None:
        """교통 층이 호출의 진행을 알릴 때 불린다.

        goal_id: DispatchCmd에 넣은 번호. robot_id: 호출을 맡은 로봇(없으면 ""). phase: 진행 단계.
        phase가 NO_ROBOT이면 맡을 로봇이 없다는 뜻이다.
        낼 수 있는 출력: EventOut(WARN, NO_ROBOT_AVAILABLE).
        """
        if phase != NO_ROBOT:
            return
        ids = [o.order_id for o in self._waiting_orders()]
        self._out.append(EventOut("WARN", "NO_ROBOT_AVAILABLE",
                                  f"{ids} wait and no robot can take them"))

    def on_dispatch_result(self, goal_id, success, robot_id, reason="") -> None:
        """로봇이 스테이션에 닿았거나 호출이 실패했을 때 불린다.

        goal_id: DispatchCmd에 넣은 번호. success: 스테이션에 닿았는지. robot_id: 닿은 로봇.
        reason: 실패 이유.
        낼 수 있는 출력: EventOut(ORDER_BOUND), ReportTripCmd(ASSIGNED), DispatchCmd,
        CancelDispatchCmd.
        """
        # ---- 설계 4.3 / pseudocode 5단계: 어느 로봇의 outbound_goal이면 그 로봇의 OUTBOUND 결과 ----
        # outbound_goal은 로봇마다 새 id라 겹치지 않고, WORK 열린 호출 id와도 같은 번호표에서 나와 겹치지 않는다.
        for name, orec in self._robots.items():
            if orec.outbound_goal == goal_id:
                orec.outbound_goal = None
                if success:
                    orec.stage = SWAPPING
                    orec.dispatch_failures = 0
                    orec.dispatch_retry_at = None
                else:
                    # 바로 다시 부르지 않고 dispatch_retry_wait_s(2.0 s) 뒤 step이 다시 부른다(고장 폭주 방지).
                    orec.dispatch_failures += 1
                    orec.dispatch_retry_at = self._clock() + self.params.dispatch_retry_wait_s
                    orec.dispatch_reason = reason
                return
        # ---- 열린 호출이 아닌 답은 무시 ----
        if goal_id not in self._open_calls:
            return
        self._open_calls.remove(goal_id)
        # ---- 설계 4.3: 묶을 자격이 있는 로봇이 닿았을 때만 묶거나 놓아 줌 ----
        # 모르는 로봇의 답은 자격이 없어 아래를 건너뛰고, 호출만 아래 _match_calls에서 다시 열린다.
        rec = self._robots.get(robot_id)
        if success and rec is not None and self._can_bind(rec):
            waiting = self._waiting_orders()
            if waiting:
                self._bind(robot_id, waiting[0])
            else:
                # pseudocode 2단계: 묶을 주문이 그새 사라졌으면 놓아 준다. 안 놓으면 작업대 구역을 잡은 채 남음.
                self._out.append(ReleaseCmd(robot_id))
        # 자격 없음(예: held 로봇)이면 묶지도 놓지도 않는다. 놓으면 붙잡아 둔 뜻이 사라진다(설계 4.3).
        # ---- 실패한 호출은 새 id로 다시 열림 ----
        self._match_calls()

    def on_arm_report(self, report) -> None:
        """팔 작업 층(ArmJobs)의 보고가 올 때 불린다.

        report: ArmFillResult, ArmSwapResult, ArmDeclined, ArmLost, ArmLate, ArmFault 가운데 하나.
        낼 수 있는 출력: ArmFailedCmd, ArmResetCmd, ReportOrderCmd, ReleaseCmd, EventOut.
        """
        # 설계 4.4: 모르는 로봇 이름은 무시. ArmDeclined · ArmFillResult · ArmFillProgress · ArmSwapResult만
        # 받고, 나머지 보고(고장, 재개 등)는 Task 4 몫이라 일부러 아무것도 하지 않는다.
        rec = self._robots.get(getattr(report, "robot", None))
        if rec is None:
            return
        if isinstance(report, ArmDeclined):
            # 설계 4.4: 팔이 거절하면 asked만 비워 다음 step이 다시 묻게 한다. 보고는 내지 않는다.
            if rec.asked in (ASK_FILL, ASK_SWAP):
                rec.asked = ""
        elif isinstance(report, ArmFillResult):
            b = rec.order
            if b is None:
                return  # 묶인 주문이 없으면(예: 치운 뒤 늦게 온 결과) 더할 곳이 없다
            rec.asked = ""
            # 결과의 placed는 이번 목표 몫 전체라 진행 값을 비워야 같은 상품이 두 번 더해지지 않는다(전역 제약 2).
            # placed_confirmed는 쓰지 않는다(설계 4.4: Phody는 둘을 같게 보냄).
            for item, n in report.placed.items():
                b.placed[item] = b.placed.get(item, 0) + n
            b.progress = {}
            if report.success:
                # pseudocode 4단계: 성공이면 못 담은 것을 확정한다(없으면 빈 dict라 전량 출고).
                b.missing = self._remaining_of(b.items, b.placed)
            else:
                # 재시도 여부는 ArmJobs가 정한다(NOT_READY만). 여기서는 알리기만 하고 missing은 건드리지 않는다.
                self._out.append(ArmFailedCmd(report.robot))
        elif isinstance(report, ArmFillProgress):
            b = rec.order
            if b is None:
                return
            # 설계 4.4: 진행은 지금 목표의 것이라 덮어쓴다. 보고의 missing은 실제로 못 담은 것(order.missing)만.
            b.progress = dict(report.placed)
            self._report_trip(report.robot, TRIP_FILLING)
        elif isinstance(report, ArmSwapResult):
            # pseudocode 6단계 / 설계 4.4: asked를 비운 뒤 차례로 판단한다.
            rec.asked = ""
            if report.took_full and rec.basket == FULL:
                rec.basket = NONE
                # pseudocode 7단계: 보고는 찬 바구니를 뺀 순간에 나가고 빈 바구니 놓기는 그 뒤다.
                # 주문이 이미 없으면(held 주문이 주문판에서 빠진 경우 등) 보고할 곳이 없어 건너뛴다.
                if rec.order is not None:
                    b = rec.order
                    self._report_final(report.robot, PARTIAL if b.missing else SHIPPED)
            # 격리 로봇의 되돌리기(pseudocode 6단계)는 격리 처리라 2차 범위(이번 계획 밖, 설계 2절 "범위 밖")이므로 여기서 하지 않는다.
            if report.put_empty_done and rec.basket == NONE:
                rec.basket = EMPTY
                rec.stage = ""
                self._out.append(ArmResetCmd(report.robot))
                self._out.append(ReleaseCmd(report.robot))
            else:
                # 빈 바구니를 못 놓았거나 찬 바구니를 못 뺐다. 다음 step이 남은 일만 다시 묻는다
                # (이미 뺐으면 basket NONE이라 take_full=False). 재시도 한도는 ArmJobs가 ArmFault로 끝낸다.
                self._out.append(ArmFailedCmd(report.robot))

    # ---- 운영자 조작 ----
    def on_force_ship(self, order_id) -> tuple[bool, str]:
        """운영자가 /fleet/force_ship으로 보류된 주문의 강제 출고를 부를 때 불린다.

        order_id: 주문 id(문자열).
        돌려주는 값: (받아들였는지, 거절 사유). 거절 사유는 commands의 FORCE_* 가운데 하나.
        낼 수 있는 출력: ReportOrderCmd, DispatchCmd, ArmResetCmd, ReleaseCmd, EventOut.
        """
        return False, FORCE_NOT_IMPLEMENTED

    # ---- 고장 입력 ----
    def on_robot_removed(self, robot) -> None:
        """운영자가 로봇을 치웠다고 확인했을 때 불린다. 묶인 주문을 대기로 돌려야 한다.

        낼 수 있는 출력: ArmDropCmd, EventOut(ORDER_REQUEUED), DispatchCmd, CancelDispatchCmd.
        """

    def on_robot_reinstated(self, robot, basket) -> None:
        """치웠던 로봇을 다시 넣을 때 불린다. basket은 "EMPTY" 또는 "NONE"이다.

        낼 수 있는 출력: ArmResetCmd(forget=True).
        """

    def on_robot_isolated(self, robot, reason) -> None:
        """교통 층이 로봇을 격리했을 때 불린다. reason은 격리 이유다.

        낼 수 있는 출력: ArmStopCmd, ArmDropCmd, EventOut(ORDER_REQUEUED), DispatchCmd,
        CancelDispatchCmd.
        """

    # ---- tick마다 로봇별 진행 ----
    def step(self, robot) -> None:
        """운행 중(RUN)이고 모든 로봇이 위치를 보고했을 때 tick마다 로봇별로 두 번 불린다.

        채우기, 출고 이동, 교환을 한 걸음씩 진행한다.
        낼 수 있는 출력: ArmFillCmd, ArmSwapCmd, ArmResetCmd, DispatchCmd, ReleaseCmd,
        ReportOrderCmd, RobotFaultCmd, EventOut.
        """
        rec = self._robots.get(robot)
        # 설계 4.2: held면 멈춤, asked가 있으면 팔 답을 기다림. 모르는 로봇은 무시.
        if rec is None or rec.held or rec.asked:
            return
        b = rec.order
        if rec.stage == "":
            self._step_idle(robot, rec)
            return
        if rec.stage == SWAPPING:
            # pseudocode 6단계: 바구니가 이미 비었으면 교환할 것이 없다. 놓아 주고 쉼으로.
            if rec.basket == EMPTY:
                rec.stage = ""
                self._out.append(ReleaseCmd(robot))
                return
            # 찬 바구니를 아직 못 뺐으면 take_full=True, 이미 뺐고 빈 바구니만 남았으면 False.
            rec.asked = ASK_SWAP
            self._out.append(ArmSwapCmd(robot, self._outbound, rec.last_order_id,
                                        rec.basket == FULL, True))
            return
        if rec.stage != FILLING or b is None:
            return
        remaining = self._remaining(b)
        if remaining:
            # pseudocode 4단계: 남은 것을 팔에 요청. FILLING 보고는 주문마다 첫 요청에서 한 번만
            # (재요청은 ArmDeclined · 실패 재시도 뒤에도 같은 값이라 다시 내지 않음, 전역 제약 3).
            rec.asked = ASK_FILL
            if not b.reported_filling:
                b.reported_filling = True
                self._report_trip(robot, TRIP_FILLING)
            self._out.append(ArmFillCmd(robot, self._work, b.order_id, remaining))
            return
        self._finish_fill(robot)

    def _step_idle(self, robot, rec) -> None:
        """stage ""일 때의 step(설계 4.2 stage "" · OUTBOUND 재시도, pseudocode 1 · 5단계)."""
        if rec.outbound_goal is not None:
            return  # OUTBOUND 호출의 답을 기다리는 중
        if rec.dispatch_retry_at is not None:
            if self._clock() < rec.dispatch_retry_at:
                return
            rec.dispatch_retry_at = None
            if rec.dispatch_failures > self.params.dispatch_retry_limit:
                # 실패가 한도(3)를 넘는 4번째: 이 로봇은 코어가 더 못 한다. 고장으로 넘기고 재시도 기록만 지운다.
                # dispatch_failures는 일부러 남겨 둔다. 지우면 아래 빈 바구니 받기가 바로 다시 호출을 낸다.
                self._out.append(RobotFaultCmd(robot, rec.dispatch_reason))
                self._out.append(EventOut(
                    "ERROR", "DISPATCH_FAILED",
                    f"{robot}: outbound call failed {rec.dispatch_failures} times ({rec.dispatch_reason})",
                    robot))
                return
            self._out.append(EventOut(
                "WARN", "DISPATCH_RETRY",
                f"{robot}: outbound call failed ({rec.dispatch_reason}), retry {rec.dispatch_failures}",
                robot))
            self._start_outbound(robot)
            return
        # pseudocode 1단계: 주문도 호출도 없이 바구니만 없으면 빈 바구니를 받으러 간다(5단계와 같은 길).
        # 다른 쉬는 로봇은 아무것도 하지 않는다(다음 일은 열린 호출에 교통 층이 고를 때 온다).
        # 격리 로봇 제외(pseudocode 1단계)는 격리 처리라 2차 범위(이번 계획 밖, 설계 2절 "범위 밖")이므로 검사하지 않는다.
        if rec.order is None and rec.basket == NONE and rec.dispatch_failures == 0:
            self._start_outbound(robot)

    # ---- 출력과 조회 ----
    def take_output(self) -> list:
        """쌓아 둔 명령 목록을 주고 비운다. FleetManager가 입력을 넣은 뒤마다 부른다."""
        out, self._out = self._out, []
        return out

    # ---- 채우기 도움 함수 ----
    @staticmethod
    def _remaining_of(items, done) -> dict:
        """items에서 done을 뺀 것. 0 이하 품목은 뺀다."""
        return {k: n - done.get(k, 0) for k, n in items.items() if n - done.get(k, 0) > 0}

    def _remaining(self, b) -> dict:
        """아직 팔에 요청할 것 = items - placed - missing(설계 4.2). 0 이하 품목은 뺀다."""
        done = dict(b.placed)
        for k, n in b.missing.items():
            done[k] = done.get(k, 0) + n
        return self._remaining_of(b.items, done)

    def _report_trip(self, robot, status, reason="") -> None:
        """ReportTripCmd를 낸다. placed는 확정된 담은 수에 진행 중인 수를 더한 누적, missing은 order.missing."""
        b = self._robots[robot].order
        placed = dict(b.placed)
        for k, n in b.progress.items():
            placed[k] = placed.get(k, 0) + n
        self._out.append(ReportTripCmd(b.order_id, robot, status, placed, dict(b.missing), reason))

    def _finish_fill(self, robot) -> None:
        """남은 것이 없을 때의 마무리(pseudocode 4단계, 설계 4.2). 출력 순서는 ArmResetCmd가 먼저(설계 7절)."""
        rec = self._robots[robot]
        b = rec.order
        self._out.append(ArmResetCmd(robot))
        b.missing = self._remaining_of(b.items, b.placed)
        if not b.placed:
            # 하나도 못 담음: CANCELED는 최종 보고(7단계)라 최종 집합에 넣고 보류 목록에서 빼며
            # 로봇의 주문과 held를 비운다. shipped는 올리지 않는다(내보낸 주문이 아님).
            self._report_final(robot, CANCELED, f"nothing could be placed, missing {b.missing}")
            rec.stage = ""
            self._out.append(ReleaseCmd(robot))
            return
        # 다 담았거나 일부만 담았거나 늘 내보낸다(v2.4에는 ship_partial 없음). 주문은 출고 보고까지 묶어 둔다.
        rec.basket = FULL
        rec.stage = ""
        self._start_outbound(robot)
        self._report_trip(robot, TRIP_TRANSPORTING)

    def _report_final(self, robot, status, detail="") -> None:
        """끝 상태 보고(SHIPPED · PARTIAL · CANCELED) 한 곳(pseudocode 7단계, 설계 4.7).

        ReportOrderCmd를 내고, 주문 id를 최종 보고 집합에 넣고 보류 목록에서 빼며, 로봇의 주문과 held를 비운다.
        shipped는 SHIPPED · PARTIAL일 때만 올린다(CANCELED는 내보낸 주문이 아니다).
        stage와 basket은 호출한 쪽이 정한다(CANCELED는 쉼, 출고는 교환 흐름이 이어 감).
        """
        rec = self._robots[robot]
        b = rec.order
        self._out.append(ReportOrderCmd(b.order_id, robot, status, dict(b.placed),
                                        dict(b.missing), detail))
        self._final.add(b.order_id)
        self._holds.pop(b.order_id, None)
        rec.order = None
        rec.held = ""
        if status in (SHIPPED, PARTIAL):
            self.shipped += 1

    def _start_outbound(self, robot) -> None:
        """OUTBOUND 호출을 새 id로 내고 outbound_goal에 둔다(pseudocode 5단계). 결과는 on_dispatch_result가 받는다."""
        goal = self._next_call
        self._next_call += 1
        self._robots[robot].outbound_goal = goal
        self._out.append(DispatchCmd(goal, self._outbound, robot))

    # ---- 대기 주문과 열린 호출 ----
    def _can_bind(self, rec) -> bool:
        """묶을 자격(설계 4.3): 주문이 없고, held가 아니고, 쉬는 중(stage "")이고, 바구니가 비어 있다."""
        return (rec.order is None and rec.held == "" and rec.stage == ""
                and rec.basket == EMPTY)

    def _bind(self, robot, order) -> None:
        """대기 주문 하나를 로봇에 묶고 알린다(설계 4.3, pseudocode 2단계)."""
        rec = self._robots[robot]
        rec.order = _Bound(order.order_id, dict(order.items), {}, {}, {}, False)
        rec.last_order_id = order.order_id
        rec.bound += 1
        rec.stage = FILLING
        self._out.append(EventOut("INFO", "ORDER_BOUND", order.order_id, robot))
        self._out.append(ReportTripCmd(order.order_id, robot, TRIP_ASSIGNED, {}, {}))

    def _waiting_orders(self) -> list:
        """주문판 순서대로, 묶이지 않았고 최종 보고도 보류도 아니며 용량 안인 주문(설계 4.6)."""
        bound = {r.order.order_id for r in self._robots.values() if r.order is not None}
        return [o for o in self._board
                if o.order_id not in bound and o.order_id not in self._final
                and o.order_id not in self._holds and o.order_id not in self._over_capacity]

    def _match_calls(self) -> None:
        """열린 WORK 호출 수를 대기 주문 수에 맞춘다. 넘치면 나중에 연 것부터 거둔다."""
        want = len(self._waiting_orders())
        while len(self._open_calls) < want:
            self._open_calls.append(self._next_call)
            self._out.append(DispatchCmd(self._next_call, self._work))
            self._next_call += 1
        while len(self._open_calls) > want:
            self._out.append(CancelDispatchCmd(self._open_calls.pop()))

    def order_of(self, robot) -> str:
        """로봇에 묶인 주문 id. 없거나 모르는 로봇이면 ""."""
        rec = self._robots.get(robot)
        return rec.order.order_id if rec and rec.order else ""

    def robot_view(self, robot) -> dict:
        """로봇 한 대의 주문 쪽 상태. 모르는 로봇이면 {}.

        stage: "", FILLING, SWAPPING. basket: EMPTY, FULL, NONE. order: 묶인 주문 id 또는 None.
        placed: 품목별로 담긴 수.
        """
        if robot not in self._names:
            return {}
        rec = self._robots[robot]
        order = rec.order
        return {"stage": rec.stage, "basket": rec.basket,
                "order": order.order_id if order else None,
                "placed": dict(order.placed) if order else {}}
