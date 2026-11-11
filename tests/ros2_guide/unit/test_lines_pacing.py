from r2g.lines import last_json_object, stdout_tail
from r2g.pacing import wait_until


def test_last_json_object_skips_trailing_noise():
    out = 'READY\n{"sent": 3}\n[ERROR] shutdown noise\nTraceback x\n'
    assert last_json_object(out) == {"sent": 3}
    assert last_json_object('{"a": 1}\n{"a": 2}\n{broken\n') == {"a": 2}
    assert last_json_object("[1]\nnone\n") is None
    assert last_json_object("") is None


def test_stdout_tail_limits_lines_and_width():
    out = "\n".join(f"line{i}" for i in range(30)) + "\n\n" + "x" * 500 + "\n"
    tail = stdout_tail(out)
    assert len(tail) == 20 and tail[-1] == "x" * 300 and tail[0] == "line11"
    assert stdout_tail("") == []


def test_wait_until_keeps_spinning_until_deadline():
    clock = [0.0]
    calls = []

    def spin(timeout_sec):
        calls.append(timeout_sec)
        clock[0] += min(timeout_sec, 0.01)  # returns early on every event

    wait_until(0.05, spin, now=lambda: clock[0])
    assert len(calls) == 5 and clock[0] >= 0.05 - 1e-9


def test_wait_until_past_deadline_does_not_spin():
    calls = []
    wait_until(1.0, lambda timeout_sec: calls.append(timeout_sec), now=lambda: 2.0)
    assert calls == []
