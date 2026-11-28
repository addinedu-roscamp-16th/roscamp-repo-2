# ---- 브리지 노드: /system/mode 발행, /central/state 지켜보기, DB 준비 뒤 서버들 ----
from __future__ import annotations

import time
import traceback
from collections.abc import Callable

from ament_index_python.packages import get_package_share_directory
from fleet_core.common.contract import load_contract, to_ros_qos
from fleet_interfaces.msg import CentralState, RobotStatusArray, SystemMode
from fleet_interfaces.srv import Hello, ListOpenTrips, ReportCommand, ReportTrip
from psycopg_pool import ConnectionPool
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.exceptions import ParameterException
from rclpy.node import Node
from rclpy.parameter import Parameter

from service_bridge.core.board_trips import list_board_trips
from service_bridge.core.central_watch import VIEW_LOST, CentralWatch, record_central_restart
from service_bridge.core.commands import apply_command
from service_bridge.core.hello import apply_hello
from service_bridge.core.reports import apply_report
from service_bridge.ros import convert
from service_bridge.ros.dispatcher import Dispatcher

MODES = (SystemMode.MODE_NORMAL, SystemMode.MODE_TEST)
REQUEST_BUDGET_SEC = 2.5   # 요청 마감 = 받은 시각 + 2.5초 (fleet 호출 한도 3초보다 짧게)
DB_PROBE_PERIOD_SEC = 0.5  # DB 첫 연결 확인 주기
DB_PROBE_TIMEOUT_SEC = 0.2  # 실행기 스레드를 오래 잡지 않게 짧게
# 메시지가 끊겨 LOST로 바뀌는 것은 받는 콜백이 없으니 주기적으로 본다 (신선함 한도 3초보다 짧게)
CENTRAL_CHECK_SEC = 1.0
CENTRAL_DB_TIMEOUT_SEC = 1.0  # 재시작 기록 한 번: 드문 일이라 넘기기보다 조금 길게 기다린다


