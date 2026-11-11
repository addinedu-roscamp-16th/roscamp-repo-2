from run_g11 import (
    CASES,
    DIRECTIONS,
    build_record,
    case_key,
    daemon_stop_cmd,
    judge_g11,
    parse_result,
)


def ok(n):
    return {"received": n, "peer_seen_max": 1 if n else 0}


def full(control=5, isolated=0):
    r = {"대조": {"PC→파이": ok(control), "파이→PC": ok(control)}}
    for c in ("도메인", "PC_LOCALHOST", "파이_LOCALHOST"):
        r[c] = {"PC→파이": ok(isolated), "파이→PC": ok(isolated)}
    return r


def test_g11_pass_and_fail():
    assert judge_g11(full())[0] == "통과"
    assert judge_g11(full(isolated=3))[0] == "실패"


def test_g11_control_must_communicate():
    verdict, notes = judge_g11(full(control=0))
    assert verdict == "판정 불가" and notes[0].startswith("대조에서 통신 안 됨")


def test_g11_graph_seen_is_note_only():
    r = full()
    r["PC_LOCALHOST"]["PC→파이"] = {"received": 0, "peer_seen_max": 1}
    verdict, notes = judge_g11(r)
    assert verdict == "통과" and "그래프에 상대 보임: PC_LOCALHOST PC→파이" in notes


def test_g11_control_none_is_unjudgeable():
    r = full()
    r["대조"]["파이→PC"] = {"received": None, "peer_seen_max": None}
    verdict, notes = judge_g11(r)
    assert verdict == "판정 불가" and notes[0].startswith("대조에서 통신 안 됨")


def test_g11_isolation_none_is_unjudgeable():
    r = full()
    r["도메인"]["PC→파이"] = {"received": None, "peer_seen_max": None}
    verdict, notes = judge_g11(r)
    assert verdict == "판정 불가"
    assert any("도메인 PC→파이" in n for n in notes)


def test_g11_received_beats_missing_data():
    r = full()
    r["도메인"]["PC→파이"] = {"received": None, "peer_seen_max": None}
    r["PC_LOCALHOST"]["파이→PC"] = ok(2)
    assert judge_g11(r)[0] == "실패"


def test_g11_missing_case_is_unjudgeable():
    r = full()
    del r["파이_LOCALHOST"]
    assert judge_g11(r)[0] == "판정 불가"


def test_cases_order_and_env():
    assert [c["name"] for c in CASES] == ["대조", "도메인", "PC_LOCALHOST", "파이_LOCALHOST"]
    by = {c["name"]: c for c in CASES}
    assert by["대조"]["pc"]["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "SUBNET"
    assert by["PC_LOCALHOST"]["pc"]["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    assert by["PC_LOCALHOST"]["pi"]["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "SUBNET"
    assert by["파이_LOCALHOST"]["pi"]["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    assert by["도메인"]["pc"]["ROS_DOMAIN_ID"] != by["도메인"]["pi"]["ROS_DOMAIN_ID"]
    assert DIRECTIONS == ("PC→파이", "파이→PC")


def test_cases_use_given_domains():
    from run_g11 import build_cases

    c = {x["name"]: x for x in build_cases("5", 9)}
    assert c["대조"]["pc"]["ROS_DOMAIN_ID"] == c["대조"]["pi"]["ROS_DOMAIN_ID"] == "5"
    assert c["도메인"]["pi"]["ROS_DOMAIN_ID"] == "9"


def test_case_key_and_parse_result():
    assert case_key("대조", "PC→파이") == "대조_PC→파이"
    assert parse_result('x\n{"received": 4, "peer_seen_max": 1}\n') == {
        "received": 4, "peer_seen_max": 1}  # fmt: skip
    assert parse_result("none\n") is None
    assert parse_result('{"received": "a"}') is None


def test_daemon_stop_cmd():
    cmd = daemon_stop_cmd({"ROS_DOMAIN_ID": "88", "ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET"})
    assert "source /opt/ros/jazzy/setup.bash" in cmd
    assert "ROS_DOMAIN_ID=88" in cmd and "ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET" in cmd
    assert cmd.endswith("ros2 daemon stop")


def test_record_keeps_received_when_publisher_failed():
    rec = build_record("PC→파이", "/t", {"received": 3, "peer_seen_max": 1}, None,
                       "보내는 노드 시간 초과")  # fmt: skip
    assert rec["received"] == 3 and not rec["ok"] and rec["pub_saw_subscribers_max"] is None
    r = full()
    r["도메인"]["PC→파이"] = rec
    assert judge_g11(r)[0] == "실패"


def test_record_keeps_both_views():
    rec = build_record("파이→PC", "/t", {"received": 0, "peer_seen_max": 0},
                       {"sent": 50, "peer_seen_max": 1}, None)  # fmt: skip
    assert rec["sub_saw_publishers_max"] == 0 and rec["pub_saw_subscribers_max"] == 1
    assert rec["peer_seen_max"] == 1 and rec["received"] == 0
    r = full()
    r["PC_LOCALHOST"]["파이→PC"] = rec
    verdict, notes = judge_g11(r)
    assert verdict == "통과"
    assert "그래프에 상대 보임: PC_LOCALHOST 파이→PC" in notes
    assert "그래프에 상대 보임(보내는 쪽이 구독자를 봄): PC_LOCALHOST 파이→PC" in notes


def test_record_no_results():
    rec = build_record("PC→파이", "/t", None, None, "받는 노드가 READY를 내지 않음")
    assert rec["received"] is None and rec["peer_seen_max"] is None
