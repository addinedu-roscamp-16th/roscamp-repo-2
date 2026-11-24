"""WS 연결별 송신 큐.

- 상태: 로봇별 최신값만 유지(덮어쓰기).
- 알림: 영속 seq 오름차순, seq 기준 중복 제거. last_seq 이하는 무시.
- 깊이 = 대기 알림 수 + 상태가 대기 중인 로봇 수. cap을 넘지 않습니다.
  꽉 찬 상태에서는 상태(버려도 되는 값)를 먼저 버리고, 알림만으로 꽉 차 있으면
  새 알림을 버리고 overflowed를 세웁니다(웹 계층이 연결을 닫고, 클라이언트는 since_seq로 복구).
- overflowed가 선 뒤에는 빈자리가 생겨도 새 알림을 받지 않습니다. 받으면 버린 seq를 건너뛴
  큰 seq가 전송되고, 클라이언트가 그 seq로 재연결하면 버린 알림이 재생에서도 빠집니다.
  큐에 남은 알림은 모두 버린 seq보다 작으므로 다 보낸 뒤 연결을 닫으면 됩니다.
- 재생(begin_replay)은 DB에 있는 만큼이므로 cap 검사를 하지 않습니다.
"""
from collections import OrderedDict


class ConnectionQueue:
    def __init__(self, cap: int, last_seq: int = 0):
        self.cap = cap
        self.last_seq = last_seq
        self._alerts = {}
        self._status = OrderedDict()
        self.max_depth_seen = 0
        self.overflowed = False

    @property
    def depth(self) -> int:
        return len(self._alerts) + len(self._status)

    @property
    def alert_depth(self) -> int:
        """대기 중인 알림 수 (상태 제외). 재생 시작 때 실시간 알림이 겹쳤는지 볼 때 씁니다."""
        return len(self._alerts)

    def _touch(self):
        self.max_depth_seen = max(self.max_depth_seen, self.depth)

    def _make_room(self) -> bool:
        if self.depth < self.cap:
            return True
        if self._status:
            self._status.popitem(last=False)  # 가장 오래된 로봇 상태를 버림
            return True
        return False

    def put_status(self, robot_id: str, msg: dict) -> None:
        if robot_id in self._status:
            self._status[robot_id] = msg
            return
        if not self._make_room():
            return  # 알림으로 꽉 참: 상태는 다음 값이 대신함
        self._status[robot_id] = msg
        self._touch()

    def put_alert(self, seq: int, msg: dict) -> None:
        if seq <= self.last_seq or seq in self._alerts:
            return
        # 이미 버린 알림이 있음: 그 뒤 알림을 보내면 클라이언트가 빈 곳을 건너뜀
        if self.overflowed:
            return
        if not self._make_room():
            self.overflowed = True
            return
        self._alerts[seq] = msg
        self._touch()

    def begin_replay(self, records) -> None:
        for seq, msg in records:
            if seq > self.last_seq and seq not in self._alerts:
                self._alerts[seq] = msg
        self._touch()

    def pop(self):
        if self._alerts:
            seq = min(self._alerts)
            self.last_seq = seq
            return self._alerts.pop(seq)
        if self._status:
            return self._status.popitem(last=False)[1]
        return None


class LatestStatus:
    """브리지 전체에서 로봇별 최신 상태 하나씩을 보관합니다 (연결과 무관).

    ConnectionQueue는 연결 중에 들어온 상태만 담으므로, 상태 발행이 멈춘 뒤 새로 연결(재연결)한
    클라이언트는 아무 상태도 받지 못합니다. 새 연결을 등록할 때 fill()로 이 스냅샷을 큐에 넣어,
    마지막으로 알려진 로봇별 상태를 먼저 보내게 합니다(계획서 LT-03 "로봇별 최신 상태 스냅샷").
    큐 상한보다 로봇이 많으면 ConnectionQueue 규칙대로 오래된 로봇 상태부터 버려집니다.
    """

    def __init__(self):
        self._latest = OrderedDict()  # robot_id -> msg, 처음 본 순서 유지

    def update(self, robot_id: str, msg: dict) -> None:
        self._latest[robot_id] = msg

    def fill(self, q: "ConnectionQueue") -> int:
        """스냅샷을 q에 넣고, 넣은 로봇 수를 돌려줍니다."""
        for robot_id, msg in self._latest.items():
            q.put_status(robot_id, msg)
        return len(self._latest)


def start_seq(since_seq, max_seq: int) -> int:
    """연결의 시작 last_seq.

    since_seq가 없으면 재생 없이 현재 최신 이후부터 받습니다.
    since_seq가 최신보다 크면(브리지 재시작 등) 재생할 것은 없고, 실시간 알림은 계속 받아야 하므로
    최신 seq로 낮춥니다.
    """
    if since_seq is None:
        return max_seq
    return min(since_seq, max_seq)


class CreditWindow:
    """애플리케이션 수준 흐름 제어: 클라이언트가 ACK하기 전까지 보낼 수 있는 메시지 수 상한.

    이렇게 해야 느린 클라이언트의 대기분이 소켓 버퍼가 아니라 ConnectionQueue에 쌓여
    상태 최신값 유지·큐 상한이 실제로 작동합니다.
    """

    def __init__(self, size: int):
        self.size = size
        self.in_flight = 0

    def can_send(self) -> bool:
        return self.in_flight < self.size

    def on_send(self) -> None:
        self.in_flight += 1

    def on_ack(self) -> None:
        if self.in_flight > 0:
            self.in_flight -= 1
