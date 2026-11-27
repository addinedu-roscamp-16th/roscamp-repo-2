"""구역 세계가 찾은 교통 층 결함(B1, B3)의 회귀 시험."""
from fake_robots.world import NoisyPose, ZoneWorld


def test_b1_no_robot_is_ever_outside_the_zones_it_holds():
    world = ZoneWorld("d", seed=2, injections=[
        NoisyPose("pode1", 20.0, 40.0, sigma_m=0.03, jump_m=0.10)])
    world.run(65.0)
    # 결함은 주입이 없는 pode2에서 났다(주입은 때만 바꾼다)
    assert not [v for v in world.violations if v.code.startswith("I3") and v.robot == "pode2"]


