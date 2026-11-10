from r2g.qos import custom, load_profiles


def test_contract_profiles():
    p = load_profiles()
    assert p["STATE"] == {"reliability": "best_effort", "durability": "volatile", "depth": 5}
    assert p["LATCHED"] == {
        "reliability": "reliable", "durability": "transient_local", "depth": 1}
    assert p["EVENT"] == {"reliability": "reliable", "durability": "volatile", "depth": 50}


def test_custom():
    assert custom("reliable", "volatile", 7) == {
        "reliability": "reliable", "durability": "volatile", "depth": 7}
