from run_l01 import VARIANTS, topic_for


def test_topic_names():
    assert topic_for("A") == "/r2g/l01/a"
    assert topic_for("E-TL") == "/r2g/l01/e_tl"
    assert len({topic_for(v) for v in VARIANTS}) == len(VARIANTS)


def test_variant_table():
    assert VARIANTS["A"] == {"profile": "EVENT", "mode": "once", "late_sub": False}
    assert VARIANTS["B"] == {"profile": "EVENT", "mode": "wait_match", "late_sub": False}
    assert VARIANTS["C"] == {"profile": "LATCHED", "mode": "once", "late_sub": False}
    assert VARIANTS["D"] == {"profile": "STATE", "mode": "once", "late_sub": False}
    assert VARIANTS["F"] == {"profile": "EVENT", "mode": "stream", "late_sub": False}
    assert VARIANTS["S"] == {"profile": None, "mode": "service", "late_sub": False}
    assert set(VARIANTS) == {"A", "B", "C", "D", "E-VOL", "E-TL", "F", "S"}


def test_sub_verdict():
    from run_l01 import sub_verdict

    assert sub_verdict(True, True, "통과") == "통과"
    assert sub_verdict(True, True, "측정") == "측정"
    assert sub_verdict(False, True, "통과") == "판정 불가"
    assert sub_verdict(True, False, "실패") == "판정 불가"


def test_late_sub_variants():
    assert VARIANTS["E-VOL"] == {"profile": "EVENT", "mode": "once", "late_sub": True}
    assert VARIANTS["E-TL"] == {"profile": "LATCHED", "mode": "once", "late_sub": True}
    assert VARIANTS["S"] == {"profile": None, "mode": "service", "late_sub": False}


def test_no_stub_path():
    import run_l01

    assert not hasattr(run_l01, "IMPLEMENTED")


def test_target_files():
    from run_l01 import target_files

    assert target_files(["A", "E-TL"]) == ["L-01-A.json", "L-01-E-TL.json"]


def test_late_offset_and_exclusion():
    from run_l01 import late_offset_ms, late_trial_exclusion

    assert late_offset_ms(2_000_000_000, 1_000_000_000) == 1000.0
    assert late_offset_ms(None, 1) is None
    assert late_offset_ms(1, None) is None

    def ex(**kw):
        a = dict(pub_ready=True, pub_timed_out=False, pub_rc=0, publish_ns=1, pub_sent=1,
                 sub_timed_out=False, sub_rc=0, sub_created_ns=2, offset_ms=1000.0)
        a.update(kw)
        return late_trial_exclusion(**a)

    assert ex() is None
    assert ex(offset_ms=500.0) is None
    assert "오프셋" in ex(offset_ms=499.9)
    assert "발행" in ex(pub_ready=False)
    assert "발행" in ex(pub_timed_out=True, pub_rc=None)
    assert "rc=1" in ex(pub_rc=1)
    assert "발행" in ex(publish_ns=None, offset_ms=None)
    assert "구독" in ex(sub_timed_out=True, sub_rc=None)
    assert "rc=2" in ex(sub_rc=2)
    assert "구독" in ex(sub_created_ns=None, offset_ms=None)
    assert "보낸 메시지 없음" in ex(pub_sent=0)
    assert "보낸 메시지 없음" in ex(pub_sent=None)


def test_pub_trial_exclusion():
    from run_l01 import pub_trial_exclusion

    good = {"trial": 0, "publish_ns": 5, "sent": 1}
    assert pub_trial_exclusion(False, 0, good) is None
    assert "타임아웃" in pub_trial_exclusion(True, None, good)
    assert "rc=1" in pub_trial_exclusion(False, 1, good)
    assert "결과 줄 없음" in pub_trial_exclusion(False, 0, None)
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {**good, "sent": 0})
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {**good, "publish_ns": None})
    assert "보낸 메시지 없음" in pub_trial_exclusion(False, 0, {"trial": 0, "sent": 1})


def test_sent_nothing_cannot_reproduce_loss():
    """A rc=0 publisher that sent nothing leaves the denominator instead of counting as lost."""
    from run_l01 import counted_tally, l01_verdict, pub_trial_exclusion

    infos = {0: {"publish_ns": 5, "sent": 1}, 1: {"publish_ns": None, "sent": 0}}
    excluded = {t for t, i in infos.items() if pub_trial_exclusion(False, 0, i)}
    t = counted_tally(2, [0], excluded)
    assert excluded == {1} and t["lost"] == 0 and t["counted"] == 1
    assert l01_verdict("A", t, len(excluded)) == "측정"


def test_pub_sent_total():
    from run_l01 import pub_sent_total

    assert pub_sent_total([{"sent": 1}, {"sent": 30}, {"sent": None}, {}]) == 31
    assert pub_sent_total([]) == 0


