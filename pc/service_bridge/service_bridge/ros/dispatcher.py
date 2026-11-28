# ---- 운반 건 넘기기: 접수 순서대로 하나씩 SubmitTrip (설계 6.2) ----
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

from fleet_interfaces.srv import SubmitTrip
from psycopg_pool import ConnectionPool
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node

from service_bridge.core.dispatch import (
    FAST_TRIES,
    RETRY_SEC,
    head_item,
    record_ack,
    record_failure,
    record_not_ready,
    retry_delay,
)
from service_bridge.ros import convert

TICK_SEC = 1.0           # 가드 조건을 못 받은 경우를 위한 확인 주기
REPLY_TIMEOUT_SEC = 2.0  # IR-40: SubmitTrip 응답 한도
DB_TIMEOUT_SEC = 0.2     # 풀에서 연결을 기다리는 한도 (실행기 스레드를 오래 잡지 않게)

# ---- central 응답 분류 (SubmitTrip.srv · 통신 설계 F-04, SB-06) ----
# central은 접수(accepted=true)와 거절(accepted=false + 까닭, 예 INVALID_REQUEST)을 확정 응답으로
# 기억하고 같은 trip_id에는 처음 응답을 그대로 준다 -> 둘 다 record_ack.
# NOT_READY는 기억하지 않는 "아직 못 받음" -> 세지 않고 다시 보낸다
# (record_not_ready, central 재시작 설계 6.4).
RETRYABLE_REASONS = frozenset({"NOT_READY"})


class Dispatcher:
    """맨 앞 건 하나만 보낸다. 상태(_future · _due)는 _tick(한 콜백 그룹) 안에서만 바꾼다.

    future의 done 콜백은 그룹 밖 실행기 작업으로 돌기 때문에 가드 조건만 울린다.
    """

    def __init__(self, node: Node, pool: ConnectionPool, contract,
                 can_dispatch: Callable[[], bool] = lambda: True):
        """can_dispatch: central이 RUNNING이고 신선한지(어느 스레드에서 불려도 되는 함수)."""
        self._node = node
        self._can_dispatch = can_dispatch
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
        self._forget_due = False   # central 재시작: 다음 _tick에서 _due를 버림 (다른 스레드가 켬)

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

    def forget_backoff(self) -> None:
        """central 재시작 뒤(DB의 실패 기록을 푼 뒤) 부른다. 앞 central 때문에 잡힌 다시 보낼 시각을
        다음 확인에서 버린다. 어느 스레드에서 불러도 됨: 깃발만 켜고 _due는 _tick에서 바꾼다."""
        self._forget_due = True
        self.wake()

    # ---- 한 번 확인 ----
    def _tick(self) -> None:
        try:
            if self._forget_due:
                self._forget_due = False
                self._due = 0.0
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
        # central이 RUNNING이 아니거나 상태가 끊김: 보내지도 세지도 않는다(DB에 아무것도 안 씀).
        # _due는 그대로 두고, RUNNING이 되면 BridgeNode가 wake()로 깨운다
        if not self._can_dispatch():
            return
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
                # 실패로 세지 않음: 30초 쉼 · DISPATCH_STALLED에 빠지지 않고 1초 ±20% 뒤 다시
                with self._pool.connection(timeout=DB_TIMEOUT_SEC) as conn:
                    record_not_ready(conn, trip_id)
                self._future = None
                self._due = time.monotonic() + retry_delay(0)
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
