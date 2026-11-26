# ---- 운반 건 넘기기: 접수 순서대로 하나씩 SubmitTrip (설계 6.2) ----
from __future__ import annotations

import time
from datetime import datetime, timezone

from fleet_interfaces.srv import SubmitTrip
from psycopg_pool import ConnectionPool
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node

from service_bridge.core.dispatch import (
    FAST_TRIES, RETRY_SEC, head_item, record_ack, record_failure, retry_delay)
from service_bridge.ros import convert

TICK_SEC = 1.0           # 가드 조건을 못 받은 경우를 위한 확인 주기
REPLY_TIMEOUT_SEC = 2.0  # IR-40: SubmitTrip 응답 한도
DB_TIMEOUT_SEC = 0.2     # 풀에서 연결을 기다리는 한도 (실행기 스레드를 오래 잡지 않게)

# ---- central 응답 분류 (SubmitTrip.srv · 통신 설계 F-04, SB-06) ----
# central은 접수(accepted=true)와 거절(accepted=false + 까닭, 예 INVALID_REQUEST)을 확정 응답으로
# 기억하고 같은 trip_id에는 처음 응답을 그대로 준다 -> 둘 다 record_ack.
# NOT_READY는 기억하지 않는 "아직 못 받음" -> 다시 보낸다(record_failure).
RETRYABLE_REASONS = frozenset({"NOT_READY"})


class Dispatcher:
    """맨 앞 건 하나만 보낸다. 상태(_future · _due)는 _tick(한 콜백 그룹) 안에서만 바꾼다.

    future의 done 콜백은 그룹 밖 실행기 작업으로 돌기 때문에 가드 조건만 울린다.
    """

    def __init__(self, node: Node, pool: ConnectionPool, contract):
        self._node = node
        self._pool = pool
        self._contract = contract
        self._group = MutuallyExclusiveCallbackGroup()
        self._client = None
        self._timer = None
        self._guard = None
        self._future = None        # 보낸 뒤 답을 기다리는 요청 하나
        self._trip_id = ""
        self._sent_at = 0.0        # monotonic
        self._due = 0.0            # 이 시각(monotonic) 전에는 새로 보내지 않음
        self._started_at = 0.0     # start() 시각(monotonic)

    def start(self) -> None:
        """켜질 때 남은 줄도 이어서 보낸다(첫 확인을 바로 시킴)."""
        self._client = self._node.create_client(
            SubmitTrip, self._contract.name("submit_trip"), callback_group=self._group)
        self._guard = self._node.create_guard_condition(self._tick, callback_group=self._group)
        self._started_at = time.monotonic()
        self._timer = self._node.create_timer(TICK_SEC, self._tick, callback_group=self._group)
        self._guard.trigger()

    def wake(self) -> None:
        """어느 스레드에서 불러도 됨. 다시 보낼 시각(_due)은 건너뛰지 않는다."""
        if self._guard is not None:
            self._guard.trigger()

    # ---- 한 번 확인 ----
    def _tick(self) -> None:
        try:
            if self._future is not None:
                if self._future.done():  # 응답이 먼저. 시간 초과 판정보다 앞
                    self._handle_reply()
                elif time.monotonic() - self._sent_at > REPLY_TIMEOUT_SEC:
                    self._abandon()
                return
            if time.monotonic() < self._due:
                return
            self._send_head()
        except Exception as e:  # 실행기를 멈추지 않는다. 다음 확인에 다시
            self._node.get_logger().warning(f"넘기기 확인 실패: {e!r}", throttle_duration_sec=5.0)

    def _send_head(self) -> None:
        with self._pool.connection(timeout=DB_TIMEOUT_SEC) as conn:
            item = head_item(conn)
        if item is None:
            return
        if item.next_attempt_at is not None:  # 쉬는 중 (5번 실패 뒤 30초)
            wait = (item.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                self._due = time.monotonic() + wait
                return
        if not self._client.service_is_ready():
            # 켜진 뒤 첫 TICK_SEC은 디스커버리 전일 수 있다: 세지 않고 다음 확인을 기다린다
            if time.monotonic() - self._started_at < TICK_SEC:
                return
            self._fail(item.trip_id, "NO_SERVICE")
            return
        self._trip_id = item.trip_id
        self._sent_at = time.monotonic()
        self._future = self._client.call_async(convert.submit_request(item))
        self._future.add_done_callback(lambda _f: self._guard.trigger())

    # ---- 응답 (_tick 안, DB 짧은 트랜잭션 하나). 버린 요청의 늦은 응답은 _future가 아니라 무시됨 ----
    def _handle_reply(self) -> None:
        future, trip_id = self._future, self._trip_id
        try:
            resp = future.result()
            if not resp.accepted and resp.reason in RETRYABLE_REASONS:
                self._future = None
                self._fail(trip_id, resp.reason)
                return
            with self._pool.connection(timeout=DB_TIMEOUT_SEC) as conn:
                record_ack(conn, trip_id, resp.accepted, resp.reason)
            self._future = None  # DB에 적은 뒤에야 비운다
            self._due = 0.0  # 다음 건은 바로
        except Exception as e:
            self._future = None
            self._node.get_logger().warning(f"넘기기 응답 처리 실패 {trip_id}: {e!r}",
                                            throttle_duration_sec=5.0)
            self._due = time.monotonic() + RETRY_SEC

    def _abandon(self) -> None:
        future, trip_id = self._future, self._trip_id
        self._future = None
        self._client.remove_pending_request(future)
        self._fail(trip_id, "TIMEOUT")

    # ---- 실패 기록과 다음 시각 ----
    def _fail(self, trip_id: str, result: str) -> None:
        try:
            with self._pool.connection(timeout=DB_TIMEOUT_SEC) as conn:
                attempts = record_failure(conn, trip_id, result)
        except Exception as e:
            self._node.get_logger().warning(f"넘기기 실패 기록 못 함 {trip_id}: {e!r}",
                                            throttle_duration_sec=5.0)
            self._due = time.monotonic() + RETRY_SEC
            return
        delay = retry_delay(attempts)
        if attempts >= FAST_TRIES:
            self._node.get_logger().warning(
                f"운반 건 넘기기 멈춤: trip_id={trip_id} attempts={attempts} result={result}")
        # 쉼(delay None)의 길이는 DB next_attempt_at이 정한다. 그때까지는 다음 확인이 읽어 맞춘다
        self._due = time.monotonic() + (delay if delay is not None else 0.0)