def test_offset_stats():
    from run_l01 import offset_stats

    assert offset_stats([]) == {"count": 0, "min": None, "median": None, "max": None}
    assert offset_stats([1000.0, 1200.0, 1100.0]) == {
        "count": 3, "min": 1000.0, "median": 1100.0, "max": 1200.0}


def test_counted_tally():
    from run_l01 import counted_tally

    t = counted_tally(5, [0, 1, 4], {2, 3})
    assert (t["counted"], t["received"], t["lost"], t["lost_trials"]) == (3, 3, 0, [])
    assert "sent" not in t
    t = counted_tally(5, [0, 3], {3})
    assert (t["counted"], t["received"], t["lost"], t["lost_trials"]) == (4, 1, 3, [1, 2, 4])
    assert counted_tally(2, [], {0, 1})["counted"] == 0


def test_l01_verdict():
    from run_l01 import counted_tally, l01_verdict

    full = counted_tally(3, [0, 1, 2], set())
    assert l01_verdict("C", full, 0) == "통과"
    assert l01_verdict("C", counted_tally(3, [0, 1], set()), 0) == "실패"
    assert l01_verdict("C", counted_tally(3, [0, 1], {2}), 1) == "판정 불가"
    assert l01_verdict("S", counted_tally(3, [0, 1, 2], {2}), 1) == "판정 불가"
    assert l01_verdict("A", counted_tally(3, [0], {2}), 1) == "측정"
    assert l01_verdict("E-VOL", full, 0) == "측정"
    assert l01_verdict("C", counted_tally(2, [], {0, 1}), 2) == "판정 불가"
    assert l01_verdict("A", counted_tally(2, [], {0, 1}), 2) == "판정 불가"


def test_service_call_ok():
    from run_l01 import service_call_ok

    assert service_call_ok(False, 0, {"trial": 0, "ok": True, "wait_ms": 3.0}) is True
    assert service_call_ok(False, 0, {"trial": 0, "ok": False, "wait_ms": 5000.0}) is False
    assert service_call_ok(False, 0, None) is False
    assert service_call_ok(True, None, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 1, {"trial": 0, "ok": True, "wait_ms": 3.0}) is False
    assert service_call_ok(False, 0, {"trial": 0, "ok": "true"}) is False


def test_l01_size_gate():
    from run_l01 import gated_verdict

    assert gated_verdict("C", "통과", 50) == ("통과", None)
    assert gated_verdict("C", "통과", 2) == ("판정 불가", "개발 실행(시도 2회): 공식 판정 아님")
    assert gated_verdict("S", "실패", 10)[0] == "판정 불가"
    assert gated_verdict("A", "측정", 2) == ("측정", "개발 실행(시도 2회): 공식 판정 아님")
    assert gated_verdict("F", "측정", 50) == ("측정", None)


def test_match_timeout_is_excluded():
    from run_l01 import MATCH_TIMEOUT, pub_trial_exclusion

    skipped = {"trial": 0, "publish_ns": None, "sent": 0, "matched": False}
    assert pub_trial_exclusion(False, 0, skipped) == MATCH_TIMEOUT
    assert pub_trial_exclusion(False, 0, {"trial": 0, "publish_ns": 5, "sent": 1,
                                         "matched": True}) is None


def test_topic_for_stage2():
    from run_l01 import topic_for

    assert topic_for("A", "l01w") == "/r2g/l01w/a"
    assert topic_for("E-TL", "l01w") == "/r2g/l01w/e_tl"
    assert topic_for("A") == "/r2g/l01/a"


def test_topic_prefix_and_targets():
    from run_l01 import target_files, topic_prefix

    assert topic_prefix("L-01") == "l01" and topic_prefix("L-01-W") == "l01w"
    assert target_files(["A", "S"], "L-01-W") == ["L-01-W-A.json", "L-01-W-S.json"]
    assert target_files(["A"]) == ["L-01-A.json"]


def test_w_order_puts_priority_first():
    from run_l01 import VARIANTS, W_ORDER

    assert W_ORDER[:4] == ["A", "B", "C", "F"] and set(W_ORDER) == set(VARIANTS)
    assert len(W_ORDER) == len(VARIANTS)


def test_device_gate():
    from run_l01 import device_gate

    note = "장비 1대(PI_HOST=local): 공식 판정 아님"
    assert device_gate("통과", "B", "local") == ("판정 불가", note)
    assert device_gate("측정", "A", "local") == ("측정", note)
    assert device_gate("통과", "B", "sbt-pi") == ("통과", None)
    assert device_gate("통과", "B", None) == ("통과", None)


class _FakeLauncher:
    def __init__(self, local):
        self.is_local = local
        self.calls = []

    def start(self, script, args, env_over):
        self.calls.append(("start", script, args, dict(env_over)))
        return "P"

    def run(self, script, args, timeout, env_over):
        self.calls.append(("run", script, args, dict(env_over)))
        return "R"

    def stop(self, p):
        self.calls.append(("stop", p))


