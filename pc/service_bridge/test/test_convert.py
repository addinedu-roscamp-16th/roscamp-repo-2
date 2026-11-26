"""ROS 요청 → core 자료형 변환."""
import pytest

pytest.importorskip("fleet_interfaces")

from builtin_interfaces.msg import Time  # noqa: E402
from fleet_interfaces.msg import OrderItem, RobotStatus, RobotStatusArray  # noqa: E402
from fleet_interfaces.srv import ReportTrip  # noqa: E402
from service_bridge.ros import convert  # noqa: E402


def test_report_in_sums_same_kind():
    req = ReportTrip.Request(trip_id="t", report_epoch=7, report_n=1, status="FILLING",
                             placed=[OrderItem(kind="mentos", count=1), OrderItem(kind="mentos", count=1)])
    assert convert.report_in(req).placed == {"mentos": 2}


def test_robots_renames_order_id():
    msg = RobotStatusArray(robots=[RobotStatus(
        robot_id="pode1", fsm_state="MOVING", fault="NONE", order_id="T-1",
        battery=50.0, x=1.5, y=2.5)])
    out = convert.robots(msg)
    assert out == [{"robot_id": "pode1", "fsm_state": "MOVING", "fault": "NONE",
                    "battery": 50.0, "x": 1.5, "y": 2.5, "trip_id": "T-1"}]


def test_report_in_carries_stamp():
    req = ReportTrip.Request(trip_id="t", stamp=Time(sec=1764000000, nanosec=500_000_000))
    assert convert.report_in(req).stamp == "2025-11-24T16:00:00.500000+00:00"
    assert convert.report_in(ReportTrip.Request(stamp=Time(sec=1764000000))).stamp == \
        "2025-11-24T16:00:00+00:00"
