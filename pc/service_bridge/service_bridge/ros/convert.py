# ---- ROS 메시지 ↔ core 자료형 변환 ----
from fleet_interfaces.msg import OrderItem
from fleet_interfaces.srv import Hello, ReportTrip

from service_bridge.core.types import HelloIn, ReportIn


def items(items: list[OrderItem]) -> dict[str, int]:
    """같은 kind는 합친다. 값 검사(음수 · 모르는 kind)는 apply_report가 한다."""
    out: dict[str, int] = {}
    for item in items:
        out[item.kind] = out.get(item.kind, 0) + item.count
    return out


def report_in(req: ReportTrip.Request) -> ReportIn:
    return ReportIn(
        trip_id=req.trip_id,
        epoch=int(req.report_epoch),
        n=int(req.report_n),
        status=req.status,
        reason=req.reason,
        robot_id=req.robot_id,
        placed=items(req.placed),
        missing=items(req.missing),
    )


def hello_in(req: Hello.Request) -> HelloIn:
    return HelloIn(node=req.node, boot_id=req.boot_id)
