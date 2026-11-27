"""apply_command (ReportCommand 수신, 설계 4장) 시험: 실제 PostgreSQL 시험 DB."""
import time

import pytest
from service_bridge.core.commands import apply_command
from service_bridge.core.db import make_pool
from service_bridge.core.types import CommandIn, CommandOut


def D() -> float:
    return time.monotonic() + 2.5


def cmd(result, request_id="R-1", reason="", **kw):
    base = dict(request_id=request_id, command="LOCK_RELEASE", target="pode1",
                result=result, reason=reason, detail="")
    base.update(kw)
    return CommandIn(**base)


@pytest.fixture
def row(conn, seeded):
    """접수 응답까지 끝나 final_result가 아직 NULL인 관리 명령 행."""
    def make(result_detail=None):
        conn.execute(
            "INSERT INTO admin_action (account_id, action, robot_id, request_id, result, "
            "result_detail) VALUES (%s, 'LOCK_RELEASE', 'pode1', 'R-1', 'SENT', %s)",
            (seeded["account_id"], result_detail))
    return make


def final_of(conn, rid="R-1"):
    return conn.execute(
        "select final_result, result_detail from admin_action where request_id=%s",
        (rid,)).fetchone()


def n_events(conn):
    return conn.execute("select count(*) from ws_event where type='command_result'").fetchone()[0]


def alerts(conn):
    return conn.execute(
        "select event_id, robot_id, detail->>'late_reason' from alert "
        "where code='LATE_COMMAND_RESULT'").fetchall()


def test_first_result_is_written_with_its_event(pool, conn, row):
    """처음 온 끝 상태 결과가 final_result에 쓰이고 사건 data가 여섯 칸 그대로 나간다."""
    row()
    assert apply_command(pool, cmd("ACCEPTED", detail="ok"), D()) == CommandOut(True, "APPLIED")
    assert final_of(conn) == ("ACCEPTED", None)
    data = conn.execute("select data from ws_event where type='command_result'").fetchall()
    assert [d[0] for d in data] == [{"request_id": "R-1", "command": "LOCK_RELEASE",
                                     "target": "pode1", "result": "ACCEPTED", "reason": "",
                                     "detail": "ok"}]


def test_unconfirmed_then_final_is_written(pool, conn, row):
    """결과 모름(UNCONFIRMED) 뒤 온 끝 상태가 덮어쓰고 사유가 result_detail에 남는다."""
    row()
    assert apply_command(pool, cmd("UNCONFIRMED", reason="ROBOT_TIMEOUT"), D()) == \
        CommandOut(True, "APPLIED")
    assert apply_command(pool, cmd("REJECTED", reason="ROBOT_REJECTED"), D()) == \
        CommandOut(True, "APPLIED")
    assert final_of(conn) == ("REJECTED", "ROBOT_REJECTED")
    assert n_events(conn) == 2


def test_same_result_again_is_duplicate(pool, conn, row):
    """같은 결과 재전송은 사건도 알림도 만들지 않는다."""
    row()
    apply_command(pool, cmd("ACCEPTED"), D())
    assert apply_command(pool, cmd("ACCEPTED"), D()) == CommandOut(True, "DUPLICATE")
    assert n_events(conn) == 1
    assert alerts(conn) == []


def test_unconfirmed_after_final_is_duplicate(pool, conn, row):
    """끝 상태 뒤에 늦게 온 UNCONFIRMED가 끝 상태를 되돌리지 못한다."""
    row()
    apply_command(pool, cmd("TIMEOUT"), D())
    assert apply_command(pool, cmd("UNCONFIRMED"), D()) == CommandOut(True, "DUPLICATE")
    assert final_of(conn)[0] == "TIMEOUT"


def test_other_final_after_final_alerts_once(pool, conn, row):
    """이미 확정된 뒤 다른 끝 상태는 덮어쓰지 않고, 같은 보고가 다시 와도 알림은 하나."""
    row()
    apply_command(pool, cmd("ACCEPTED"), D())
    for _ in range(2):
        assert apply_command(pool, cmd("REJECTED"), D()) == \
            CommandOut(True, "LATE", "FINAL_ALREADY")
    assert final_of(conn)[0] == "ACCEPTED"
    assert alerts(conn) == [("LATECMD:R-1:REJECTED", "pode1", "FINAL_ALREADY")]


def test_unknown_request_id_alerts_once(pool, conn, row):
    """모르는 request_id는 반영하지 않고 알림 하나, 알림 robot_id는 보고의 target."""
    row()
    for _ in range(2):
        assert apply_command(pool, cmd("ACCEPTED", request_id="R-NONE", target="arm_left"),
                             D()) == CommandOut(True, "LATE", "NOT_FOUND")
    assert alerts(conn) == [("LATECMD:R-NONE:ACCEPTED", "arm_left", "NOT_FOUND")]


def test_empty_request_id_is_not_found(pool, conn, row):
    """빈 request_id가 request_id NULL인 행에 걸려 쓰이지 않는다."""
    row()
    conn.execute("update admin_action set request_id = NULL")
    assert apply_command(pool, cmd("ACCEPTED", request_id=""), D()) == \
        CommandOut(True, "LATE", "NOT_FOUND")
    assert conn.execute("select final_result from admin_action").fetchone()[0] is None


def test_result_outside_the_four_alerts(pool, conn, row):
    """네 값 밖의 result는 DB CHECK에 닿기 전에 걸러져 알림이 되고 행은 그대로다."""
    row()
    assert apply_command(pool, cmd("DONE"), D()) == CommandOut(True, "LATE", "INVALID_RESULT")
    assert final_of(conn)[0] is None
    assert alerts(conn)[0][2] == "INVALID_RESULT"


def test_empty_reason_keeps_result_detail(pool, conn, row):
    """끝 상태 보고의 빈 reason이 접수 응답 때 쓴 거절 사유를 지우지 않는다."""
    row("NOT_STOPPED")
    assert apply_command(pool, cmd("ACCEPTED", reason=""), D()) == CommandOut(True, "APPLIED")
    assert final_of(conn) == ("ACCEPTED", "NOT_STOPPED")


def test_timeout_raises_no_alert(pool, conn, row):
    """TIMEOUT 알림은 fleet 몫이라 브리지는 받아 적기만 한다."""
    row()
    assert apply_command(pool, cmd("TIMEOUT"), D()) == CommandOut(True, "APPLIED")
    assert alerts(conn) == []


def test_db_error_is_not_accepted():
    """닿지 않는 DB면 accepted=false, DB_ERROR (fleet이 다시 보낸다)."""
    p = make_pool("postgresql://postgres:x@localhost:55999/none", open=True)
    try:
        start = time.monotonic()
        assert apply_command(p, cmd("ACCEPTED"), D()) == CommandOut(False, reason="DB_ERROR")
        assert time.monotonic() - start < 2.5
    finally:
        p.close()
