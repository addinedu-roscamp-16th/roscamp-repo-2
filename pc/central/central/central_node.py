# ---- 간이 central: SubmitTrip 서버 · 주문판 발행 · ReportOrder 서버 (F-04 · F-05 · F-10) ----
from __future__ import annotations

import time
import uuid

import rclpy
from ament_index_python.packages import get_package_share_directory
from fleet_core.common.contract import load_contract, to_ros_qos
from fleet_interfaces.msg import CentralState, Order, OrderBoard, OrderItem, SystemMode
from fleet_interfaces.srv import ListOpenTrips, ReportOrder, SubmitTrip
from rclpy.node import Node

from central.board import FINAL_STATUSES, HELD, Board
from central.lifecycle import CONNECTING, RUNNING, SYNCING, Lifecycle

NOT_READY = "NOT_READY"
LIST_TIMEOUT_SEC = 3.0  # 목록 응답을 기다리는 한도. TODO(Q-09): IR-40 Report* 임시 값
FIND_LOG_SEC = 5.0  # 브리지를 못 찾는 동안 로그를 남기는 간격


# ---- 요청 품목 합치기 ----
def sum_items(items) -> dict:
    """같은 kind는 합친다. 개수나 kind가 잘못된 항목이 있으면 빈 목록으로 거절시킨다."""
    out: dict = {}
    for item in items:
        if not item.kind or item.count < 1:
            return {}
        out[item.kind] = out.get(item.kind, 0) + item.count
    return out


