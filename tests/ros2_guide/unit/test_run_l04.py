from run_l04 import (
    COMBOS,
    combo_key,
    gst_sender_cmd,
    heartbeat_table,
    parse_last_json,
    pc_to_pi_stats,
    pi_to_pc_stats,
    robots_problem,
    topic_table,
    video_mbps,
    video_problem,
)


def test_combos_and_key():
    assert len(COMBOS) == 6 and combo_key("낮음", "ROS만") == "낮음_ROS만"
    assert COMBOS[0] == ("낮음", "ROS만") and COMBOS[1] == ("낮음", "영상 동시")


def test_gst_sender_cmd_uses_config():
    cmd = gst_sender_cmd({"width": 640, "height": 480, "fps": 15,
                          "bitrate_kbps": 2000, "port": 5600}, "192.168.0.10")
    assert "framerate=15/1" in cmd and "bitrate=2000" in cmd
    assert "host=192.168.0.10 port=5600" in cmd
    assert cmd.startswith("gst-launch-1.0 -q videotestsrc is-live=true pattern=ball ! ")
    assert "video/x-raw,width=640,height=480,framerate=15/1" in cmd


def test_parse_last_json():
    assert parse_last_json('READY\n{"sent": {"a": 1}}\n\n') == {"sent": {"a": 1}}
    assert parse_last_json("READY\n") is None
    assert parse_last_json("[1, 2]\n") is None


def test_video_mbps():
    assert video_mbps({"bytes": 2_500_000, "seconds": 10.0}) == 2.0
    assert video_mbps({"bytes": 0, "seconds": 0}) == 0.0
    assert video_mbps(None) is None


def test_video_problem():
    assert video_problem(False, 1.9, 2000) is None
    assert video_problem(True, 1.9, 2000) == "영상 송신 확인 안 됨"
    assert video_problem(False, 0.19, 2000) == "영상 송신 확인 안 됨"
    assert video_problem(False, None, 2000) == "영상 송신 확인 안 됨"


def test_pi_to_pc_stats_uses_robot_sent_and_pc_receive_times():
    s = 1_000_000
    got = pi_to_pc_stats(4, [0, 1, 3], [0, 100 * s, 350 * s])
    assert (got["sent"], got["received"], got["lost"]) == (4, 3, 1)
    assert got["max_gap_ms"] == 250.0


def test_pc_to_pi_stats_counts_over_received_span():
    got = pc_to_pi_stats(10, [3, 4, 6, 7], max_gap_ms=None)
    assert (got["sent"], got["received"], got["lost"], got["loss_rate"]) == (5, 4, 1, 0.2)
    assert (got["first_seq"], got["last_seq"]) == (3, 7)
    assert (got["sent_full"], got["loss_rate_full"]) == (10, 0.6)


def test_pc_to_pi_stats_perfect_link_with_extra_central_seqs_is_zero_loss():
    got = pc_to_pi_stats(20, list(range(4, 15)), max_gap_ms=500.0)
    assert (got["sent"], got["received"], got["lost"], got["loss_rate"]) == (11, 11, 0, 0.0)
    assert got["longest_gap"] == 0 and got["max_gap_ms"] == 500.0
    assert got["sent_full"] == 20 and got["loss_rate_full"] == 0.45


def test_pc_to_pi_stats_nothing_received_falls_back_to_full_count():
    got = pc_to_pi_stats(5, [], max_gap_ms=None)
    assert (got["sent"], got["received"], got["loss_rate"]) == (5, 0, 1.0)
    assert got["first_seq"] is None and got["loss_rate_full"] == 1.0


