# ---- central 시험용 가짜 브리지: /bridge/list_board_trips에 답하고 /system/mode(TEST)를 한 번 발행 ----
#     python -m service_bridge.tools.board_trips_stub [--trips FILE.json]
# FILE.json: [{"trip_id": "...", "items": {"mentos": 1}, "status": "RECEIVED",
#              "submitted_at": "2026-11-28T09:00:00+00:00"}]. 없으면 빈 목록으로 답한다.
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import rclpy
from fleet_core.common.contract import to_ros_qos
from fleet_interfaces.msg import SystemMode
from fleet_interfaces.srv import ListOpenTrips
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node

from service_bridge.core.board_trips import BoardTrip
from service_bridge.ros import convert
from service_bridge.tools.common import load_default_contract


# ---- 파일 → 목록 ----
def load_trips(path: str) -> list[BoardTrip]:
    """파일 항목마다 trip_id · items · status · submitted_at(ISO)을 읽는다. 나머지 칸은 빈 값."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    trips = []
    for i, r in enumerate(raw, start=1):
        at = datetime.fromisoformat(r["submitted_at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        trips.append(BoardTrip(
            trip_id=r["trip_id"], order_id=r.get("order_id", f"STUB-{i}"), trip_index=1,
            trip_count=1, items={k: int(n) for k, n in r["items"].items()},
            status=r.get("status", "RECEIVED"), reason="", robot_id="", placed={}, missing={},
            submitted_at=at, report_epoch=0, report_n=0))
    return trips


# ---- 노드 ----
class BoardTripsStub(Node):
    def __init__(self, trips: list[BoardTrip], **kwargs):
        super().__init__("board_trips_stub", **kwargs)
        self._trips = trips
        contract = load_default_contract()
        self._mode_pub = self.create_publisher(
            SystemMode, contract.name("system_mode"), to_ros_qos(contract.qos("system_mode")))
        self._mode_pub.publish(
            SystemMode(mode=SystemMode.MODE_TEST, stamp=self.get_clock().now().to_msg()))
        self.create_service(ListOpenTrips, contract.name("list_board_trips"), self._on_list)
        self.get_logger().info(f"list_board_trips 서버 열림: {len(trips)}건, mode TEST 발행")

    def _on_list(self, request, response):
        response.accepted = True
        response.trips = [convert.open_trip(t) for t in self._trips]
        response.reason = ""
        return response


# ---- 실행 ----
def main(args=None) -> int:
    parser = argparse.ArgumentParser(description="central 시험용 가짜 브리지 목록 서버")
    parser.add_argument("--trips", help="목록으로 돌려줄 운반 건 JSON 파일")
    ros_args = rclpy.utilities.remove_ros_args(args if args is not None else sys.argv)
    opts = parser.parse_args(ros_args[1:])
    trips = load_trips(opts.trips) if opts.trips else []
    rclpy.init(args=args)
    node = BoardTripsStub(trips)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