def test_pi_side_routes_through_launcher():
    from run_l01 import PiSide

    fl = _FakeLauncher(local=False)
    side = PiSide({}, fl, {"ROS_DOMAIN_ID": "87"}, "l01w")
    assert side.remote
    assert side.start("l01_pub.py", ["--x"]) == "P"
    assert side.run("l01_srv.py", ["client"], 5.0) == "R"
    side.stop("P")
    assert fl.calls[0] == ("start", "l01_pub.py", ["--x"], {"ROS_DOMAIN_ID": "87"})
    assert fl.calls[1][:2] == ("run", "l01_srv.py")
    assert fl.calls[2] == ("stop", "P")
    assert not PiSide({}, _FakeLauncher(local=True)).remote
    assert not PiSide({}).remote


def test_pi_side_without_launcher_is_stage1_command(monkeypatch):
    import sys

    import run_l01
    from run_l01 import PiSide

    seen = {}
    monkeypatch.setattr(run_l01.proc, "run_child",
                        lambda cmd, t, env: seen.update(cmd=cmd, t=t, env=env))
    PiSide({"E": "1"}).run("l01_pub.py", ["--topic", "/t"], 15.0)
    assert seen["cmd"] == [sys.executable, str(run_l01.NODES / "l01_pub.py"), "--topic", "/t"]
    assert seen["env"] == {"E": "1"} and seen["t"] == 15.0


def _fake_main_env(monkeypatch, tmp_path):
    import run_l01

    launcher = _FakeLauncher(local=True)
    launcher.cleaned = 0
    launcher.cleanup = lambda: setattr(launcher, "cleaned", launcher.cleaned + 1)
    ran = []

    def fake_run_variant(v, trials, env, tmp, side=None):
        ran.append(v)
        obs = {"proc_label": "구독 노드", "sub_ready": True, "sub_alive": True,
               "sub_summary_present": True, "excluded_trials": [], "excluded_reasons": {},
               "counted": trials, "received": trials, "lost": 0, "loss_rate": 0.0,
               "lost_trials": [], "sub_exit_code": None}
        return obs, {"trials": trials}, []

    monkeypatch.setattr(run_l01, "run_variant", fake_run_variant)
    monkeypatch.setattr(run_l01.results, "save_doctor", lambda d: d / "doctor.txt")
    monkeypatch.setattr(run_l01.remote, "make_launcher", lambda host, env: launcher)
    monkeypatch.setattr(run_l01.os, "environ", dict(run_l01.os.environ))
    return run_l01, launcher, ran


def test_main_pi_host_resume_and_notes(monkeypatch, tmp_path):
    import json

    run_l01, launcher, ran = _fake_main_env(monkeypatch, tmp_path)
    argv = ["--pi-host", "local", "--id-prefix", "L-01-W", "--trials", "2",
            "--variants", "S,A", "--out-root", str(tmp_path), "--run-id", "r"]
    assert run_l01.main(argv) == 0
    assert ran == ["A", "S"]  # W_ORDER, not the given order
    a = json.loads((tmp_path / "r" / "L-01-W-A.json").read_text(encoding="utf-8"))
    s = json.loads((tmp_path / "r" / "L-01-W-S.json").read_text(encoding="utf-8"))
    assert a["conditions"]["pi_host"] == "local" and a["conditions"]["devices"] == "1대"
    assert s["result"] == "판정 불가"
    assert "장비 1대(PI_HOST=local): 공식 판정 아님" in s["notes"]
    assert launcher.cleaned == 1
    assert run_l01.main(argv) == 2  # existing results, no --resume: refuse
    (tmp_path / "r" / "L-01-W-S.json").unlink()
    ran.clear()
    assert run_l01.main(argv + ["--resume"]) == 0
    assert ran == []  # S part exists (done_keys), A json exists


def test_main_resume_runs_only_missing(monkeypatch, tmp_path):
    run_l01, _, ran = _fake_main_env(monkeypatch, tmp_path)
    argv = ["--pi-host", "local", "--id-prefix", "L-01-W", "--trials", "2",
            "--variants", "A,B", "--out-root", str(tmp_path), "--run-id", "r"]
    assert run_l01.main(argv[:-2] + ["--variants", "A", "--run-id", "r"]) == 0
    ran.clear()
    assert run_l01.main(argv + ["--resume"]) == 0
    assert ran == ["B"]


def test_main_without_pi_host_unchanged_fields(monkeypatch, tmp_path):
    import json

    run_l01, _, ran = _fake_main_env(monkeypatch, tmp_path)
    assert run_l01.main(["--trials", "2", "--variants", "A", "--out-root", str(tmp_path),
                         "--run-id", "s1"]) == 0
    d = json.loads((tmp_path / "s1" / "L-01-A.json").read_text(encoding="utf-8"))
    assert "pi_host" not in d["conditions"] and "devices" not in d["conditions"]
    assert not (tmp_path / "s1" / "parts").exists()