def test_heartbeat_and_topic_tables_use_each_side_counts():
    s = 1_000_000_000
    robots_out = {
        "sent": {"/pinky1/state": 3},
        "window": [s, 4 * s],
        "hb_recv_ns": {"pinky1": [s // 2, 2 * s, 3 * s]},
        "seqs": {"/fleet/heartbeat@pinky1": [1, 2, 3], "/central/events": [0],
                 "/fleet/robots": [0, 1]},
        "matched": True,
    }
    central_out = {
        "sent": {"/fleet/heartbeat": 6, "/central/events": 2, "/fleet/robots": 3},
        "seqs": {"/pinky1/state": [0, 2]},
        "recv_ns": {"/pinky1/state": [10 * s, 10 * s + 200_000_000]},
    }
    hb = heartbeat_table(robots_out, 1.5)
    assert hb == {"pinky1": {"max_gap_ms": 1000.0, "over_count": 0, "over_at_s": []}}
    table = topic_table(robots_out, central_out, hb)
    assert table["/pinky1/state"]["sent"] == 3 and table["/pinky1/state"]["lost"] == 1
    assert table["/pinky1/state"]["max_gap_ms"] == 200.0
    beat = table["/fleet/heartbeat@pinky1"]
    assert (beat["sent"], beat["received"], beat["max_gap_ms"]) == (3, 3, 1000.0)
    assert beat["loss_rate"] == 0.0 and beat["sent_full"] == 6
    assert beat["loss_rate_full"] == 0.5
    assert table["/central/events"]["sent_full"] == 2 and table["/fleet/robots"]["received"] == 2


def test_robots_problem():
    ok = {"sent": {}, "matched": True}
    assert robots_problem(None, True, None) == "로봇 노드 시간 초과"
    assert robots_problem(None, False, 0) == "로봇 노드 결과 줄 없음"
    assert robots_problem({"sent": {}, "matched": False}, False, 0) == "관제 매칭 실패"
    assert robots_problem(ok, False, 1) == "로봇 노드 비정상 종료(rc=1)"
    assert robots_problem(ok, False, 0) is None


def test_heartbeat_skip_needs_complete_robots_and_window():
    from run_l04 import heartbeat_skip

    good = {"sent": {}, "matched": True, "window": [1, 5], "hb_recv_ns": {"pinky1": []}}
    assert heartbeat_skip(None, good, {}) is None
    assert heartbeat_skip("로봇 노드 시간 초과", good, {}) == "로봇 노드 시간 초과"
    assert heartbeat_skip(None, None, {}) is not None
    assert heartbeat_skip(None, {**good, "matched": False}, {}) is not None
    assert heartbeat_skip(None, good, None) is not None
    assert heartbeat_skip(None, {**good, "window": [5, 5]}, {}) is not None
    assert heartbeat_skip(None, {**good, "window": None}, {}) is not None
    assert heartbeat_skip(None, {**good, "hb_recv_ns": {}}, {}) is not None


def test_overall_risk_is_tri_state():
    from run_l04 import overall_risk

    clean = {"ok": True, "heartbeat": {"pinky1": {}}, "l04_risk": []}
    risky = {"ok": True, "heartbeat": {"pinky1": {}}, "l04_risk": ["pinky1 ..."]}
    unknown = {"ok": False, "heartbeat": None, "l04_risk": []}
    assert overall_risk({"a": clean, "b": clean}) is False
    assert overall_risk({"a": clean, "b": unknown}) is None
    assert overall_risk({"a": risky, "b": unknown}) is True
    assert overall_risk({"a": clean, "b": {"ok": False, "reason": "실행 기록 없음"}}) is None


def test_parse_last_json_skips_trailing_noise():
    out = 'READY\n{"sent": {"a": 1}}\n[ERROR] rcl shutdown\nTraceback (most recent call last)\n'
    assert parse_last_json(out) == {"sent": {"a": 1}}


def test_robots_problem_nonzero_rc_without_result():
    assert robots_problem(None, False, 139) == "로봇 노드 비정상 종료(rc=139)"
    assert robots_problem(None, False, 0) == "로봇 노드 결과 줄 없음"


def test_robots_problem_reports_node_error():
    out = {"sent": {}, "matched": True, "error": "RCLError('timer is canceled')"}
    assert robots_problem(out, False, 0) == "로봇 노드 오류(RCLError('timer is canceled'))"
    assert robots_problem({"sent": {}, "matched": True, "error": None}, False, 0) is None