# ---- 노드 만들기와 켜질 때 순서 ----
class BridgeNode(Node):
    def __init__(self, pool: ConnectionPool,
                 on_robots: Callable[[list[dict]], None] | None = None,
                 on_central: Callable[[str], None] | None = None, **kwargs):
        """on_central: 보이는 central 상태(CONNECTING · SYNCING · RUNNING · LOST)가 바뀔 때 부름."""
        super().__init__("service_bridge", **kwargs)
        self._pool = pool
        self._on_robots = on_robots
        self._on_central = on_central
        self._central = CentralWatch()
        self._central_view = VIEW_LOST  # 마지막으로 알린 값 (_central_group 안에서만 바꿈)
        self._dispatcher: Dispatcher | None = None
        self._mode = self._read_mode()
        self._contract = load_contract(
            get_package_share_directory("fleet_interfaces") + "/contract.yaml")
        self._services = []

        # 1. DB와 상관없이 바로 모드 발행 (LATCHED, 실행 중에는 바꾸지 않음)
        self._mode_pub = self.create_publisher(
            SystemMode, self._contract.name("system_mode"),
            to_ros_qos(self._contract.qos("system_mode")))
        self._mode_pub.publish(SystemMode(mode=self._mode, stamp=self.get_clock().now().to_msg()))
        self.get_logger().info(f"system mode {self._mode} 발행")

        # 로봇 상태 구독 (DB와 무관, LATCHED라 늦게 켜져도 마지막 값을 받음)
        self._robots_sub = self.create_subscription(
            RobotStatusArray, self._contract.name("robots"), self._on_robots_msg,
            to_ros_qos(self._contract.qos("robots")),
            callback_group=MutuallyExclusiveCallbackGroup())

        # central 상태 구독 (DB와 무관). 구독 · 확인 타이머는 한 그룹: _central_view를 하나씩 바꿈
        self._central_group = MutuallyExclusiveCallbackGroup()
        self._central_sub = self.create_subscription(
            CentralState, self._contract.name("central_state"), self._on_central_msg,
            to_ros_qos(self._contract.qos("central_state")), callback_group=self._central_group)
        self._central_timer = self.create_timer(
            CENTRAL_CHECK_SEC, self._check_central, callback_group=self._central_group)

        # 2. DB 첫 연결이 될 때까지 확인. 되면 3 · 4 · 5.
        # 서버가 없는 동안 fleet은 시간 초과 뒤 다시 보내므로 잃는 보고가 없다
        self._probe_timer = self.create_timer(DB_PROBE_PERIOD_SEC, self._probe_db)

    # 기본값 없는 필수 매개변수: 빠지거나 NORMAL · TEST가 아니면 켜지지 않는다
    def _read_mode(self) -> str:
        try:
            self.declare_parameter(
                "mode", Parameter.Type.STRING,
                ParameterDescriptor(description="운영 모드 NORMAL · TEST (기본값 없음)"))
            mode = self.get_parameter("mode").value
        except ParameterException as e:
            raise ValueError(f"매개변수 mode가 없거나 잘못됨: {e}") from e
        if mode not in MODES:
            raise ValueError(f"매개변수 mode는 NORMAL · TEST 중 하나여야 함: {mode!r}")
        return mode

    def _probe_db(self) -> None:
        if self._probe_timer is None:
            return
        try:
            with self._pool.connection(timeout=DB_PROBE_TIMEOUT_SEC) as conn:
                conn.execute("select 1")
                sessions = conn.execute(
                    "select node, epoch, boot_id, hello_at from bridge_session order by node"
                ).fetchall()
        except Exception as e:  # 아직 DB 없음: 다음 주기에 다시
            self.get_logger().warn(f"DB 연결 대기 중: {e}", throttle_duration_sec=5.0)
            return
        # 3. 브리지 재시작은 fleet 재시작이 아님: epoch · HELD는 건드리지 않고 기록만
        if not sessions:
            self.get_logger().info("bridge_session 비어 있음")
        for node, epoch, boot_id, hello_at in sessions:
            self.get_logger().info(
                f"bridge_session node={node} epoch={epoch} boot_id={boot_id} hello_at={hello_at}")
        # 4. 서버. 각자 MutuallyExclusive 그룹이라 Hello와 보고는 서로 막지 않고,
        # 같은 서버의 요청은 하나씩 처리된다. 실행기 스레드 4개는 풀 크기와 같다(main.py)
        self._services = [
            self.create_service(
                Hello, self._contract.name("hello"), self._on_hello,
                callback_group=MutuallyExclusiveCallbackGroup()),
            self.create_service(
                ReportTrip, self._contract.name("report_trip"), self._on_report,
                callback_group=MutuallyExclusiveCallbackGroup()),
            self.create_service(
                ReportCommand, self._contract.name("report_command"), self._on_command,
                callback_group=MutuallyExclusiveCallbackGroup()),
            # central이 켜질 때 주문판을 다시 세우려고 부른다 (설계 6.3)
            self.create_service(
                ListOpenTrips, self._contract.name("list_board_trips"), self._on_list_board_trips,
                callback_group=MutuallyExclusiveCallbackGroup()),
        ]
        # 5. 운반 건 넘기기 시작: 켜질 때 남은 줄도 다시 보낸다
        self._dispatcher = Dispatcher(
            self, self._pool, self._contract,
            can_dispatch=lambda: self._central.can_dispatch(time.monotonic()))
        self._dispatcher.start()
        self._probe_timer.cancel()
        self.destroy_timer(self._probe_timer)
        self._probe_timer = None
        self.get_logger().info("DB 연결됨, Hello · ReportTrip · ReportCommand · list_board_trips 서버 열림")

    @property
    def mode(self) -> str:
        return self._mode

    def wake_dispatch(self) -> None:
        """웹 스레드에서 불러도 됨. 아직 켜지는 중이면 start()가 남은 줄을 보낸다."""
        if self._dispatcher is not None:
            self._dispatcher.wake()

    def _on_robots_msg(self, msg: RobotStatusArray) -> None:
        if self._on_robots is None:
            return
        try:
            self._on_robots(convert.robots(msg))
        except Exception:
            self.get_logger().error(f"on_robots 예외\n{traceback.format_exc()}")

    # ---- central 지켜보기 (설계 6.4) ----
    def _on_central_msg(self, msg: CentralState) -> None:
        try:
            old = self._central.on_state(msg.boot_id, msg.state, time.monotonic())
            if old is not None:
                self._central_restarted(old, msg.boot_id)
            self._check_central()
        except Exception:
            self.get_logger().error(f"/central/state 처리 예외\n{traceback.format_exc()}")

    def _central_restarted(self, old: str, new: str) -> None:
        # DB가 아직 없거나 잠깐 끊겼으면 한 번만 시도하고 넘어간다: 안내 알림이고, 넘기기도 DB가
        # 있어야 돌므로 풀 기록이 없어 생기는 일은 앞 실패 기록에 따른 쉼이 남는 것뿐이다
        try:
            with self._pool.connection(timeout=CENTRAL_DB_TIMEOUT_SEC) as conn:
                n = record_central_restart(conn, old, new)
        except Exception as e:
            self.get_logger().error(
                f"central 재시작 기록 못 함 old_boot_id={old} new_boot_id={new}: {e!r}")
            return
        self.get_logger().info(
            f"central 재시작 알아챔 old_boot_id={old} new_boot_id={new}, 실패 기록 푼 줄 {n}")
        if self._dispatcher is not None:
            self._dispatcher.forget_backoff()

    def _check_central(self) -> None:
        view = self._central.view(time.monotonic())
        if view == self._central_view:
            return
        self._central_view = view
        self.get_logger().info(f"central 상태 {view}")
        if view == "RUNNING" and self._dispatcher is not None:
            self._dispatcher.wake()
        if self._on_central is not None:
            try:
                self._on_central(view)
            except Exception:
                self.get_logger().error(f"on_central 예외\n{traceback.format_exc()}")

    # ---- 끌 때 ----
    def close_servers(self) -> None:
        """끌 때 실행기를 멈추기 전에 서버부터 없앤다 (설계 6.1)."""
        for srv in self._services:
            self.destroy_service(srv)
        self._services = []

    # ---- 서버 콜백 ----
    # 다른 서비스를 부르거나 기다리지 않음 (IR-31). 커밋한 뒤 답함 (IR-12).
    # 예외를 밖으로 내보내면 실행기가 멈추므로 모두 잡아 accepted=false로 답한다
    def _on_hello(self, request, response):
        deadline = time.monotonic() + REQUEST_BUDGET_SEC
        try:
            out = apply_hello(self._pool, convert.hello_in(request), deadline)
            response.accepted, response.epoch, response.reason = out.accepted, out.epoch, out.reason
        except Exception:
            self.get_logger().error(
                f"Hello 처리 중 예외: node={request.node} boot_id={request.boot_id}\n"
                f"{traceback.format_exc()}")
            response.accepted, response.epoch, response.reason = False, 0, "DB_ERROR"
        return response

    def _on_report(self, request, response):
        deadline = time.monotonic() + REQUEST_BUDGET_SEC
        try:
            out = apply_report(self._pool, convert.report_in(request), deadline)
            response.accepted, response.result, response.reason = (
                out.accepted, out.result, out.reason)
        except Exception:
            self.get_logger().error(
                f"ReportTrip 처리 중 예외: trip_id={request.trip_id} "
                f"({request.report_epoch}, {request.report_n})\n{traceback.format_exc()}")
            response.accepted, response.result, response.reason = False, "", "DB_ERROR"
        return response

    def _on_list_board_trips(self, request, response):
        # request.epoch는 보지 않는다(central은 0)
        deadline = time.monotonic() + REQUEST_BUDGET_SEC
        try:
            out = list_board_trips(self._pool, deadline)
            response.accepted, response.reason = out.accepted, out.reason
            response.trips = [convert.open_trip(t) for t in out.trips]
        except Exception:
            self.get_logger().error(f"list_board_trips 처리 중 예외\n{traceback.format_exc()}")
            response.accepted, response.trips, response.reason = False, [], "DB_ERROR"
        return response

    def _on_command(self, request, response):
        deadline = time.monotonic() + REQUEST_BUDGET_SEC
        try:
            out = apply_command(self._pool, convert.command_in(request), deadline)
            response.accepted = out.accepted
            # 응답에 result 칸이 없어 판정(APPLIED · DUPLICATE · LATE)은 로그에만 남긴다 (설계 4.4).
            # fleet은 accepted만 보고 다시 보낼지 정하며, 늦은 결과의 경보는 core.commands가 DB에 쓴다
            self.get_logger().info(
                f"ReportCommand {out.result or out.reason}: request_id={request.request_id} "
                f"result={request.result}")
        except Exception:
            self.get_logger().error(
                f"ReportCommand 처리 중 예외: request_id={request.request_id}\n"
                f"{traceback.format_exc()}")
            response.accepted = False
        return response
