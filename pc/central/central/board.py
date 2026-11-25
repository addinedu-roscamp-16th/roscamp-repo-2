"""간이 central의 주문판 상태. ROS에 의존하지 않는다 (설계 7절)."""
from __future__ import annotations

from dataclasses import dataclass

INVALID_REQUEST = "INVALID_REQUEST"
FINAL_STATUSES = frozenset({"SHIPPED", "PARTIAL", "CANCELED", "HELD"})


@dataclass(frozen=True)
class BoardEntry:
    trip_id: str
    items: dict
    created_at: object


class Board:
    def __init__(self):
        self._entries: list[BoardEntry] = []
        self._answers: dict[str, tuple[bool, str]] = {}  # trip_id -> 처음의 확정 응답
        self._finished: dict[str, str] = {}  # 주문판에서 뺀 trip_id -> 끝 상태

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
        """끝 상태면 True(이미 뺐거나 모르는 건도 같은 결과), 그 밖의 상태면 False."""
        if status not in FINAL_STATUSES:
            return False
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
