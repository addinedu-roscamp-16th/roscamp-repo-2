# ---- fleet ReportCommand(관리 명령 최종 결과)를 한 트랜잭션으로 DB에 반영 (ROS 의존 없음) ----
import dataclasses
import logging

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from service_bridge.core.retry import run_tx_guarded
from service_bridge.core.types import CommandIn, CommandOut

log = logging.getLogger(__name__)

# 끝 상태: 한 번 쓰면 다른 끝 상태로 바꾸지 않는다.
# 근거: IR-62, 주문 DB 설계 4.9, DB CHECK ck_admin_action_final_result
FINAL = frozenset({"ACCEPTED", "REJECTED", "TIMEOUT"})
# DB CHECK ck_admin_action_final_result와 같은 4개. 밖의 값은 쓰기 전에 걸러야 CHECK 오류(=DB_ERROR 재전송 반복)가 안 난다
RESULTS = FINAL | {"UNCONFIRMED"}


# ---- 서버 콜백이 부르는 입구 ----
def apply_command(pool: ConnectionPool, rep: CommandIn, deadline: float) -> CommandOut:
    """결과 하나를 반영하고 커밋한 뒤 응답 값을 돌려준다. DB 오류는 accepted=false, DB_ERROR.

    반영 · 중복 · 늦은 결과는 모두 accepted=true(fleet이 다시 보내지 않게).
    accepted=false는 DB 오류뿐이고, fleet이 같은 결과를 다시 보낸다.
    """
    def on_error(e: Exception) -> CommandOut:
        log.error("명령 결과 반영 실패 (DB 오류): %s %s %s: %s",
                  rep.request_id, rep.command, rep.result, e)
        return CommandOut(False, reason="DB_ERROR")

    return run_tx_guarded(pool, lambda conn: _apply(conn, rep), deadline, on_error)


# ---- 판정과 반영 (한 트랜잭션, 설계 4.2 표) ----
def _apply(conn: psycopg.Connection, rep: CommandIn) -> CommandOut:
    # 설계 4.2 표 8번 줄: 행을 읽기 전이라 알림 robot_id는 보고의 target밖에 모른다
    if rep.result not in RESULTS:
        return _late(conn, rep, rep.target or None, "INVALID_RESULT")

    # 표 7번 줄. request_id는 UNIQUE text라 ''인 행이 있을 수 있어, 조회에 맡기지 않고 먼저 막는다
    if not rep.request_id:
        return _late(conn, rep, rep.target or None, "NOT_FOUND")

    # FOR UPDATE로 같은 request_id 보고가 차례로 처리돼 IR-62 "한 번만 확정"이 지켜진다
    row = conn.execute(
        "SELECT action_id, robot_id, final_result FROM admin_action "
        "WHERE request_id = %s FOR UPDATE", (rep.request_id,)).fetchone()
    if row is None:
        return _late(conn, rep, rep.target or None, "NOT_FOUND")
    action_id, row_robot, cur = row

    # 표 1번 줄(NULL) · 2번 줄(UNCONFIRMED 뒤 끝 상태): 확정은 여기서 한 번뿐 (IR-62)
    if cur is None or (cur == "UNCONFIRMED" and rep.result in FINAL):
        # reason이 비면 접수 응답 때 쓴 result_detail(거절 사유 등)을 지우지 않는다
        conn.execute(
            "UPDATE admin_action SET final_result = %s, "
            "result_detail = CASE WHEN %s <> '' THEN %s ELSE result_detail END "
            "WHERE action_id = %s", (rep.result, rep.reason, rep.reason, action_id))
        # 화면은 이 사건으로 갱신한다. final_result를 쓴 경우에만 낸다
        conn.execute(
            "INSERT INTO ws_event (type, data) VALUES ('command_result', %s)",
            (Jsonb({"request_id": rep.request_id, "command": rep.command, "target": rep.target,
                    "result": rep.result, "reason": rep.reason, "detail": rep.detail}),))
        # TIMEOUT 알림은 fleet 몫(W-31)이라 여기서 내지 않음
        return CommandOut(True, "APPLIED")

    # 표 3번 줄(UNCONFIRMED 재전송) · 4번 줄(끝 상태 뒤 늦은 UNCONFIRMED) · 5번 줄(같은 끝 상태 재전송)
    if rep.result == cur or (rep.result == "UNCONFIRMED" and cur in FINAL):
        return CommandOut(True, "DUPLICATE")

    # 표 6번 줄: 이미 끝 상태인데 다른 끝 상태가 오면 덮어쓰지 않고 알림 (ck_admin_action_final_result 이후 값 불변)
    return _late(conn, rep, row_robot or rep.target or None, "FINAL_ALREADY")


# ---- 늦은 결과 알림 ----
def _late(conn: psycopg.Connection, rep: CommandIn, robot_id: str | None,
          why: str) -> CommandOut:
    """LATE_COMMAND_RESULT 알림을 연다. admin_action은 그대로.

    why는 INVALID_RESULT · NOT_FOUND · FINAL_ALREADY. 같은 결과가 다시 와도 알림이 하나이도록
    event_id "LATECMD:{request_id}:{result}"로 ON CONFLICT 한다.
    alert.robot_id에는 FK가 없어 행이 없는 target도 그대로 넣을 수 있다.
    """
    detail = {**dataclasses.asdict(rep), "late_reason": why}
    log.warning("늦은 명령 결과 %s: %s %s %s", why, rep.request_id, rep.command, rep.result)
    conn.execute(
        "INSERT INTO alert (level, code, event_id, source, robot_id, message, detail) "
        "VALUES ('WARN', 'LATE_COMMAND_RESULT', %s, 'fleet', %s, %s, %s) "
        "ON CONFLICT (event_id) DO NOTHING",
        (f"LATECMD:{rep.request_id}:{rep.result}", robot_id,
         f"반영하지 않은 명령 결과 ({why}): {rep.request_id} {rep.result}", Jsonb(detail)))
    return CommandOut(True, "LATE", why)
