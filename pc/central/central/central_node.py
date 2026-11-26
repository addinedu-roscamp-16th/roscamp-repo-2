# ---- 간이 central: SubmitTrip 서버 · 주문판 발행 · ReportOrder 서버 (F-04 · F-05 · F-10) ----
from __future__ import annotations

import rclpy
from ament_index_python.packages import get_package_share_directory
from fleet_core.common.contract import load_contract, to_ros_qos
from fleet_interfaces.msg import Order, OrderBoard, OrderItem, SystemMode
from fleet_interfaces.srv import ReportOrder, SubmitTrip
from rclpy.node import Node

from central.board import FINAL_STATUSES, HELD, Board


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
        self._mode = ""
        self._pub = self.create_publisher(
            OrderBoard, contract.name("order_board"), to_ros_qos(contract.qos("order_board")))
        self.create_service(SubmitTrip, contract.name("submit_trip"), self._submit)
        self.create_service(ReportOrder, contract.name("report_order"), self._report)
        self.create_subscription(
            SystemMode, contract.name("system_mode"), self._on_system_mode,
            to_ros_qos(contract.qos("system_mode")))
        self.create_timer(1.0, self._publish)  # 바뀔 때 말고도 1초마다 다시 발행
        self._publish()

    # ---- 서버 콜백 ----
    def _submit(self, request, response):
        before = len(self._board.entries())
        response.accepted, response.reason = self._board.submit(
            request.trip_id, sum_items(request.items), request.submitted_at)
        if len(self._board.entries()) != before:
            self._publish()
        return response

    def _report(self, request, response):
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
        if msg.mode != self._mode:
            self._mode = msg.mode
            self.get_logger().info(f"system mode {msg.mode}")

    # Order.order_id에 trip_id를 싣는다 (F-05)
    def _publish(self) -> None:
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
