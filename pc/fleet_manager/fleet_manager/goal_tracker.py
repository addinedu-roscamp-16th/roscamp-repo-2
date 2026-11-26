# ---- 액션 goal 추적 ----
from __future__ import annotations

import random
from dataclasses import dataclass

from fleet_core.common.params import RETRY_JITTER


def _factor(rand) -> float:
    # IR-41: 재전송 간격에 곱하는 배수, 0.8~1.2 균등 (report_queue도 같이 씀)
    return 1.0 - RETRY_JITTER + 2.0 * RETRY_JITTER * rand()


# ---- goal 전송·수락·완료·유실과 취소 상태 추적 ----
@dataclass
class _Cancel:
    tried_at: float
    factor: float
    tries: int = 1
    warned: bool = False


class GoalTracker:
    def __init__(self, rand=random.random):
        self._rand = rand
        self._pending: set = set()
        self._handles: dict = {}
        self._cancels: dict = {}
        self._lost: set = set()
        self._result_lost: set = set()  # 결과 future를 잃었지만 handle은 쥔 goal(IR-62)
        self._lost_seen: dict = {}  # 그 goal을 처음 본 시각. 오래되면 놓는다
        self._dropped: list = []

    def sent(self, key) -> None:
        self._pending.add(key)

    def accepted(self, key, handle) -> None:
        self._pending.discard(key)
        self._handles[key] = handle
        if key in self._cancels:
            handle.cancel_goal_async()

    def finished(self, key) -> None:
        self._pending.discard(key)
        self._handles.pop(key, None)
        self._cancels.pop(key, None)
        self._lost.discard(key)
        self._result_lost.discard(key)
        self._lost_seen.pop(key, None)

    def lost(self, key) -> None:
        if key in self._cancels:
            self._lost.add(key)

    def result_lost(self, key) -> None:
        self._pending.discard(key)
        self._result_lost.add(key)

    def drop_stale_lost(self, now: float, keep_s: float) -> None:
        for key in list(self._result_lost):
            if now - self._lost_seen.setdefault(key, now) >= keep_s:
                self.finished(key)

    def cancel(self, key, now: float) -> None:
        if key in self._cancels:
            return
        handle = self._handles.get(key)
        if handle is not None:
            handle.cancel_goal_async()
        elif key not in self._pending:
            return
        if key in self._result_lost:  # 결과가 올 수 없으니 한 번 보내고 끝낸다
            self.finished(key)
            return
        self._cancels[key] = _Cancel(now, _factor(self._rand))

    def canceling(self, key) -> bool:
        return key in self._cancels

    def take_dropped(self) -> list:
        dropped, self._dropped = self._dropped, []
        return dropped

    def retry_cancels(self, now: float, retry_s: float, warn_after: int) -> list:
        unconfirmed = []
        for key, cancel in list(self._cancels.items()):
            if now - cancel.tried_at < retry_s * cancel.factor:
                continue
            if cancel.warned and key in self._lost:
                self.finished(key)
                self._dropped.append(key)
                continue
            if cancel.tries >= warn_after and not cancel.warned:
                cancel.warned = True
                unconfirmed.append(key)
            cancel.tried_at = now
            cancel.factor = _factor(self._rand)
            cancel.tries += 1
            handle = self._handles.get(key)
            if handle is not None:
                handle.cancel_goal_async()
        return unconfirmed

