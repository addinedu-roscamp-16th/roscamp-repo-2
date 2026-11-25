# ---- 서비스 요청 · 응답 값 (core가 ROS 메시지를 모르게 둔 순수 자료형) ----
from dataclasses import dataclass


# ---- ReportTrip ----
@dataclass(frozen=True)
class ReportIn:
    trip_id: str
    epoch: int
    n: int
    status: str
    reason: str
    robot_id: str
    placed: dict[str, int]   # kind -> 지금까지 담은 수 전체(IR-23)
    missing: dict[str, int]  # kind -> 지금까지 못 담은 수 전체


@dataclass(frozen=True)
class ReportOut:
    accepted: bool    # false는 DB 오류뿐. fleet은 같은 번호로 다시 보낸다
    result: str = ""  # APPLIED · DUPLICATE · LATE_REPORT
    reason: str = ""  # LATE_REPORT 까닭 또는 DB_ERROR


# ---- Hello ----
@dataclass(frozen=True)
class HelloIn:
    node: str
    boot_id: str


@dataclass(frozen=True)
class HelloOut:
    accepted: bool
    epoch: int = 0
    reason: str = ""
