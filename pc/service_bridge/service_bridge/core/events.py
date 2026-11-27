# ---- ws_event 읽기와 보낼 곳 나누기 (설계 7.4, ROS 의존 없음) ----
from dataclasses import dataclass
from typing import Literal

import psycopg

from service_bridge.core.customer import status_text


@dataclass(frozen=True)
class Outgoing:
    audience: Literal["admin", "customer"]
    account_id: int | None
    message: dict


def read_event(conn: psycopg.Connection, seq: int) -> tuple[str, dict] | None:
    row = conn.execute("SELECT type, data FROM ws_event WHERE seq = %s", (seq,)).fetchone()
    return (row[0], row[1]) if row else None


# ---- 겉모양 {type, seq(사건만), ts, data} ----
def envelope(type: str, data: dict, ts: str, seq: int | None = None) -> dict:
    msg = {"type": type, "ts": ts, "data": data}
    if seq is not None:
        msg["seq"] = seq
    return msg


def route(event_type: str, seq: int, data: dict, ts: str) -> list[Outgoing]:
    if event_type == "order_changed":
        admin = {"order_id": data["order_id"], "status": data["status"]}
        cust = {"order_id": data["order_id"], "status_text": status_text(data["status"])}
        return [Outgoing("admin", None, envelope(event_type, admin, ts, seq)),
                Outgoing("customer", data["account_id"], envelope(event_type, cust, ts, seq))]
    # command_result는 관리자 화면 전용: 로봇 · 명령 정보라 고객에게 보내지 않는다
    if event_type in ("trip_changed", "command_result"):
        return [Outgoing("admin", None, envelope(event_type, data, ts, seq))]
    return []
