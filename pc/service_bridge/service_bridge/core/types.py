"""서비스 요청 · 응답 값 (ROS 메시지와 분리한 순수 자료형)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReportIn:
    trip_id: str
    epoch: int
    n: int
    status: str
    reason: str
    robot_id: str
    placed: dict[str, int]
    missing: dict[str, int]


@dataclass(frozen=True)
class ReportOut:
    accepted: bool
    result: str = ""
    reason: str = ""


@dataclass(frozen=True)
class HelloIn:
    node: str
    boot_id: str


@dataclass(frozen=True)
class HelloOut:
    accepted: bool
    epoch: int = 0
    reason: str = ""
