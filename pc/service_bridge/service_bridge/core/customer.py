# ---- 고객 화면에 보이는 주문 상태 말 (설계 5.5) ----
STATUS_TEXT: dict[str, str] = {
    "RECEIVED": "접수됨",
    "ASSIGNED": "준비 중",
    "FILLING": "준비 중",
    "FILLING_PAUSE": "준비 중",
    "HELD": "준비 중",
    "TRANSPORTING": "출고 중",
    "SHIPPED": "출고 완료",
    "PARTIAL": "일부 출고",
    "CANCELLED": "취소됨",
}


def status_text(status: str) -> str:
    return STATUS_TEXT[status]
