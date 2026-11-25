"""ROS 요청 → core 자료형 변환."""
import pytest

pytest.importorskip("fleet_interfaces")

from fleet_interfaces.msg import OrderItem  # noqa: E402
from fleet_interfaces.srv import ReportTrip  # noqa: E402
from service_bridge.ros import convert  # noqa: E402


def test_report_in_sums_same_kind():
    req = ReportTrip.Request(trip_id="t", report_epoch=7, report_n=1, status="FILLING",
                             placed=[OrderItem(kind="A", count=1), OrderItem(kind="A", count=1)])
    assert convert.report_in(req).placed == {"A": 2}
