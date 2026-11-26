# ---- FastAPI 앱: 주문 접수 · 스냅샷 REST, 고객 · 관리자 WS, LISTEN 사건 전달 (설계 5 · 7) ----
import asyncio
import contextlib
import datetime
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import anyio.from_thread
import psycopg
from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg_pool import ConnectionPool, PoolTimeout, TooManyRequests
from pydantic import BaseModel, ConfigDict

from service_bridge.core import snapshot
from service_bridge.core.events import Outgoing, envelope, read_event, route
from service_bridge.core.intake import account_exists, check_shape, place_order
from service_bridge.core.types import OrderLine, OrderRequest, PlaceError, PlaceOk
from service_bridge.web.hub import Client, Hub
from service_bridge.web.listener import run_listener

log = logging.getLogger("service_bridge.web")

ROBOTS_PERIOD = 0.2   # robots를 보내는 간격(초). 새 값이 있을 때만 보낸다
CLOSE_UNKNOWN_ACCOUNT = 4404
# 화면 파일 폴더 (N-07). 설치본에도 같은 자리에 깔린다(setup.py package_data)
STATIC_DIR = Path(__file__).resolve().parent / "static"
# 잠금 · 풀 대기 초과, 기다리는 요청 넘침, statement_timeout: 잠시 뒤 다시 보내면 되는 오류 (503 BUSY)
BUSY_ERRORS = (LockNotAvailable, PoolTimeout, TooManyRequests, QueryCanceled)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---- 오류 답: {"code", "message", "detail"} (설계 5.1) ----
class ApiError(Exception):
    def __init__(self, error: PlaceError, headers: dict | None = None):
        self.error = error
        self.headers = headers


def _error_response(e: PlaceError, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=e.status, headers=headers,
                        content={"code": e.code, "message": e.message, "detail": e.detail})


# ---- 고객 구분을 읽는 유일한 곳. 인증을 붙일 때 이 함수만 바꾼다 ----
def current_account(conn: psycopg.Connection, account_id: int | None) -> int:
    if account_id is None or not account_exists(conn, account_id):
        raise ApiError(PlaceError(404, "UNKNOWN_ACCOUNT", "모르는 계정"))
    return account_id


# ---- 로봇 상태 마지막 값 (브리지 → 웹) ----
class RobotsBox:
    """마지막 값만 쥔다. put은 어느 스레드에서 불러도 되고, 값은 이벤트 루프 스레드에서 바뀐다."""

    def __init__(self):
        self._robots: list[dict] = []
        self.version = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop | None) -> None:
        with self._lock:
            self._loop = loop

    def put(self, robots: list[dict]) -> None:
        value = list(robots)
        with self._lock:
            loop = self._loop
        if loop is None:
            self._set(value)  # 루프가 아직 없음(켜기 전 · 끈 뒤): 그대로 둔다
            return
        try:
            loop.call_soon_threadsafe(self._set, value)
        except RuntimeError:  # 루프가 닫힘
            self._set(value)

    def _set(self, value: list[dict]) -> None:
        self._robots = value
        self.version += 1

    def latest(self) -> list[dict]:
        return self._robots


# ---- 주문 요청 본문 ----
class _Item(BaseModel):
    model_config = ConfigDict(strict=True)
    product_id: int
    qty: int


class _OrderBody(BaseModel):
    model_config = ConfigDict(strict=True)
    account_id: int
    items: list[_Item]


