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

from fleet_core.common.commands import (
    FILL,
    FORCE_NOT_IMPLEMENTED,
    NO_ROBOT,
    TRIP_ASSIGNED,
    CancelDispatchCmd,
    DispatchCmd,
    EventOut,
    ReportTripCmd,
)

# ---- 다른 모듈이 가져다 쓰는 상태 이름 ----
FILLING = "FILLING"
SWAPPING = "SWAPPING"
EMPTY = "EMPTY"
FULL = "FULL"
NONE = "NONE"


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
        self._bound = {name: None for name in robots}  # 로봇 -> 묶인 OrderRecord
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
        # ---- 열린 호출이 아닌 답은 무시 ----
        if goal_id not in self._open_calls:
            return
        self._open_calls.remove(goal_id)
        # ---- 아는 빈 Pode가 닿았으면 맨 앞 대기 주문을 묶음 ----
        if success and self._bound.get(robot_id, True) is None:
            waiting = self._waiting_orders()
            if waiting:
                order = waiting[0]
                self._bound[robot_id] = order
                self._out.append(EventOut("INFO", "ORDER_BOUND", order.order_id, robot_id))
                self._out.append(ReportTripCmd(order.order_id, robot_id, TRIP_ASSIGNED, {}, {}))
        # ---- 실패한 호출은 새 id로 다시 열림 ----
        self._match_calls()

    def on_arm_report(self, report) -> None:
        """팔 작업 층(ArmJobs)의 보고가 올 때 불린다.

        report: ArmFillResult, ArmSwapResult, ArmDeclined, ArmLost, ArmLate, ArmFault 가운데 하나.
        낼 수 있는 출력: ArmFailedCmd, ArmResetCmd, ReportOrderCmd, ReleaseCmd, EventOut.
        """

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

    # ---- 출력과 조회 ----
    def take_output(self) -> list:
        """쌓아 둔 명령 목록을 주고 비운다. FleetManager가 입력을 넣은 뒤마다 부른다."""
        out, self._out = self._out, []
        return out

    # ---- 대기 주문과 열린 호출 ----
    def _waiting_orders(self) -> list:
        """주문판 순서대로, 로봇에 묶이지 않았고 용량 안인 주문."""
        bound = {o.order_id for o in self._bound.values() if o is not None}
        return [o for o in self._board
                if o.order_id not in bound and o.order_id not in self._over_capacity]

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
        order = self._bound.get(robot)
        return order.order_id if order else ""

    def robot_view(self, robot) -> dict:
        """로봇 한 대의 주문 쪽 상태. 모르는 로봇이면 {}.

        stage: "", FILLING, SWAPPING. basket: EMPTY, FULL, NONE. order: 묶인 주문 id 또는 None.
        placed: 품목별로 담긴 수.
        """
        if robot not in self._names:
            return {}
        order = self._bound[robot]
        if order is None:
            return {"stage": "", "basket": EMPTY, "order": None, "placed": {}}
        return {"stage": FILLING, "basket": EMPTY, "order": order.order_id, "placed": {}}
