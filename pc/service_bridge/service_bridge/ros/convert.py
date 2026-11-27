# ---- ROS 메시지 ↔ core 자료형 변환 ----
from datetime import datetime, timezone

from builtin_interfaces.msg import Time
from fleet_interfaces.msg import OrderItem, RobotStatusArray
from fleet_interfaces.srv import Hello, ReportCommand, ReportTrip, SubmitTrip

from service_bridge.core.dispatch import DispatchItem
from service_bridge.core.types import CommandIn, HelloIn, ReportIn


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
        stamp=iso(req.stamp),
    )


def command_in(req: ReportCommand.Request) -> CommandIn:
    # 칸 이름과 값을 그대로 옮긴다. 값 검사는 하지 않음: ReportCommand 값은 DB final_result와
    # 같은 집합이고(ReportCommand.srv), 낯선 값은 core.commands가 판정한다
    return CommandIn(
        request_id=req.request_id,
        command=req.command,
        target=req.target,
        result=req.result,
        reason=req.reason,
        detail=req.detail,
        stamp=iso(req.stamp),
    )


def hello_in(req: Hello.Request) -> HelloIn:
    return HelloIn(node=req.node, boot_id=req.boot_id)


# ---- 시각 ----
def iso(t: Time) -> str:
    """ROS 시각 → UTC ISO 문자열 (마이크로초까지)."""
    return datetime.fromtimestamp(t.sec, tz=timezone.utc).replace(
        microsecond=t.nanosec // 1000).isoformat()


def to_time(dt: datetime) -> Time:
    dt = dt.astimezone(timezone.utc)
    return Time(sec=int(dt.replace(microsecond=0).timestamp()), nanosec=dt.microsecond * 1000)


# ---- 운반 건 넘기기 ----
def submit_request(item: DispatchItem) -> SubmitTrip.Request:
    return SubmitTrip.Request(
        trip_id=item.trip_id,
        order_id=item.order_id,
        trip_index=item.trip_index,
        trip_count=item.trip_count,
        items=[OrderItem(kind=k, count=n) for k, n in item.items.items()],
        submitted_at=to_time(item.submitted_at),
    )


# ---- 로봇 상태 (웹 화면용). order_id는 웹에서 trip_id라 부른다 ----
def robots(msg: RobotStatusArray) -> list[dict]:
    return [
        {"robot_id": r.robot_id, "fsm_state": r.fsm_state, "fault": r.fault,
         "battery": float(r.battery), "x": float(r.x), "y": float(r.y), "trip_id": r.order_id}
        for r in msg.robots
    ]
