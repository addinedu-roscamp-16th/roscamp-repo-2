# ---- WS 연결 모음: 보낼 줄 · 나눠 보내기 · 스냅샷 다시 보내기 (설계 7.1 · 7.2) ----
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

import anyio

from service_bridge.core.events import Outgoing

log = logging.getLogger("service_bridge.web.hub")

QUEUE_SIZE = 100          # 연결마다 보낼 줄 길이. 넘으면 그 연결만 닫는다
CLOSE_SLOW = 1013         # 느린 받는 쪽 (Try Again Later)
CLOSE_SHUTDOWN = 1001     # 서버가 꺼짐


class Client:
    """WS 연결 하나. 보낼 메시지는 줄에 넣기만 하고, 실제 보내기는 serve()의 펌프가 한다."""

    def __init__(self, ws, audience: Literal["admin", "customer"], account_id: int | None):
        self.ws = ws
        self.audience = audience
        self.account_id = account_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.close_code: int | None = None
        self._close_requested = asyncio.Event()

    def offer(self, message: dict) -> bool:
        """줄에 넣는다. 가득 찼으면 닫기를 청하고 False."""
        if self.close_code is not None:
            return False
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.request_close(CLOSE_SLOW)
            return False

    def request_close(self, code: int) -> None:
        if self.close_code is None:
            self.close_code = code
            self._close_requested.set()


# 연결에 맞는 첫 메시지들(snapshot, 고객 시험 모드면 customer_notice)을 만드는 함수
SnapshotBuilder = Callable[[Client], Awaitable[list[dict]]]


class Hub:
    def __init__(self, build_snapshot: SnapshotBuilder):
        self._build_snapshot = build_snapshot
        self.clients: set[Client] = set()

    # ---- 등록 ----
    def register(self, ws, audience: Literal["admin", "customer"],
                 account_id: int | None) -> Client:
        client = Client(ws, audience, account_id)
        self.clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self.clients.discard(client)

    # ---- 연결 하나를 끝날 때까지 돌린다 ----
    async def serve(self, client: Client, initial: list[dict]) -> None:
        """initial을 먼저 보내고 줄을 비운다. 받는 쪽이 끊거나 보내기가 실패하거나
        닫기를 청하면(느림 · 꺼짐) 끝난다. 끝나면 등록을 지운다.
        셋 중 하나가 끝나면 나머지를 취소한다(anyio 작업 묶음: 바깥 취소도 그대로 따름)."""
        try:
            async with anyio.create_task_group() as tg:
                async def first_done(fn, *args):
                    try:
                        await fn(*args)
                    except Exception as e:  # 보내기 실패 = 끊긴 연결
                        log.info("WS 연결 끝남 (%s, account %s): %r",
                                 client.audience, client.account_id, e)
                    tg.cancel_scope.cancel()

                tg.start_soon(first_done, self._pump, client, initial)
                tg.start_soon(first_done, self._wait_disconnect, client.ws)
                tg.start_soon(first_done, client._close_requested.wait)
        finally:
            self.unregister(client)
        if client.close_code is not None:
            if client.close_code == CLOSE_SLOW:
                log.warning("WS 보낼 줄이 %d개를 넘어 닫음 (%s, account %s)",
                            QUEUE_SIZE, client.audience, client.account_id)
            try:
                await client.ws.close(code=client.close_code)
            except Exception:  # 이미 끊긴 연결
                pass

    async def _pump(self, client: Client, initial: list[dict]) -> None:
        for m in initial:
            await client.ws.send_json(m)
        while True:
            m = await client.queue.get()
            await client.ws.send_json(m)

    @staticmethod
    async def _wait_disconnect(ws) -> None:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return

    # ---- 나눠 보내기 ----
    async def deliver(self, outgoings: list[Outgoing]) -> None:
        """customer에 account_id가 None이면 모든 고객 연결에 보낸다(stock)."""
        for out in outgoings:
            for client in list(self.clients):
                if client.audience != out.audience:
                    continue
                if (out.audience == "customer" and out.account_id is not None
                        and client.account_id != out.account_id):
                    continue
                client.offer(out.message)

    # ---- LISTEN이 다시 붙은 뒤: 모든 연결에 스냅샷을 다시 ----
    async def resend_snapshots(self) -> None:
        for client in list(self.clients):
            try:
                messages = await self._build_snapshot(client)
            except Exception:
                log.exception("스냅샷 다시 만들기 실패 (%s, account %s)",
                              client.audience, client.account_id)
                continue
            for m in messages:
                client.offer(m)

    # ---- 끌 때 ----
    def close_all(self, code: int = CLOSE_SHUTDOWN) -> None:
        for client in list(self.clients):
            client.request_close(code)
