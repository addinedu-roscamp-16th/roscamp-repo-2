"""운반 건 허용 전이 표(SB-03)와 주문 상태 요약 규칙. DB 없이 동작하는 순수 함수."""

STATUSES = frozenset({
    "RECEIVED", "ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING",
    "SHIPPED", "PARTIAL", "HELD", "CANCELLED",
})
ACTIVE = frozenset({"ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING"})
TERMINAL = frozenset({"SHIPPED", "PARTIAL", "CANCELLED"})

# status_history CHECK(ck_status_history_reason)와 같은 13개
TRIP_REASONS = frozenset({
    "OUT_OF_STOCK", "PICK_FAILED", "RECOGNITION_ERROR", "ROBOT_FAULT", "ARM_FAULT",
    "FLEET_RESTART", "USER_CANCEL", "NOTHING_TO_FILL", "STOCK_RECHECK", "FORCED_SHIP",
    "FORCED_PARTIAL", "REASSIGN", "RETURNED_TO_SHELF",
})

# 주문 DB 설계 5장 "허용 전이" 표 (같은 상태는 is_allowed가 따로 허용)
_ALLOWED = {
    "RECEIVED": frozenset({"ASSIGNED", "FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED",
                           "PARTIAL", "HELD", "CANCELLED"}),
    "ASSIGNED": frozenset({"FILLING", "FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL",
                           "HELD", "CANCELLED", "RECEIVED"}),
    "FILLING": frozenset({"FILLING_PAUSE", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD",
                          "CANCELLED", "RECEIVED"}),
    "FILLING_PAUSE": frozenset({"FILLING", "TRANSPORTING", "SHIPPED", "PARTIAL", "HELD",
                                "CANCELLED", "RECEIVED"}),
    "TRANSPORTING": frozenset({"SHIPPED", "PARTIAL", "HELD"}),
    "HELD": frozenset({"SHIPPED", "PARTIAL", "RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING"}),
    "SHIPPED": frozenset(),
    "PARTIAL": frozenset(),
    "CANCELLED": frozenset(),
}

# 규칙 4의 단계 순서: 배정됨 < 담는 중 = 담기 일시 중지 < 운반 중
_STAGE = {"ASSIGNED": 0, "FILLING": 1, "FILLING_PAUSE": 1, "TRANSPORTING": 2}


def is_allowed(current: str, new: str) -> bool:
    """번호가 더 큰 보고의 상태 `new`를 `current`에서 반영해도 되는지. 같은 상태는 언제나 True."""
    return current == new or new in _ALLOWED[current]


def order_summary(trip_statuses: list[str]) -> str:
    """운반 건 상태들로 주문 상태를 계산한다 (주문 DB 설계 4.4 규칙 1 ~ 4). 입력은 비어 있지 않다."""
    statuses = set(trip_statuses)
    # 규칙 1: 모두 끝남
    if statuses <= TERMINAL:
        if statuses == {"CANCELLED"}:
            return "CANCELLED"
        if statuses == {"SHIPPED"}:
            return "SHIPPED"
        return "PARTIAL"
    # 규칙 2: 모두 접수됨
    if statuses == {"RECEIVED"}:
        return "RECEIVED"
    # 규칙 3: 끝나지 않은 건 중 보류
    if "HELD" in statuses:
        return "HELD"
    # 규칙 4: 시작한 주문의 남은 접수됨은 배정됨으로 센다 (여기 오면 접수됨 아닌 건이 있다)
    stages = {_STAGE[s] if s in _STAGE else 0 for s in statuses - TERMINAL}
    behind = min(stages)  # '가장 늦은' = 가장 덜 진행된 단계 (모두 운반 중일 때만 운반 중)
    if behind == 1:
        return "FILLING_PAUSE" if "FILLING_PAUSE" in statuses else "FILLING"
    return "TRANSPORTING" if behind == 2 else "ASSIGNED"
