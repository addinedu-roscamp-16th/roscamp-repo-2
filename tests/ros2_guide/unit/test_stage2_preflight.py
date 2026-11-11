import json

from stage2_preflight import doctor_pi_cmd, preflight_verdict, write_preflight

ALL_OK = {"ssh": True, "pi_ros_jazzy": True, "sync": True,
          "pc_to_pi": True, "pi_to_pc": True, "gst": True}


def test_preflight_all_ok():
    assert preflight_verdict(ALL_OK) == (True, [])


def test_preflight_blocks_on_one_way_comm():
    ok, reasons = preflight_verdict({**ALL_OK, "pi_to_pc": False})
    assert not ok and reasons[0].startswith("pi_to_pc")
    assert "PC 방화벽" in reasons[0]


def test_preflight_gst_missing_warns_only():
    ok, reasons = preflight_verdict({**ALL_OK, "gst": False})
    assert ok and any("gst" in r for r in reasons)


def test_preflight_unknown_is_failure():
    assert preflight_verdict({**ALL_OK, "ssh": None})[0] is False


def test_preflight_missing_key_is_failure():
    checks = {k: v for k, v in ALL_OK.items() if k != "sync"}
    ok, reasons = preflight_verdict(checks)
    assert not ok and reasons[0].startswith("sync")


def test_write_preflight_keeps_previous(tmp_path):
    first = write_preflight(tmp_path, {"n": 1})
    second = write_preflight(tmp_path, {"n": 2})
    assert first == second == tmp_path / "preflight.json"
    assert json.loads(second.read_text(encoding="utf-8")) == {"n": 2}
    olds = list(tmp_path.glob("preflight.*.json"))
    assert len(olds) == 1 and json.loads(olds[0].read_text(encoding="utf-8")) == {"n": 1}
    write_preflight(tmp_path, {"n": 3})
    assert len(list(tmp_path.glob("preflight.*.json"))) == 2


def test_doctor_pi_cmd_sources_ros_with_env():
    cmd = doctor_pi_cmd({"ROS_DOMAIN_ID": "87", "ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET"})
    assert cmd.startswith("source /opt/ros/jazzy/setup.bash")
    assert "ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET" in cmd and "ros2 doctor --report" in cmd


def test_preflight_wifi_signal_unknown_warns_only():
    ok, reasons = preflight_verdict({**ALL_OK, "wifi": False})
    assert ok and "wifi: 신호 세기를 읽지 못함" in reasons
    assert preflight_verdict({**ALL_OK, "wifi": None}) == (True, [])
    assert preflight_verdict({**ALL_OK, "wifi": True}) == (True, [])
