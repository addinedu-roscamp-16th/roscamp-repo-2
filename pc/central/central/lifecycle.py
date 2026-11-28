# ---- central 단계: 연결 설정 → 주문판 갱신 → 실행 중 (ROS 의존 없음, 시각은 부르는 쪽이 챙김) ----
from __future__ import annotations

import random
from typing import Callable

from central.board import Board

CONNECTING = "CONNECTING"  # 브리지 목록 서비스와 /system/mode를 기다리는 중
SYNCING = "SYNCING"  # 브리지에서 끝나지 않은 건 목록을 받는 중
RUNNING = "RUNNING"  # 주문판을 발행하고 SubmitTrip · ReportOrder를 받는 중

HELD = "HELD"
# TODO(Q-09): IR-40 Report* 임시 값(1 → 2 → 4 → 8 → 10초, 최대 10초, ±20%)
RETRY_STEPS = (1, 2, 4, 8, 10)
JITTER = 0.2


class Lifecycle:
    def __init__(self, board: Board, boot_id: str, rand: Callable[[], float] = random.random):
        self._board = board
        self._rand = rand
        self._failures = 0
        self.boot_id = boot_id
        self.state = CONNECTING

    def ready_to_sync(self, service_ready: bool, mode_seen: bool) -> bool:
        """목록 서비스가 보이고 /system/mode도 한 번 받았을 때만 SYNCING으로 간다."""
        if self.state != CONNECTING or not (service_ready and mode_seen):
            return False
        self.state = SYNCING
        return True

    def on_list(self, accepted: bool, trips: list[tuple[str, dict, object, str]]) -> bool:
        """trips: (trip_id, items, submitted_at, status). 거절이면 SYNCING에 남는다."""
        if self.state != SYNCING or not accepted:
            return False
        self._board.load([(trip_id, items, submitted_at, status == HELD)
                          for trip_id, items, submitted_at, status in trips])
        self.state = RUNNING
        return True

    def next_retry_sec(self) -> float:
        """실패 때마다 한 칸씩 늘어나는 다시 부르기 간격(±20% 흩뜨림)."""
        base = RETRY_STEPS[min(self._failures, len(RETRY_STEPS) - 1)]
        self._failures += 1
        return base * (1 + JITTER * (2 * self._rand() - 1))

    def accepts_work(self) -> bool:
        return self.state == RUNNING
