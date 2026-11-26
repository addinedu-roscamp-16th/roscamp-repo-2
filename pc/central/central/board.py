# ---- 간이 central의 메모리 주문판 (ROS 의존 없음, 재시작하면 비어 있음) ----
from __future__ import annotations

from dataclasses import dataclass

INVALID_REQUEST = "INVALID_REQUEST"
# ReportOrder 끝 상태(주문판에서 뺌). ReportOrder 철자는 CANCELED(ReportTrip · DB는 CANCELLED)
FINAL_STATUSES = frozenset({"SHIPPED", "PARTIAL", "CANCELED"})
# 끝 상태가 아니다: 주문판에 남기고 관리자 조치 대기로 표시한다
HELD = "HELD"


@dataclass(frozen=True)
class BoardEntry:
    trip_id: str
    items: dict
    created_at: object


# ---- 접수 · 빼기 · 조회 ----
class Board:
    def __init__(self):
        self._entries: list[BoardEntry] = []
        # trip_id -> 처음의 확정 응답(접수 · 거절). 같은 trip_id가 다시 오면 그대로 준다 (SB-06).
        # NOT_READY는 기억하지 않는다(이번 단계에는 내는 경우가 없음)
        self._answers: dict[str, tuple[bool, str]] = {}
        self._finished: dict[str, str] = {}  # 주문판에서 뺀 trip_id -> 끝 상태
        self._held: set[str] = set()  # 관리자 조치 대기 중인 trip_id

    def submit(self, trip_id: str, items: dict, created_at: object) -> tuple[bool, str]:
        if trip_id in self._answers:
            return self._answers[trip_id]
        if not trip_id or not items or any(count < 1 for count in items.values()):
            answer = (False, INVALID_REQUEST)
        else:
            self._entries.append(BoardEntry(trip_id, dict(items), created_at))
            answer = (True, "")
        self._answers[trip_id] = answer
        return answer

    def report(self, order_id: str, status: str) -> bool:
        """끝 상태 · HELD면 True(이미 뺐거나 모르는 건도 같은 결과), 그 밖의 상태면 False.

        HELD는 주문판에 남기고 표시만 한다(없는 건이면 표시하지 않음).
        끝 상태가 오면 표시도 지우고 주문판에서 뺀다."""
        if status == HELD:
            if self.on_board(order_id):
                self._held.add(order_id)
            return True
        if status not in FINAL_STATUSES:
            return False
        self._held.discard(order_id)
        kept = [e for e in self._entries if e.trip_id != order_id]
        if len(kept) != len(self._entries):
            self._entries = kept
            self._finished[order_id] = status
        return True

    def entries(self) -> list[BoardEntry]:
        return list(self._entries)

    def knows(self, order_id: str) -> bool:
        """주문판에 있거나 이미 끝난 건인지(모르는 order_id 경고용)."""
        return order_id in self._finished or any(e.trip_id == order_id for e in self._entries)

    def on_board(self, order_id: str) -> bool:
        """지금 주문판에 있는 건인지(끝나서 뺀 건은 거짓). HELD 표시는 이 건에만 한다."""
        return any(e.trip_id == order_id for e in self._entries)

    def held(self) -> set[str]:
        """관리자 조치 대기 중인 trip_id."""
        return set(self._held)
