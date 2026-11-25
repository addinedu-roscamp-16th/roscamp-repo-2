"""로봇 노드의 운영 모드 판단: 켜질 때 값(IR-72), 같은 번호 한 번만(IR-20 · IR-21)."""
from pode_fsm.admin import MODE_MISMATCH, REMEMBER_COUNT, REMEMBER_S, AdminState, Reply


def fresh():
    return AdminState("boot-1")


def test_a_fresh_robot_is_locked_in_unknown_mode_with_nothing_forced():
    a = fresh()
    assert (a.boot_id, a.lock, a.mode, a.forced, a.last_request_id) == (
        "boot-1", True, "UNKNOWN", [], "")


def test_the_mode_is_a_target_value_and_unknown_values_are_refused():
    a = fresh()
    assert a.set_mode("r1", "TEST", now=0.0) == Reply(True)
    assert a.mode == "TEST" and a.last_request_id == "r1"
    assert a.set_mode("r2", "TEST", now=0.0).accepted and a.mode == "TEST"
    bad = a.set_mode("r3", "UNKNOWN", now=0.0)
    assert (bad.accepted, bad.reason, a.mode) == (False, MODE_MISMATCH, "TEST")
    assert a.last_request_id == "r2", "a refusal is not applied"
    assert a.set_mode("r4", "NORMAL", now=0.0).accepted and a.mode == "NORMAL"


def test_the_same_request_id_is_applied_once_and_answered_the_same():
    a = fresh()
    first = a.set_mode("r1", "TEST", now=0.0)
    assert a.set_mode("r2", "NORMAL", now=1.0).accepted
    again = a.set_mode("r1", "TEST", now=2.0)
    assert again == first and a.mode == "NORMAL", "a repeat must not switch the mode back"


def test_a_refusal_is_remembered_too():
    a = fresh()
    first = a.set_mode("r1", "STAGE", now=0.0)
    assert a.set_mode("r1", "STAGE", now=1.0) == first and a.mode == "UNKNOWN"


def test_request_ids_are_forgotten_after_sixty_seconds():
    a = fresh()
    a.set_mode("r1", "TEST", now=0.0)
    a.set_mode("r2", "NORMAL", now=1.0)
    assert a.set_mode("r1", "TEST", now=1.0 + REMEMBER_S).accepted
    assert a.mode == "TEST", "r1 was forgotten, so it is a new request"


def test_only_the_last_thirty_two_request_ids_are_kept():
    a = fresh()
    a.set_mode("old", "TEST", now=0.0)
    for i in range(REMEMBER_COUNT):
        a.set_mode(f"r{i}", "NORMAL", now=1.0)
    assert a.mode == "NORMAL"
    assert a.set_mode("old", "TEST", now=2.0).accepted and a.mode == "TEST"


def test_an_empty_request_id_is_not_remembered():
    a = fresh()
    a.set_mode("", "TEST", now=0.0)
    assert a.last_request_id == "" and a.mode == "TEST"
    a.set_mode("", "NORMAL", now=1.0)
    assert a.mode == "NORMAL"