# ---- 노드 만들기 ----
class CentralNode(Node):
    def __init__(self):
        super().__init__("central")
        contract = load_contract(
            get_package_share_directory("fleet_interfaces") + "/contract.yaml")
        self._board = Board()
        self._life = Lifecycle(self._board, uuid.uuid4().hex)
        self._mode = ""
        self._mode_seen = False
        self._pending = None  # 보낸 뒤 답을 기다리는 목록 요청(future)
        self._pending_since = 0.0
        self._next_call_at = 0.0  # 이 시각(monotonic) 전에는 목록을 다시 부르지 않음
        self._next_find_log = 0.0
        self._pub = self.create_publisher(
            OrderBoard, contract.name("order_board"), to_ros_qos(contract.qos("order_board")))
        self._state_pub = self.create_publisher(
            CentralState, contract.name("central_state"),
            to_ros_qos(contract.qos("central_state")))
        self._list_client = self.create_client(
            ListOpenTrips, contract.name("list_board_trips"))
        self.create_service(SubmitTrip, contract.name("submit_trip"), self._submit)
        self.create_service(ReportOrder, contract.name("report_order"), self._report)
        self.create_subscription(
            SystemMode, contract.name("system_mode"), self._on_system_mode,
            to_ros_qos(contract.qos("system_mode")))
        self.create_timer(1.0, self._on_second)  # 바뀔 때 말고도 1초마다 다시 발행
        self.create_timer(0.5, self._sync_step)  # 연결 확인 · 목록 부르기(다시 부르기 간격이 1초 안팎이라 더 촘촘히)
        self.get_logger().info(f"central 시작 boot_id={self._life.boot_id}: CONNECTING")
        self._publish_state()

    # ---- 서버 콜백 ----
    def _submit(self, request, response):
        if not self._life.accepts_work():
            # 아직 주문판을 다 세우지 못함: 기억하지 않아야 브리지가 다시 보냈을 때 정상 접수된다
            response.accepted, response.reason = False, NOT_READY
            return response
        before = len(self._board.entries())
        response.accepted, response.reason = self._board.submit(
            request.trip_id, sum_items(request.items), request.submitted_at)
        if len(self._board.entries()) != before:
            self._publish()
        return response

    def _report(self, request, response):
        if not self._life.accepts_work():
            response.accepted = False  # fleet이 다시 보낸다
            return response
        # 끝 상태도 HELD도 아니면 WARN 뒤 accepted=false. 이미 뺐거나 모르는 건도 accepted=true(F-10)
        # HELD는 주문판에 남기고 관리자 조치 대기로 표시한다
        if request.status == HELD:
            if self._board.on_board(request.order_id):
                self.get_logger().info(
                    f"관리자 조치 대기: order_id={request.order_id} "
                    f"hold_reason={request.hold_reason}")
            elif self._board.knows(request.order_id):
                self.get_logger().warn(f"이미 끝난 건에 HELD 보고: {request.order_id}")
            else:
                self.get_logger().warn(f"모르는 order_id 보고: {request.order_id}")
        elif request.status not in FINAL_STATUSES:
            self.get_logger().warn(
                f"끝 상태가 아닌 ReportOrder 거절: order_id={request.order_id} "
                f"status={request.status} (ReportOrder 철자는 CANCELED)")
        elif not self._board.knows(request.order_id):
            self.get_logger().warn(f"모르는 order_id 보고: {request.order_id}")
        before = len(self._board.entries())
        response.accepted = self._board.report(request.order_id, request.status)
        if len(self._board.entries()) != before:
            self._publish()
        return response

    # ---- 구독 · 발행 ----
    def _on_system_mode(self, msg: SystemMode) -> None:
        # TODO(Q-24): 모드가 다르면 SubmitTrip 거절은 아직 안 함 (IR-73). 지금은 로그만
        self._mode_seen = True
        if msg.mode != self._mode:
            self._mode = msg.mode
            self.get_logger().info(f"system mode {msg.mode}")

    # ---- 단계 진행: 연결 설정 → 주문판 갱신 → 실행 중 ----
    def _sync_step(self) -> None:
        now = time.monotonic()
        if self._life.state == CONNECTING:
            if self._life.ready_to_sync(self._list_client.service_is_ready(), self._mode_seen):
                self.get_logger().info("브리지 목록 서비스와 system mode 확인: SYNCING")
                self._publish_state()
            elif now >= self._next_find_log:
                self._next_find_log = now + FIND_LOG_SEC
                self.get_logger().info("브리지를 찾는 중 (목록 서비스 · system mode 대기)")
        if self._life.state != SYNCING:
            return
        if self._pending is not None:
            if now - self._pending_since > LIST_TIMEOUT_SEC:
                # 늦은 답은 무시한다(요청을 버림)
                self._list_client.remove_pending_request(self._pending)
                self._pending = None
                self._schedule_retry(now, "목록 응답 시간 초과")
            return
        if now >= self._next_call_at:
            self._pending = self._list_client.call_async(ListOpenTrips.Request(epoch=0))
            self._pending_since = now
            self._pending.add_done_callback(self._on_list_done)

    def _schedule_retry(self, now: float, why: str) -> None:
        delay = self._life.next_retry_sec()
        self._next_call_at = now + delay
        self.get_logger().warn(f"{why}: {delay:.1f}초 뒤 다시 부름")

    def _on_list_done(self, future) -> None:
        if future is not self._pending:
            return  # 이미 버린 요청의 늦은 답
        self._pending = None
        try:
            result = future.result()
        except Exception as exc:  # 서비스가 사라지는 등
            self._schedule_retry(time.monotonic(), f"목록 요청 실패({exc})")
            return
        trips = [(t.trip_id, {i.kind: i.count for i in t.items}, t.submitted_at, t.status)
                 for t in result.trips]
        if not self._life.on_list(result.accepted, trips):
            self._schedule_retry(time.monotonic(), f"목록 거절({result.reason})")
            return
        self.get_logger().info(
            f"주문판 갱신 끝: {len(self._board.entries())}건 "
            f"(HELD {len(self._board.held())}건) boot_id={self._life.boot_id}")
        self._publish_state()
        self._publish()

    # ---- 발행 ----
    def _on_second(self) -> None:
        self._publish_state()
        self._publish()

    def _publish_state(self) -> None:
        self._state_pub.publish(CentralState(
            boot_id=self._life.boot_id, state=self._life.state,
            stamp=self.get_clock().now().to_msg()))

    # Order.order_id에 trip_id를 싣는다 (F-05)
    def _publish(self) -> None:
        if self._life.state != RUNNING:
            return  # 주문판을 다 세우기 전에는 빈 판도 내지 않는다
        orders = [
            Order(order_id=e.trip_id, created_at=e.created_at,
                  items=[OrderItem(kind=k, count=c) for k, c in e.items.items()])
            for e in self._board.entries()]
        self._pub.publish(OrderBoard(
            run_mode=OrderBoard.MODE_RUN, orders=orders,
            stamp=self.get_clock().now().to_msg()))


# ---- 실행 ----
def main(args=None):
    rclpy.init(args=args)
    node = CentralNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception:  # SIGINT가 수신 중에 떨어질 수 있다. 진짜 오류면 ROS가 아직 살아 있다
        if rclpy.ok():
            raise
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
