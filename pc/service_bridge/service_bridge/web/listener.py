# ---- LISTEN 전용 연결: 사건 순번을 받아 넘기고, 끊기면 다시 붙는다 (설계 7.2) ----
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import psycopg

log = logging.getLogger("service_bridge.web.listener")

CHANNEL = "ifp_events"          # design/db/order_db_extra.sql fn_ws_event_notify
BACKOFF = (1.0, 2.0, 5.0)       # 다시 붙기 간격(초). 마지막 값을 계속 쓴다
POLL = 0.5                      # 멈춤 신호를 확인하는 간격(초)


async def run_listener(dsn: str,
                       on_seq: Callable[[int], Awaitable[None]],
                       on_reconnect: Callable[[], Awaitable[None]],
                       stop: asyncio.Event,
                       on_connect: Callable[[int], None] | None = None) -> None:
    """stop이 설 때까지 LISTEN한다. on_connect(backend_pid)는 붙을 때마다 부른다(시험 · 로그용).

    처음 붙기가 곧바로 된 경우만 빼고, 붙은 뒤 on_reconnect()를 부른다(끊긴 동안 사건은 스냅샷이 대신함)."""
    failures = 0
    resync = False
    while not stop.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                await conn.execute(f"LISTEN {CHANNEL}")
                pid = conn.info.backend_pid
                log.info("LISTEN %s 붙음 (backend pid %d)", CHANNEL, pid)
                failures = 0
                if on_connect is not None:
                    on_connect(pid)
                if resync:
                    await _safe(on_reconnect, "스냅샷 다시 보내기")
                resync = True
                while not stop.is_set():
                    async for n in conn.notifies(timeout=POLL):
                        await _handle(n.payload, on_seq)
                        if stop.is_set():
                            break
                    # notifies는 POLL마다 끝나므로 연결이 살았는지 확인한다
                    if conn.closed or conn.broken:
                        raise psycopg.OperationalError("LISTEN 연결 끊김")
        except Exception as e:  # 연결 · 읽기 오류는 모두 다시 붙기로
            resync = True
            if stop.is_set():
                break
            delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
            failures += 1
            log.warning("LISTEN 연결 끊김, %.0f초 뒤 다시 붙음: %s", delay, e)
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except TimeoutError:
                pass


async def _handle(payload: str, on_seq: Callable[[int], Awaitable[None]]) -> None:
    # ---- 사건 하나의 실패는 로그만 남기고 다음 사건으로 (한 건이 전달을 멈추면 안 됨) ----
    try:
        seq = json.loads(payload).get("seq")
    except (ValueError, AttributeError):
        log.error("NOTIFY payload를 못 읽음: %r", payload)
        return
    if seq is None:
        # 순번 없는 상태 알림(stock_close 등)은 이번 주 보내지 않음
        log.debug("순번 없는 NOTIFY 건너뜀: %s", payload)
        return
    try:
        await on_seq(int(seq))
    except Exception:
        log.exception("사건 seq %s 처리 실패, 다음 사건으로 넘어감", seq)


async def _safe(fn: Callable[[], Awaitable[None]], what: str) -> None:
    try:
        await fn()
    except Exception:
        log.exception("%s 실패", what)
