"""보고 대기열: Hello 뒤 ReportTrip, 끝 상태는 ReportOrder까지 (ROS 없음).

번호 · 순서 · 바꿔 끼우기 · 늘어나는 간격 규칙을 여기서 모두 정한다.
노드는 due · expire · answer를 부르고 요청을 보내기만 한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from fleet_core.common.commands import CANCELED, ReportOrderCmd

from fleet_manager.goal_tracker import _factor

HELLO, TRIP, ORDER = "hello", "trip", "order"
_CANCELLED = "CANCELLED"  # 브리지 ReportTrip 철자 (주문판은 CANCELED)


@dataclass(frozen=True)
class TripReport:
    # ReportTrip 요청 칸 (ROS 타입 아님)
    trip_id: str
    robot: str
    status: str  # ASSIGNED · FILLING · TRANSPORTING · SHIPPED · PARTIAL · CANCELLED · HELD
    reason: str
    placed: dict[str, int]
    missing: dict[str, int]
    epoch: int
    n: int
    stamp: float  # 보고를 처음 보낼 때의 now


@dataclass(frozen=True)
class Send:
    key: int  # 요청 번호 (큐 안에서 유일)
    kind: str  # HELLO | TRIP | ORDER
    payload: object  # HELLO: boot_id(str), TRIP: TripReport, ORDER: ReportOrderCmd


@dataclass
class _Slot:
    # (종류, trip_id)마다 하나. 보내는 중인 것 하나와 기다리는 최신 하나
    kind: str
    sending: object = None  # 보낸 적 있는 payload. 실패하면 이것을 다시 보냄
    origin: object = None  # sending을 만든 명령
    waiting: object = None  # 아직 보내지 않은 최신 명령
    key: int | None = None  # 답을 기다리는 요청
    step: int = 0
    next_at: float = 0.0


@dataclass
class _Request:
    slot_id: tuple
    sent_at: float
    origin: object = None


class ReportQueue:
    def __init__(self, boot_id: str, first_s: float, max_s: float, reply_limit_s: float,
                 max_in_flight: int, rand=random.random):
        self._first_s = first_s
        self._max_s = max_s
        self._reply_limit_s = reply_limit_s
        self._max_in_flight = max_in_flight
        self._rand = rand
        self._epoch: int | None = None
        self._n = 0
        self._next_key = 0
        self._slots: dict[tuple, _Slot] = {(HELLO, ""): _Slot(HELLO, sending=boot_id)}
        self._requests: dict[int, _Request] = {}

    @property
    def epoch(self) -> int | None:
        return self._epoch

    def add(self, cmd) -> None:
        # ReportOrderCmd도 먼저 ReportTrip으로 브리지에 알림. order_id 값이 trip_id
        trip_id = cmd.order_id if isinstance(cmd, ReportOrderCmd) else cmd.trip_id
        self._put(TRIP, trip_id, cmd)

    def due(self, now: float, ready: dict[str, bool]) -> list[Send]:
        sends = []
        in_flight = sum(1 for r in self._requests.values() if r.slot_id[0] != HELLO)
        for slot_id, slot in self._slots.items():
            kind = slot.kind
            if slot.key is not None or now < slot.next_at or not ready.get(kind, False):
                continue
            if kind != HELLO:
                # 보고는 epoch가 생긴 뒤에만. IR-41: 답을 기다리는 요청 수 상한
                if self._epoch is None or in_flight >= self._max_in_flight:
                    continue
                in_flight += 1
            if slot.waiting is not None:
                # 기다리는 최신 보고를 새 번호로 (단계는 이어 씀)
                slot.origin, slot.waiting = slot.waiting, None
                slot.sending = self._payload(kind, slot.origin, now)
            key = self._next_key
            self._next_key += 1
            slot.key = key
            self._requests[key] = _Request(slot_id, now, slot.origin)
            sends.append(Send(key, kind, slot.sending))
        return sends

    def expire(self, now: float) -> list[int]:
        late = [k for k, r in self._requests.items() if now - r.sent_at >= self._reply_limit_s]
        for key in late:
            self._fail(self._requests.pop(key), now)
        return late

    def answer(self, key: int, now: float, accepted: bool | None,
               epoch: int = 0) -> ReportOrderCmd | None:
        request = self._requests.pop(key, None)
        if request is None:
            return None  # 한도를 넘겨 버린 요청이거나 모르는 key
        slot_id = request.slot_id
        kind = slot_id[0]
        if accepted is None or (accepted is False and kind != ORDER):
            self._fail(request, now)
            return None
        slot = self._slots[slot_id]
        slot.key, slot.sending, slot.origin = None, None, None
        slot.step, slot.next_at = 0, now
        if kind == HELLO:
            self._epoch = epoch
        elif kind == TRIP and isinstance(request.origin, ReportOrderCmd):
            # 브리지가 받은 끝 상태 · HELD만 central에 알림
            self._put(ORDER, slot_id[1], request.origin)
        if slot.waiting is None:
            del self._slots[slot_id]
        if kind == ORDER and accepted is False:
            return request.origin  # central 거절. 다시 보내지 않음
        return None

    def _put(self, kind: str, trip_id: str, cmd) -> None:
        slot = self._slots.setdefault((kind, trip_id), _Slot(kind))
        # 기다리는 것이 있으면 바꿔 끼움. 기다리던 HELD(ReportOrderCmd)가 더 새 ReportTripCmd로
        # 바뀌면 그 보류는 보고하지 않음 (IR-23 최신 상태 규칙)
        slot.waiting = cmd

    def _payload(self, kind: str, cmd, now: float):
        if kind == ORDER:
            return cmd
        self._n += 1
        if isinstance(cmd, ReportOrderCmd):
            status = _CANCELLED if cmd.status == CANCELED else cmd.status
            return TripReport(cmd.order_id, cmd.robot, status, cmd.hold_reason,
                              dict(cmd.placed), dict(cmd.missing), self._epoch, self._n, now)
        return TripReport(cmd.trip_id, cmd.robot, cmd.status, cmd.reason,
                          dict(cmd.placed), dict(cmd.missing), self._epoch, self._n, now)

    def _fail(self, request: _Request, now: float) -> None:
        # 다음 간격은 실패(답 없음 · 한도 넘김 · 거절)한 뒤부터 셈
        # TODO(Q-09): IR-40 표에 Hello · ReportOrder 줄이 없어 Report* 값을 임시로 씀
        slot = self._slots[request.slot_id]
        interval = min(self._first_s * 2 ** slot.step, self._max_s)
        slot.key = None
        slot.next_at = now + interval * _factor(self._rand)
        slot.step += 1
