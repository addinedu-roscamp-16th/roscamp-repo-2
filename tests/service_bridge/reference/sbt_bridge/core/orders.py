"""REST 주문 접수의 멱등 처리 (Idempotency-Key).

- 처음 보는 키: 주문 ID를 새로 만들고, orders(키·정규화한 본문·응답)와
  outbox(PENDING)를 한 트랜잭션으로 저장 → 201
- 같은 키 + 같은 본문: 처음 응답을 그대로 돌려줌 (주문을 새로 만들지 않음)
- 같은 키 + 다른 본문: IdempotencyConflict → 웹 계층이 409
본문은 키 순서·공백을 정규화해 비교하므로 {"a":1,"b":2}와 {"b":2, "a":1}은 같은 본문입니다.
"""
import json
import uuid


class IdempotencyConflict(Exception):
    """같은 Idempotency-Key에 다른 본문이 들어온 경우."""


def _norm(payload: dict) -> str:
    """비교·저장용 정규화 JSON (키 정렬, 공백 없음)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class OrderDesk:
    """POST /orders 처리기. accept()는 (HTTP 상태 코드, 응답 본문)을 돌려줍니다."""

    def __init__(self, db, outbox):
        self.db = db
        self.outbox = outbox

    def accept(self, idempotency_key: str, payload: dict) -> tuple:
        norm = _norm(payload)
        with self.db.lock:
            row = self.db.execute(
                "SELECT payload_norm, status_code, body_json FROM orders WHERE idem_key = ?",
                (idempotency_key,)).fetchone()
            if row is not None:
                if row[0] != norm:
                    raise IdempotencyConflict(idempotency_key)
                return row[1], json.loads(row[2])
            order_id = str(uuid.uuid4())
            body = {"order_id": order_id, "state": "PENDING"}
            self.db.execute(
                "INSERT INTO orders(idem_key, order_id, payload_norm, status_code, body_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (idempotency_key, order_id, norm, 201, json.dumps(body)))
            self.db.execute("INSERT INTO outbox(order_id, state, payload) VALUES (?, 'PENDING', ?)",
                            (order_id, norm))
            self.db.commit()
            return 201, body
