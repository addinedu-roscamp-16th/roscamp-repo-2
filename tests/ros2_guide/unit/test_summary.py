import json

from r2g import parts
from r2g.summary import W_VARIANTS, summary_lines


def _result(run_dir, test_id, result, observations=None):
    (run_dir / f"{test_id}.json").write_text(json.dumps(
        {"id": test_id, "result": result, "observations": observations or {}},
        ensure_ascii=False), encoding="utf-8")


def test_w_variants_match_runner_order():
    from run_l01 import W_ORDER

    assert list(W_VARIANTS) == W_ORDER


def test_summary_counts_failed_parts_and_missing_files(tmp_path):
    _result(tmp_path, "L-01-W-A", "통과", {"excluded_trials": []})
    _result(tmp_path, "L-01-W-B", "판정 불가", {"excluded_trials": [1, 4]})
    _result(tmp_path, "L-02", "측정", {"combos": {}})
    parts.save_part(tmp_path, "L-02", "a", {"ok": True})
    parts.save_part(tmp_path, "L-02", "b", {"ok": False})
    parts.save_part(tmp_path, "L-02", "c", {"ok": False})
    parts.retire_file(parts.save_part(tmp_path, "L-02", "d", {"ok": False}))
    _result(tmp_path, "G-11", "통과")
    lines = summary_lines(tmp_path, ["L-01-W", "L-02", "L-04", "G-11"])
    assert "L-01-W-A: 통과" in lines
    assert "L-01-W-B: 판정 불가 (제외된 시도 2건)" in lines
    assert "L-01-W-C: 결과 파일 없음" in lines
    assert "L-02: 측정 (실패한 조합 2개)" in lines
    assert "L-04: 결과 파일 없음" in lines
    assert "G-11: 통과" in lines
    assert len(lines) == 8 + 3


def test_summary_respects_only_and_ignores_retired_results(tmp_path):
    _result(tmp_path, "G-11", "실패")
    (tmp_path / "G-11.failed-20260101-000000.json").write_text("{}", encoding="utf-8")
    assert summary_lines(tmp_path, ["G-11"]) == ["G-11: 실패"]


def test_summary_l04_risk_tri_state(tmp_path):
    combos = {"a": {"ok": True, "heartbeat": {}}, "b": {"ok": False, "heartbeat": None},
              "c": {"ok": False, "heartbeat": None}}
    _result(tmp_path, "L-04", "측정", {"risk": None, "combos": combos})
    assert summary_lines(tmp_path, ["L-04"]) == ["L-04: 측정 (통합 운영 위험: 확인 못 한 조합 2)"]
    (tmp_path / "L-04.json").unlink()
    _result(tmp_path, "L-04", "측정", {"risk": True, "combos": {}})
    assert summary_lines(tmp_path, ["L-04"]) == ["L-04: 측정 (통합 운영 위험: 있음)"]
    (tmp_path / "L-04.json").unlink()
    _result(tmp_path, "L-04", "측정", {"risk": False, "combos": {}})
    assert summary_lines(tmp_path, ["L-04"]) == ["L-04: 측정 (통합 운영 위험: 없음)"]


def test_summary_unreadable_result(tmp_path):
    (tmp_path / "G-11.json").write_text("{", encoding="utf-8")
    assert summary_lines(tmp_path, ["G-11"])[0].startswith("G-11: 읽기 실패")
