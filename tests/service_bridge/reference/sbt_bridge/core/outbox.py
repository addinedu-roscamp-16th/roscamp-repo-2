"""주문 outbox와 상태 전이. 상태 변경은 Outbox.transition 한 곳에서만 합니다.

상태 흐름
    PENDING ─제출→ SENT ─submit 응답 accepted→ ACCEPTED ─완료 이벤트·조회 DONE→ DONE
                        ├─submit 거절→ REJECTED
                        └─완료 이벤트(응답보다 먼저 옴)→ DONE
- 허용되지 않은 전이는 InvalidTransition. 브리지(bridge_node._advance)는 전이 전에 ALLOWED로 걸러
  늦게 온 응답·이벤트를 무시하고 probe에 state_ignored로 남깁니다.
- UT-01은 패키지 전체에서 outbox 상태를 바꾸는 SQL이 이 파일에만 있는지 검사합니다.
  다른 파일의 주석·문자열에도 그 SQL 문구를 쓰지 마십시오.
"""
from enum import Enum


class OrderState(str, Enum):
    """outbox 행의 state 열 값. str을 상속해 DB·JSON에 그대로 씁니다."""
    PENDING = "PENDING"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    DONE = "DONE"
    REJECTED = "REJECTED"


S = OrderState
# 상태별로 갈 수 있는 다음 상태. DONE·REJECTED는 끝 상태입니다.
ALLOWED = {
    S.PENDING: frozenset({S.SENT}),
    S.SENT: frozenset({S.ACCEPTED, S.REJECTED, S.DONE}),
    S.ACCEPTED: frozenset({S.DONE}),
    S.DONE: frozenset(),
    S.REJECTED: frozenset(),
}


class InvalidTransition(Exception):
    """ALLOWED에 없는 전이를 시도함 (호출자 결함)."""


class Outbox:
    """주문을 먼저 DB(outbox)에 남기고, 브리지가 그 기록을 보고 FMS에 제출·복구합니다."""

    def __init__(self, db):
        self.db = db

    def add(self, order_id: str, payload: str) -> None:
        with self.db.lock:
            self.db.execute("INSERT INTO outbox(order_id, state, payload) VALUES (?, ?, ?)",
                            (order_id, S.PENDING.value, payload))
            self.db.commit()

    def state(self, order_id: str) -> OrderState:
        row = self.db.execute("SELECT state FROM outbox WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(order_id)
        return OrderState(row[0])

    def payload(self, order_id: str) -> str:
        row = self.db.execute(
            "SELECT payload FROM outbox WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            raise KeyError(order_id)
        return row[0]

    def ids_in(self, state: OrderState) -> list:
        """해당 상태의 주문 ID를 넣은 순서대로 (outbox 타이머가 PENDING을 제출할 때 사용)."""
        rows = self.db.execute("SELECT order_id FROM outbox WHERE state = ? ORDER BY rowid",
                               (OrderState(state).value,)).fetchall()
        return [r[0] for r in rows]

    def transition(self, order_id: str, to: OrderState) -> OrderState:
        """상태를 바꾸고 이전 상태를 돌려줍니다.

        읽기·검사·쓰기를 DB 락 하나로 묶어 경합을 막습니다.
        """
        to = OrderState(to)
        with self.db.lock:
            prev = self.state(order_id)
            if to not in ALLOWED[prev]:
                raise InvalidTransition(f"{order_id}: {prev.value} -> {to.value}")
            self.db.execute("UPDATE outbox SET state = ? WHERE order_id = ?", (to.value, order_id))
            self.db.commit()
            return prev
