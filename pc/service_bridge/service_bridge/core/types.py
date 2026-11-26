# ---- 서비스 요청 · 응답 값 (core가 ROS 메시지를 모르게 둔 순수 자료형) ----
from dataclasses import dataclass, field


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


# ---- 주문 접수 (POST /api/orders) ----
@dataclass(frozen=True)
class OrderLine:
    product_id: int
    qty: int


@dataclass(frozen=True)
class OrderRequest:
    account_id: int
    items: tuple[OrderLine, ...]


@dataclass(frozen=True)
class TripPlan:
    seq: int                      # 1부터
    items: tuple[OrderLine, ...]


@dataclass(frozen=True)
class PlaceOk:
    order_id: str
    trip_ids: tuple[str, ...]
    replayed: bool = False        # 같은 키 · 같은 본문으로 다시 온 요청


@dataclass(frozen=True)
class PlaceError:
    status: int                   # HTTP 상태 코드
    code: str
    message: str
    detail: dict = field(default_factory=dict)
