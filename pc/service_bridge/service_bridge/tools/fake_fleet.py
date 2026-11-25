# ---- 가짜 fleet: fleet_manager_node가 브리지 · central을 부르는 방식만 흉내 낸다 ----
# 배정 · 로봇은 없다. 서비스 호출은 작업 스레드 하나에서 기다리고, 실행기 스레드는 막지 않는다.
# fleet_manager_node와 같이 켜지 않는다(둘 다 같은 주문판을 보고 보고함).
from __future__ import annotations

import queue
import threading
import uuid

import rclpy
from fleet_core.common.contract import to_ros_qos
from fleet_interfaces.msg import OrderBoard
from fleet_interfaces.srv import Hello, ReportOrder, ReportTrip
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node

from service_bridge.tools.common import (
    call_once,
    load_default_contract,
    parse_items,
    to_order_items,
)

NODE_NAME = "fleet"           # Hello의 node 값
ROBOT_ID = "fake1"
# F-08 값: 3초 한도, 1 → 2 → 4초(최대 10초) 간격, 포기하지 않음
TIMEOUT_SEC = 3.0
FIRST_GAP_SEC = 1.0
MAX_GAP_SEC = 10.0
QUEUE_POLL_SEC = 0.2


# ---- 노드 만들기 ----
class FakeFleetNode(Node):
    def __init__(self, **kwargs):
        super().__init__("fake_fleet", **kwargs)
        self._step_s = float(self.declare_parameter("step_s", 1.0).value)
        self._duplicate = bool(self.declare_parameter("duplicate_every_report", False).value)
        self._missing = parse_items(str(self.declare_parameter("missing", "").value))
        contract = load_default_contract()
        self._hello = self.create_client(Hello, contract.name("hello"))
        self._report_trip = self.create_client(ReportTrip, contract.name("report_trip"))
        self._report_order = self.create_client(ReportOrder, contract.name("report_order"))
        self.create_subscription(
            OrderBoard, contract.name("order_board"), self._on_board,
            to_ros_qos(contract.qos("order_board")))

        # 켤 때마다 새 boot_id: 브리지는 이것으로 fleet 재시작을 알아본다
        self.boot_id = str(uuid.uuid4())
        self.epoch = 0
        self.duplicates = 0      # 브리지가 DUPLICATE로 답한 수 (시험용)
        self.order_reports = 0   # central이 받은 ReportOrder 수 (시험용)
        self._n = 0              # 노드 전체에서 1씩 늘림 (운반 건마다 따로 세지 않음)
        self._seen: set[str] = set()
        self._todo: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._work, name="fake_fleet_worker", daemon=True)
        self._worker.start()

    def destroy_node(self):
        self._stop.set()
        self._worker.join(timeout=TIMEOUT_SEC + 1.0)
        return super().destroy_node()

    # 실행기 스레드: 새 운반 건을 줄에 넣기만 한다
    def _on_board(self, msg: OrderBoard) -> None:
        for order in msg.orders:
            if order.order_id not in self._seen:
                self._seen.add(order.order_id)
                self._todo.put((order.order_id, {i.kind: i.count for i in order.items}))

    # ---- 작업 스레드: Hello 뒤 운반 건을 하나씩 끝까지 보고 ----
    def _work(self) -> None:
        # epoch를 받기 전에는 보고하지 않는다 (F-06)
        response = self._call_until_accepted(
            self._hello, lambda: Hello.Request(node=NODE_NAME, boot_id=self.boot_id), "Hello")
        if response is None:
            return
        self.epoch = response.epoch
        self.get_logger().info(f"Hello 받음: epoch={self.epoch} boot_id={self.boot_id}")
        while not self._stop.is_set():
            try:
                trip_id, items = self._todo.get(timeout=QUEUE_POLL_SEC)
            except queue.Empty:
                continue
            self._run_trip(trip_id, items)

    def _run_trip(self, trip_id: str, items: dict[str, int]) -> None:
        # 못 담을 수는 매개변수 missing으로 정한다. 하나라도 못 담으면 PARTIAL
        missing = {k: min(q, items[k]) for k, q in self._missing.items() if k in items and q > 0}
        placed = {k: q - missing.get(k, 0) for k, q in items.items() if q > missing.get(k, 0)}
        final = ReportTrip.Request.STATUS_PARTIAL if missing else ReportTrip.Request.STATUS_SHIPPED
        steps = [
            (ReportTrip.Request.STATUS_ASSIGNED, {}, {}, ""),
            (ReportTrip.Request.STATUS_FILLING, {}, {}, ""),
            (ReportTrip.Request.STATUS_TRANSPORTING, placed, missing, ""),
            (final, placed, missing, "OUT_OF_STOCK" if missing else ""),
        ]
        for i, (status, p, m, reason) in enumerate(steps):
            if i and self._stop.wait(self._step_s):
                return
            self._n += 1
            # 시험용: 같은 번호를 두 번 보내 두 번째가 DUPLICATE인지 본다
            for _ in range(2 if self._duplicate else 1):
                if not self._send_report(trip_id, status, p, m, reason):
                    return
        # 끝 보고가 accepted=true를 받은 뒤 central 주문판에서 뺀다 (F-10)
        order_status = (ReportOrder.Request.STATUS_PARTIAL if missing
                        else ReportOrder.Request.STATUS_SHIPPED)
        response = self._call_until_accepted(
            self._report_order,
            lambda: ReportOrder.Request(
                order_id=trip_id, robot_id=ROBOT_ID, status=order_status,
                placed=to_order_items(placed), missing=to_order_items(missing)),
            f"ReportOrder {trip_id}")
        if response is not None:
            self.order_reports += 1
            self.get_logger().info(f"{trip_id} 끝: {order_status}")

    # ---- 서비스 호출과 다시 보내기 ----
    def _send_report(self, trip_id, status, placed, missing, reason) -> bool:
        n = self._n  # 다시 보낼 때도 같은 번호

        def make():
            return ReportTrip.Request(
                trip_id=trip_id, report_epoch=self.epoch, report_n=n, status=status,
                reason=reason, robot_id=ROBOT_ID, placed=to_order_items(placed),
                missing=to_order_items(missing), stamp=self.get_clock().now().to_msg())

        response = self._call_until_accepted(
            self._report_trip, make, f"ReportTrip {trip_id} ({self.epoch}, {n}) {status}")
        if response is None:
            return False
        if response.result == "DUPLICATE":
            self.duplicates += 1
        self.get_logger().info(
            f"{trip_id} ({self.epoch}, {n}) {status}: {response.result} {response.reason}")
        return True

    def _call_until_accepted(self, client, make_request, what: str):
        """accepted=true가 올 때까지 다시 보낸다(F-08, 포기하지 않음). 멈추면 None."""
        gap = FIRST_GAP_SEC
        while not self._stop.is_set():
            response = call_once(client, make_request(), TIMEOUT_SEC, self._stop)
            if response is not None and response.accepted:
                return response
            why = "응답 없음" if response is None else f"거절 {getattr(response, 'reason', '')}"
            self.get_logger().warn(f"{what}: {why}, {gap:.0f}초 뒤 다시")
            if self._stop.wait(gap):
                break
            gap = min(gap * 2, MAX_GAP_SEC)
        return None


# ---- 실행 ----
def main(args=None):
    rclpy.init(args=args)
    node = FakeFleetNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        executor.shutdown(timeout_sec=2.0)
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
