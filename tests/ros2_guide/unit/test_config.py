from r2g.config import load


def test_stage2_defaults():
    c = load()
    assert c["l04"]["levels"] == {
        "낮음": {"state_hz": 5, "vision_hz": 1},
        "중간": {"state_hz": 10, "vision_hz": 5},
        "높음": {"state_hz": 20, "vision_hz": 10},
    }
    assert (c["l04"]["heartbeat_hz"], c["l04"]["robots_hz"]) == (2, 1)
    assert c["l04"]["video"] == {"width": 640, "height": 480, "fps": 15,
                                 "bitrate_kbps": 2000, "port": 5600}
    assert c["l02"]["sizes"] == {"1KB": 1024, "64KB": 65536, "500KB": 512000}


def test_stage2_assumed_values():
    c = load()
    assert c["l04"]["events_hz"] == 0.5 and c["l04"]["state_bytes"] == 200
    assert c["l04"]["vision_bytes"] == 640 and c["l04"]["heartbeat_stop_s"] == 1.5
    assert c["l04"]["seconds"] == 120
    assert c["l02"]["rates_hz"] == [10, 50] and c["l02"]["depth"] == 50
    assert c["l02"]["reliabilities"] == ["best_effort", "reliable"]
    assert (c["l02"]["seconds"], c["l02"]["repeats"]) == (30, 3)
    assert (c["g11"]["observe_s"], c["g11"]["other_domain_id"]) == (10, 88)
