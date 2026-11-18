# ---- OrderOrchestrator: 주문판을 받아 로봇에 주문을 묶고 진행하는 자리(뼈대) ----
"""주문 처리의 뼈대. 몸통은 비어 있고 흐름은 같은 폴더의 pseudocode.md에 있다.

FleetManager가 이 클래스를 하나 만들어 아래 입력 함수를 부르고, `take_output()`으로 명령을
가져간다. 이름과 시그니처는 FleetManager와 맞물려 있으니 바꾸지 않는다.
낼 수 있는 명령은 모두 fleet_core.common.commands에 있다.
"""
from __future__ import annotations

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

    # ---- 주문판, 배차, 팔 보고 입력 ----
    def on_order_board(self, run_mode, orders) -> None:
        """central의 주문판이 올 때마다 불린다. 검사를 통과한 주문판만 온다.

        run_mode: "RUN", "PAUSE", "STOP". orders: OrderRecord 목록이고 목록 순서가 우선순위다.
        낼 수 있는 출력: DispatchCmd(로봇 이름 없이 WORK를 부르는 열린 호출), CancelDispatchCmd.
        """

    def on_dispatch_feedback(self, goal_id, robot_id, phase) -> None:
        """교통 층이 호출의 진행을 알릴 때 불린다.

        goal_id: DispatchCmd에 넣은 번호. robot_id: 호출을 맡은 로봇(없으면 ""). phase: 진행 단계.
        phase가 NO_ROBOT이면 맡을 로봇이 없다는 뜻이다.
        낼 수 있는 출력: EventOut(WARN, NO_ROBOT_AVAILABLE).
        """

    def on_dispatch_result(self, goal_id, success, robot_id, reason="") -> None:
        """로봇이 스테이션에 닿았거나 호출이 실패했을 때 불린다.

        goal_id: DispatchCmd에 넣은 번호. success: 스테이션에 닿았는지. robot_id: 닿은 로봇.
        reason: 실패 이유.
        낼 수 있는 출력: EventOut(ORDER_BOUND), ReleaseCmd, DispatchCmd, CancelDispatchCmd.
        """

    def on_arm_report(self, report) -> None:
        """팔 작업 층(ArmJobs)의 보고가 올 때 불린다.

        report: ArmFillResult, ArmSwapResult, ArmDeclined, ArmLost, ArmLate 가운데 하나.
        낼 수 있는 출력: ArmFailedCmd, ArmResetCmd, ReportOrderCmd, ReleaseCmd, EventOut.
        """

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
        return []

    def order_of(self, robot) -> str:
        """로봇에 묶인 주문 id. 없거나 모르는 로봇이면 ""."""
        return ""

    def robot_view(self, robot) -> dict:
        """로봇 한 대의 주문 쪽 상태. 모르는 로봇이면 {}.

        stage: "", FILLING, SWAPPING. basket: EMPTY, FULL, NONE. order: 묶인 주문 id 또는 None.
        placed: 품목별로 담긴 수.
        """
        if robot not in self._names:
            return {}
        return {"stage": "", "basket": EMPTY, "order": None, "placed": {}}
