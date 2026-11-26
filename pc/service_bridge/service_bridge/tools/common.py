# ---- 시험 도구 공통: contract 읽기, 품목 변환, 실행기 밖에서 기다리는 서비스 호출 ----
from __future__ import annotations

import threading

from ament_index_python.packages import get_package_share_directory
from fleet_core.common.contract import Contract, load_contract
from fleet_interfaces.msg import OrderItem

WAIT_SLICE_SEC = 0.05  # 기다리는 동안 stop을 확인하는 간격


# ---- contract와 품목 ----
def load_default_contract() -> Contract:
    return load_contract(get_package_share_directory("fleet_interfaces") + "/contract.yaml")


def parse_items(text: str) -> dict[str, int]:
    """"mentos:1,mygummy:1" → {"mentos": 1, "mygummy": 1}. 같은 kind는 합친다. 빈 글은 빈 dict."""
    out: dict[str, int] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        kind, sep, count = part.partition(":")
        if not sep or not kind.strip():
            raise ValueError(f"품목 형식은 KIND:개수: {part!r}")
        out[kind.strip()] = out.get(kind.strip(), 0) + int(count)
    return out


def to_order_items(items: dict[str, int]) -> list[OrderItem]:
    return [OrderItem(kind=k, count=c) for k, c in items.items()]


# ---- 서비스 한 번 부르기 ----
def call_once(client, request, timeout: float, stop: threading.Event | None = None):
    """요청 한 번. 응답이 오면 응답, 한도가 지나거나 stop이면 None.

    서버가 아직 안 보이면 보낸 요청이 사라지므로, 한도 안에서 서버가 보일 때까지 먼저 기다린다.
    응답은 실행기가 future를 끝낼 때 Event로 알린다. 부르는 쪽은 실행기 밖 스레드여야 한다.
    """
    stopped = threading.Event() if stop is None else stop
    waited = 0.0
    while not client.service_is_ready():
        if waited >= timeout or stopped.wait(WAIT_SLICE_SEC):
            return None
        waited += WAIT_SLICE_SEC
    done = threading.Event()
    future = client.call_async(request)
    future.add_done_callback(lambda _f: done.set())
    while waited < timeout and not stopped.is_set():
        if done.wait(WAIT_SLICE_SEC):
            return future.result()
        waited += WAIT_SLICE_SEC
    # 한도가 지난 요청은 대기 목록에서 빼 늦게 온 응답을 버린다
    if not future.done():
        client.remove_pending_request(future)
    return future.result() if future.done() else None
