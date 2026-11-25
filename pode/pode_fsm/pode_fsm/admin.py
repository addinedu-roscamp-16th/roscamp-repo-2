# ---- 관리 명령 상태: 운영 모드, 요청 번호 기억 ----
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

# ---- 켜질 때 값, 모드, 거절 사유, 기억 한도 ----
BOOT_MODE = "UNKNOWN"
MODES = ("NORMAL", "TEST")
MODE_MISMATCH = "MODE_MISMATCH"
REMEMBER_COUNT = 32
REMEMBER_S = 60.0


@dataclass(frozen=True)
class Reply:
    accepted: bool
    reason: str = ""
    detail: str = ""


# ---- 로봇 노드가 보고하는 관리 상태 (IR-72: 잠김 · UNKNOWN · 강제 없음으로 시작) ----
class AdminState:
    def __init__(self, boot_id: str):
        self.boot_id = boot_id
        self.lock = True
        self.mode = BOOT_MODE
        self.forced: list[str] = []
        self.last_request_id = ""
        self._seen: OrderedDict[str, tuple[float, Reply]] = OrderedDict()

    def set_mode(self, request_id: str, mode: str, now: float) -> Reply:
        def judge() -> Reply:
            if mode not in MODES:
                return Reply(False, MODE_MISMATCH, f"unknown mode {mode!r}")
            self.mode = mode
            return Reply(True)
        return self._once(request_id, now, judge)

    # ---- IR-20 · IR-21: 같은 request_id는 한 번만 반영하고 같은 답 ----
    def _once(self, request_id: str, now: float, judge) -> Reply:
        self._forget(now)
        if request_id and request_id in self._seen:
            return self._seen[request_id][1]
        reply = judge()
        if request_id:
            self._seen[request_id] = (now, reply)
            while len(self._seen) > REMEMBER_COUNT:
                self._seen.popitem(last=False)
            if reply.accepted:
                self.last_request_id = request_id
        return reply

    def _forget(self, now: float) -> None:
        while self._seen and now - next(iter(self._seen.values()))[0] > REMEMBER_S:
            self._seen.popitem(last=False)
