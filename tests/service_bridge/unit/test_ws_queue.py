"""WS 연결별 송신 큐 (LT-02·LT-03이 기대는 로직)."""
from sbt_bridge.core.ws_queue import ConnectionQueue, CreditWindow, start_seq


def _alert(seq):
    return {"type": "alert", "seq": seq}


def test_status_coalesces_per_robot():
    q = ConnectionQueue(cap=10)
    for i in range(100):
        q.put_status("r1", {"v": i})
    q.put_status("r2", {"v": 0})
    assert q.depth == 2
    assert q.pop() == {"v": 99}


def test_depth_never_exceeds_cap():
    q = ConnectionQueue(cap=5)
    for i in range(20):
        q.put_status(f"r{i}", {"v": i})
    assert q.max_depth_seen <= 5
    assert q.depth == 5


def test_alert_overflow_flags():
    q = ConnectionQueue(cap=3)
    for s in range(1, 5):
        q.put_alert(s, _alert(s))
    assert q.overflowed is True
    assert q.depth == 3


def test_overflow_blocks_later_alerts():
    """포화로 알림을 버린 뒤에는 빈자리가 생겨도 뒤 알림을 받지 않습니다 (검토 의견 1).

    받으면 클라이언트가 빠진 seq를 건너뛴 큰 seq를 받고,
    그 seq로 재연결하면 빠진 알림이 재생에서도 빠집니다.
    """
    q = ConnectionQueue(cap=2)
    for s in (1, 2, 3):          # 3은 포화로 버려짐
        q.put_alert(s, _alert(s))
    out = [q.pop()["seq"]]       # 1을 보내 빈자리가 생김
    q.put_alert(4, _alert(4))    # 포화 뒤 도착: 받으면 안 됨
    while (m := q.pop()) is not None:
        out.append(m["seq"])
    # 빠진 3보다 작은 것만 보내고 끝 → 클라이언트는 since_seq=2로 재연결
    assert out == [1, 2]
    assert q.overflowed is True


def test_new_connection_gets_latest_status_snapshot():
    """상태 발행이 멈춘 뒤 새로 연결해도 로봇별 최신 상태를 받습니다 (검토 의견 5, LT-03)."""
    from sbt_bridge.core.ws_queue import LatestStatus
    latest = LatestStatus()
    for i in range(5):
        latest.update("r1", {"robot_id": "r1", "seq": i})
    latest.update("r2", {"robot_id": "r2", "seq": 7})
    q = ConnectionQueue(cap=10)
    assert latest.fill(q) == 2
    # 스냅샷은 상태뿐: 재생 시나리오 확인(live_queued)에 섞이지 않음
    assert q.alert_depth == 0
    got = []
    while (m := q.pop()) is not None:
        got.append((m["robot_id"], m["seq"]))
    assert got == [("r1", 4), ("r2", 7)]  # 로봇별 최신값 한 번씩


def test_replay_merges_live_without_dup():
    q = ConnectionQueue(cap=50, last_seq=10)
    q.put_alert(13, _alert(13))
    q.put_alert(14, _alert(14))
    q.begin_replay([(11, _alert(11)), (12, _alert(12)), (13, _alert(13))])
    out = []
    while (m := q.pop()) is not None:
        out.append(m["seq"])
    assert out == [11, 12, 13, 14]


def test_replay_since_future_seq_is_empty():
    q = ConnectionQueue(cap=5, last_seq=999)
    q.begin_replay([])
    assert q.pop() is None


def test_alert_at_or_below_last_seq_ignored():
    q = ConnectionQueue(cap=5, last_seq=3)
    q.put_alert(2, _alert(2))
    q.put_alert(3, _alert(3))
    assert q.depth == 0


def test_start_seq_clamps_future_since():
    assert start_seq(None, 50) == 50          # since_seq 없음: 재생 없이 다음 알림부터
    assert start_seq(20, 50) == 20            # 정상 재생
    assert start_seq(10**6, 50) == 50         # 최신보다 큼: 재생 없음, 실시간 알림은 받아야 함


def test_future_since_still_receives_live_alert():
    q = ConnectionQueue(cap=5, last_seq=start_seq(10**6, 50))
    q.begin_replay([])
    q.put_alert(51, {"seq": 51})
    assert q.pop() == {"seq": 51}


def test_credit_window_limits_in_flight():
    w = CreditWindow(2)
    assert w.can_send()
    w.on_send(); w.on_send()
    assert not w.can_send()
    w.on_ack()
    assert w.can_send()
    w.on_ack(); w.on_ack()  # 초과 ACK는 무시
    assert w.in_flight == 0


def test_backpressure_coalesces_status_when_client_slow():
    q, w = ConnectionQueue(cap=50), CreditWindow(1)
    sent = []
    for i in range(100):           # 클라이언트가 ACK하지 않는 동안 상태 100개 도착
        q.put_status("r1", {"seq": i})
        if w.can_send() and (m := q.pop()) is not None:
            w.on_send(); sent.append(m["seq"])
    w.on_ack()
    sent.append(q.pop()["seq"])
    assert sent == [0, 99]         # 중간 값은 최신값으로 합쳐짐
