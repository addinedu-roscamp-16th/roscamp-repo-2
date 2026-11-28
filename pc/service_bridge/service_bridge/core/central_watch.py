# ---- central 상태 지켜보기: 신선함 · 재시작 알림 · 실패 기록 풀기 (재시작 설계 6.4, ROS 없음) ----
import json
import logging
import threading

import psycopg

STALE_SEC = 3.0  # TODO(Q-09): 마지막으로 받은 뒤 이보다 오래면 연결 안 됨(central은 1초마다 발행)

VIEW_LOST = "LOST"  # 3초 넘게 못 받음 또는 아직 한 번도 못 받음 (화면 work_state.central)
# CentralState.msg의 STATE_* 와 같은 값. 그 밖의 값은 넘기기를 막으려고 LOST로 본다
STATES = frozenset({"CONNECTING", "SYNCING", "RUNNING"})

RESTART_MESSAGE = "central이 다시 켜져 주문판을 다시 세웁니다"

log = logging.getLogger("service_bridge.central_watch")


class CentralWatch:
    """마지막으로 받은 (boot_id, state, 받은 시각)을 쥔다. now는 time.monotonic() 값.

    구독 콜백 · 넘기기 · 타이머가 서로 다른 실행기 스레드에서 부르므로 잠금으로 묶는다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._boot_id: str | None = None
        self._state = ""
        self._at: float | None = None

    def on_state(self, boot_id: str, state: str, now: float) -> str | None:
        """받은 값을 둔다. boot_id가 바뀌었으면 옛 boot_id를 돌려준다.
        브리지가 켜진 뒤 처음 본 boot_id는 기억만 한다(None) — 꺼져 있던 동안의 재시작은 모름."""
        with self._lock:
            old = self._boot_id
            self._boot_id, self._state, self._at = boot_id, state, now
        return old if old is not None and old != boot_id else None

    def view(self, now: float) -> str:
        with self._lock:
            state, at = self._state, self._at
        if at is None or now - at > STALE_SEC or state not in STATES:
            return VIEW_LOST
        return state

    def can_dispatch(self, now: float) -> bool:
        """RUNNING이고 신선할 때만 SubmitTrip을 보낸다(아니면 보내지도 세지도 않음)."""
        return self.view(now) == "RUNNING"


# ---- central 재시작: 안내 알림 + 확정 답 전인 줄의 실패 기록 풀기 (한 트랜잭션) ----
def record_central_restart(conn: psycopg.Connection, old_boot_id: str, new_boot_id: str) -> int:
    """새 central은 앞 central의 일을 모르므로 앞의 시도 수 · 쉼 · 결과 모름이 뜻을 잃는다.
    last_result는 무엇이 있었는지 보려고 남긴다. 푼 줄 수를 돌려준다."""
    with conn.transaction():
        # 줄 잠금 순서는 record_failure와 같다(trip_dispatch 먼저, alert 나중)
        trips = [r[0] for r in conn.execute(
            "UPDATE trip_dispatch SET attempts = 0, next_attempt_at = NULL, unknown_since = NULL "
            "WHERE central_acked_at IS NULL RETURNING trip_id").fetchall()]
        conn.execute(
            "UPDATE alert SET status = 'RESOLVED', resolved_at = now() "
            "WHERE code = 'DISPATCH_STALLED' AND status = 'OPEN' AND trip_id = ANY(%s)", (trips,))
        # 관리자는 확인만 한다(조치 없음). code별 유일 제약이 없어 재시작마다 한 줄씩 쌓인다
        conn.execute(
            "INSERT INTO alert (level, code, source, message, detail) "
            "VALUES ('INFO', 'CENTRAL_RESTART', 'service_bridge', %s, %s)",
            (RESTART_MESSAGE, json.dumps({"old_boot_id": old_boot_id,
                                          "new_boot_id": new_boot_id})))
    log.info("central 재시작: old_boot_id=%s new_boot_id=%s 실패 기록 푼 줄 %d",
             old_boot_id, new_boot_id, len(trips))
    return len(trips)