def create_app(pool: ConnectionPool, dsn: str, mode: str,
               wake_dispatch: Callable[[], None]) -> FastAPI:
    """pool: 웹 전용 동기 풀(브리지 풀과 따로). dsn: LISTEN 전용 연결 주소.
    mode: NORMAL · TEST. wake_dispatch: 브리지 넘기기 깨우기(어느 스레드에서 불러도 됨)."""
    robots = RobotsBox()

    # ---- 첫 메시지 만들기 (DB 읽기는 스레드로) ----
    def _customer_snapshot(account_id: int) -> dict:
        with pool.connection() as conn:
            return snapshot.customer_snapshot(conn, account_id, mode)

    def _admin_snapshot(latest: list[dict]) -> dict:
        with pool.connection() as conn:
            return snapshot.admin_snapshot(conn, mode, latest)

    async def build_snapshot(client: Client) -> list[dict]:
        if client.audience == "admin":
            data = await asyncio.to_thread(_admin_snapshot, robots.latest())
            return [envelope("snapshot", data, _now())]
        data = await asyncio.to_thread(_customer_snapshot, client.account_id)
        msgs = [envelope("snapshot", data, _now())]
        if data["notice"] is not None:
            msgs.append(envelope("customer_notice", data["notice"], _now()))
        return msgs

    hub = Hub(build_snapshot)

    # ---- LISTEN이 받은 사건 하나: 읽기 → 나누기 → 보내기 ----
    def _read(seq: int):
        with pool.connection() as conn:
            return read_event(conn, seq)

    async def on_seq(seq: int) -> None:
        try:
            row = await asyncio.to_thread(_read, seq)
        except psycopg.OperationalError:
            # 일시 DB 오류(풀 대기 초과 등): 커밋된 사건을 놓쳤으니 다시 붙을 때처럼 스냅샷으로 맞춘다.
            # 데이터 오류(줄 없음 · 잘못된 data)는 아래처럼 로그만 남기고 다음 사건으로
            log.exception("ws_event seq %d 읽기 실패, 모든 연결에 스냅샷 다시 보냄", seq)
            try:
                await hub.resend_snapshots()
            except Exception:
                log.exception("스냅샷 다시 보내기 실패")
            return
        if row is None:
            log.warning("ws_event seq %d 줄이 없음", seq)
            return
        event_type, data = row
        outgoings = route(event_type, seq, data, _now())
        if not outgoings:
            # TODO(Q-07): alert 사건은 이번 주 WS로 보내지 않는다(설계 7.5). 칸이 정해지면 route에서 나눈다
            log.debug("보내지 않는 사건 %s seq %d", event_type, seq)
            return
        await hub.deliver(outgoings)

    def on_connect(pid: int) -> None:
        app.state.listener_pid = pid

    # ---- robots: 0.2초마다, 바뀌었을 때만 관리자에게 ----
    async def robots_ticker() -> None:
        sent = robots.version
        while True:
            await asyncio.sleep(ROBOTS_PERIOD)
            if robots.version == sent:
                continue
            sent = robots.version
            msg = envelope("robots", {"robots": robots.latest()}, _now())
            await hub.deliver([Outgoing("admin", None, msg)])

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        robots.bind(asyncio.get_running_loop())
        stop = asyncio.Event()
        listener = asyncio.create_task(
            run_listener(dsn, on_seq, hub.resend_snapshots, stop, on_connect))
        ticker = asyncio.create_task(robots_ticker())
        try:
            yield
        finally:
            stop.set()
            hub.close_all()
            ticker.cancel()
            try:
                await asyncio.wait_for(listener, 2)
            except (TimeoutError, asyncio.CancelledError):
                pass
            await asyncio.gather(ticker, return_exceptions=True)
            robots.bind(None)

    app = FastAPI(title="iponic order intake", lifespan=lifespan)
    app.state.robots = robots
    app.state.hub = hub
    app.state.listener_pid = None

    # ---- 오류 답 모양 맞추기 ----
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, e: ApiError):
        return _error_response(e.error, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_req: Request, e: RequestValidationError):
        return _error_response(PlaceError(400, "BAD_REQUEST", "요청 모양이 틀림",
                                          {"errors": jsonable_encoder(e.errors())}))

    # ---- POST /api/orders (설계 5.2) ----
    @app.post("/api/orders", status_code=202)
    def post_order(body: _OrderBody,
                   idempotency_key: str | None = Header(default=None)):
        if mode == "TEST":
            raise ApiError(PlaceError(503, "MAINTENANCE", "점검 중"))
        if not idempotency_key:
            raise ApiError(PlaceError(400, "BAD_REQUEST", "Idempotency-Key 헤더가 없음"))
        req = OrderRequest(body.account_id,
                           tuple(OrderLine(i.product_id, i.qty) for i in body.items))
        error = check_shape(req)
        if error:
            raise ApiError(error)
        try:
            with pool.connection() as conn:
                current_account(conn, req.account_id)
            with pool.connection() as conn:
                res = place_order(conn, req, idempotency_key)
        except BUSY_ERRORS:
            raise ApiError(PlaceError(503, "BUSY", "잠시 뒤 같은 키로 다시 보내 주세요"),
                           {"Retry-After": "1"}) from None
        if isinstance(res, PlaceError):
            raise ApiError(res)
        assert isinstance(res, PlaceOk)
        if not res.replayed:
            # 다시 온 요청은 아무것도 쓰지 않으므로 깨우거나 재고를 다시 보낼 일이 없다
            wake_dispatch()
            _send_stock()
        return {"order_id": res.order_id, "status": "RECEIVED", "trip_ids": list(res.trip_ids)}

    def _send_stock() -> None:
        # 접수는 이미 커밋됨: 재고 보내기 실패가 202를 바꾸지 않게 로그만 남긴다
        try:
            with pool.connection() as conn:
                data = snapshot.stock(conn)
            msg = envelope("stock", data, _now())
            # 요청 스레드(FastAPI 스레드 풀)에서 이벤트 루프로 넘겨 보낸다
            anyio.from_thread.run(hub.deliver, [Outgoing("admin", None, msg),
                                                Outgoing("customer", None, msg)])
        except Exception:
            log.exception("주문 뒤 stock 보내기 실패")

    # ---- GET /api/snapshot (설계 5.5) ----
    @app.get("/api/snapshot")
    def get_snapshot(view: str, account_id: int | None = None):
        if view == "customer":
            with pool.connection() as conn:
                acc = current_account(conn, account_id)
            return _customer_snapshot(acc)
        if view == "admin":
            return _admin_snapshot(robots.latest())
        raise ApiError(PlaceError(400, "BAD_REQUEST", "view는 customer · admin 중 하나",
                                  {"view": view}))

    # ---- WS (설계 7.1) ----
    @app.websocket("/ws/admin")
    async def ws_admin(ws: WebSocket):
        await ws.accept()
        await _serve(ws, "admin", None)

    @app.websocket("/ws/customer")
    async def ws_customer(ws: WebSocket, account_id: int | None = None):
        await ws.accept()

        def check() -> int:
            with pool.connection() as conn:
                return current_account(conn, account_id)

        try:
            acc = await asyncio.to_thread(check)
        except ApiError:
            await ws.close(code=CLOSE_UNKNOWN_ACCOUNT)
            return
        await _serve(ws, "customer", acc)

    async def _serve(ws: WebSocket, audience, account_id) -> None:
        # 먼저 등록해 스냅샷을 만드는 동안의 사건도 줄에 쌓고, 스냅샷을 맨 앞에 보낸다
        client = hub.register(ws, audience, account_id)
        try:
            initial = await build_snapshot(client)
        except Exception:
            hub.unregister(client)
            log.exception("WS 첫 스냅샷 실패 (%s, account %s)", audience, account_id)
            await ws.close(code=1011)
            return
        await hub.serve(client, initial)

    # ---- 화면 파일 (N-07): /customer/ 고객, /admin/ 관리자, / → 고객 화면 ----
    # 모든 경로를 받는 마운트라 API · WS 경로를 다 등록한 뒤 맨 끝에 붙인다(먼저 붙이면 가로챔)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app

