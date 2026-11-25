"""운영 모드가 다른 Pode는 새 호출(운반 건)을 받지 않고, 모드가 맞으면 다시 받는다(IR-73). 하던 일은 그대로 한다."""
from core_helpers import codes
from test_manager import call, drive, make


def ticks(core, n=40):
    out = []
    for _ in range(n):
        out += core.tick()
    return out


def moves_to(out, robot):
    return [c for c in out if getattr(c, "robot", None) == robot
            and type(c).__name__ in ("MoveCmd", "MoveOrderCmd")]


def test_a_robot_in_another_mode_is_not_called():
    core, _ = make({"r1": "P-1"})
    core.set_mode_blocked("r1", True, "reports NORMAL, system TEST")
    call(core, 1000)
    out = ticks(core)
    assert not moves_to(out, "r1")
    assert core.robot_view("r1")["phase"] != "FILLING"
    assert "ROBOT_MODE_MISMATCH" in codes(out)


def test_it_is_called_again_when_the_mode_matches():
    core, _ = make({"r1": "P-1"})
    core.set_mode_blocked("r1", True, "reports NORMAL, system TEST")
    call(core, 1000)
    ticks(core)
    core.set_mode_blocked("r1", False)
    out = drive(core, "r1", lambda: core.robot_view("r1")["phase"] == "FILLING")
    assert "ROBOT_MODE_MATCHED" in codes(out)


def test_another_robot_takes_the_call_meanwhile():
    core, _ = make({"r1": "P-1", "r2": "P-2"})
    core.set_mode_blocked("r1", True, "reports NORMAL, system TEST")
    call(core, 1000)
    drive(core, ("r1", "r2"), lambda: core.robot_view("r2")["phase"] == "FILLING")
    assert core.robot_view("r1")["phase"] != "FILLING"


def test_the_same_answer_twice_gives_one_event():
    core, _ = make({"r1": "P-1"})
    core.set_mode_blocked("r1", True, "x")
    core.set_mode_blocked("r1", True, "x")
    core.set_mode_blocked("r1", False)
    core.set_mode_blocked("r1", False)
    out = core.tick()
    assert codes(out).count("ROBOT_MODE_MISMATCH") == 1
    assert codes(out).count("ROBOT_MODE_MATCHED") == 1


def test_an_unknown_robot_is_rejected():
    core, _ = make({"r1": "P-1"})
    core.set_mode_blocked("nobody", True, "x")
    assert "INPUT_REJECTED" in codes(core.tick())
