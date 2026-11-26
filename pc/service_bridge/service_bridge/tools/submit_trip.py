# ---- 웹 대신 운반 건 하나를 접수하고 central에 넘긴다 ----
#     python -m service_bridge.tools.submit_trip --items mentos:1,mygummy:1
#     python -m service_bridge.tools.submit_trip --trip-id O-20261125-0001-1   # DB에 넣지 않고 다시 보냄
from __future__ import annotations

import argparse
import sys
import threading

import psycopg
import rclpy
from fleet_core.common.contract import Contract
from fleet_interfaces.srv import SubmitTrip
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from service_bridge.core.db import dsn_from_env
from service_bridge.tools.common import (
    call_once,
    load_default_contract,
    parse_items,
    to_order_items,
)
from service_bridge.tools.seed import TEST_LOGIN
from service_bridge.tools.trips import insert_trip, trip_items

# F-04 값: 2초 한도, 1초 간격, 첫 요청 포함 5번
TIMEOUT_SEC = 2.0
GAP_SEC = 1.0
MAX_TRIES = 5


# ---- central에 넘기기 ----
def submit(node: Node, contract: Contract, order_id: str, trip_id: str,
           items: dict[str, int]) -> tuple[bool, str]:
    """SubmitTrip을 부른다(trip_index 1 / trip_count 1). (accepted, reason), 응답이 없으면
    (False, "TIMEOUT"). node는 다른 스레드의 실행기가 돌리고 있어야 한다."""
    client = node.create_client(SubmitTrip, contract.name("submit_trip"))
    try:
        for attempt in range(1, MAX_TRIES + 1):
            request = SubmitTrip.Request(
                trip_id=trip_id, order_id=order_id, trip_index=1, trip_count=1,
                items=to_order_items(items), submitted_at=node.get_clock().now().to_msg())
            # 같은 trip_id로 다시 보내면 central은 처음 응답을 그대로 준다 (F-04)
            response = call_once(client, request, TIMEOUT_SEC)
            if response is not None:
                return response.accepted, response.reason
            node.get_logger().warn(f"SubmitTrip 응답 없음 ({attempt}/{MAX_TRIES}): {trip_id}")
            if attempt < MAX_TRIES:
                threading.Event().wait(GAP_SEC)
        return False, "TIMEOUT"
    finally:
        node.destroy_client(client)


# ---- DB에 넣거나 이미 넣은 건 읽기 ----
def _prepare(opts) -> tuple[str, str, dict[str, int]]:
    with psycopg.connect(dsn_from_env(), autocommit=True) as conn:
        if opts.trip_id:
            order_id, items = trip_items(conn, opts.trip_id)
            return order_id, opts.trip_id, items
        row = conn.execute(
            "SELECT account_id FROM account WHERE login_id = %s", (TEST_LOGIN,)).fetchone()
        if row is None:
            raise SystemExit(f"submit_trip: 시험 계정 {TEST_LOGIN} 없음 (setup_db를 먼저)")
        items = parse_items(opts.items)
        order_id, trip_id = insert_trip(conn, row[0], items)
        return order_id, trip_id, items


# ---- 실행 ----
def main(args=None) -> int:
    parser = argparse.ArgumentParser(description="운반 건 하나를 접수하고 central에 넘긴다")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--items", help="예: mentos:1,mygummy:1")
    group.add_argument("--trip-id", help="DB에 넣지 않고 이 운반 건을 다시 보냄")
    ros_args = rclpy.utilities.remove_ros_args(args if args is not None else sys.argv)
    opts = parser.parse_args(ros_args[1:])
    try:
        # DB 커밋을 먼저 끝낸 뒤 central을 부른다(접수되었는데 DB에 없는 건이 없게)
        order_id, trip_id, items = _prepare(opts)
    except (ValueError, LookupError) as e:
        print(f"submit_trip: 접수 못 함: {e}")
        return 2

    rclpy.init(args=args)
    node = rclpy.create_node("submit_trip")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    # call_once는 실행기 밖 스레드에서 기다려야 하므로 실행기는 따로 돌린다
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    try:
        accepted, reason = submit(node, load_default_contract(), order_id, trip_id, items)
    finally:
        executor.shutdown(timeout_sec=2.0)
        spinner.join(timeout=3)
        node.destroy_node()
        rclpy.try_shutdown()
    print(f"submit_trip {trip_id} {items}: accepted={accepted} reason={reason!r}")
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
