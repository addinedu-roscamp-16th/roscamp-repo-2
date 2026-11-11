import re

import pytest

from r2g.parts import (
    done_keys,
    load_part,
    retire_failed,
    retire_file,
    retire_for_resume,
    save_part,
)


def test_save_load_and_done(tmp_path):
    save_part(tmp_path, "L-02", "best_effort_1KB_10Hz_r0", {"x": 1})
    assert load_part(tmp_path, "L-02", "best_effort_1KB_10Hz_r0") == {"x": 1}
    assert done_keys(tmp_path, "L-02") == {"best_effort_1KB_10Hz_r0"}
    assert load_part(tmp_path, "L-02", "missing") is None


def test_save_part_refuses_overwrite(tmp_path):
    save_part(tmp_path, "L-02", "k", {})
    with pytest.raises(FileExistsError):
        save_part(tmp_path, "L-02", "k", {})


def test_done_keys_empty(tmp_path):
    assert done_keys(tmp_path, "L-04") == set()


def test_save_part_leaves_no_temp_file(tmp_path):
    save_part(tmp_path, "L-02", "k", {"ok": True})
    names = sorted(p.name for p in (tmp_path / "parts" / "L-02").iterdir())
    assert names == ["k.json"]


def test_load_part_corrupt_is_missing(tmp_path):
    path = save_part(tmp_path, "L-02", "k", {"ok": True})
    path.write_text('{"ok": tr', encoding="utf-8")
    assert load_part(tmp_path, "L-02", "k") is None
    assert done_keys(tmp_path, "L-02") == set()


def test_retire_failed_moves_failed_and_corrupt_parts(tmp_path):
    save_part(tmp_path, "G-11", "good", {"ok": True})
    save_part(tmp_path, "G-11", "bad", {"ok": False, "reason": "x"})
    broken = save_part(tmp_path, "G-11", "broken", {"ok": True})
    broken.write_text("{", encoding="utf-8")
    retired = retire_failed(tmp_path, "G-11")
    assert sorted(retired) == ["bad", "broken"]
    assert done_keys(tmp_path, "G-11") == {"good"}
    names = sorted(p.name for p in (tmp_path / "parts" / "G-11").iterdir())
    assert "good.json" in names and "bad.json" not in names and "broken.json" not in names
    assert any(re.fullmatch(r"bad\.failed-\d{8}-\d{6}(-\d+)?\.json", n) for n in names)
    # the key can be saved again (re-run) and a second retire keeps both copies
    save_part(tmp_path, "G-11", "bad", {"ok": False})
    assert retire_failed(tmp_path, "G-11") == ["bad"]
    assert len([n for n in (tmp_path / "parts" / "G-11").iterdir()
                if n.name.startswith("bad.failed-")]) == 2
    assert retire_failed(tmp_path, "nothing") == []


def test_retire_file_keeps_unique_names(tmp_path):
    a = tmp_path / "L-02.json"
    a.write_text("1", encoding="utf-8")
    first = retire_file(a)
    a.write_text("2", encoding="utf-8")
    second = retire_file(a)
    assert first != second and not a.exists()
    assert first.read_text() == "1" and second.read_text() == "2"
    assert ".failed-" in first.name and first.name.startswith("L-02.")


def test_retire_for_resume_also_moves_stale_result(tmp_path):
    save_part(tmp_path, "L-04", "a", {"ok": True})
    final = tmp_path / "L-04.json"
    final.write_text("{}", encoding="utf-8")
    assert retire_for_resume(tmp_path, "L-04") == []
    assert final.exists()  # nothing failed: the result stays
    save_part(tmp_path, "L-04", "b", {"ok": False})
    assert retire_for_resume(tmp_path, "L-04") == ["b"]
    assert not final.exists()
    assert len(list(tmp_path.glob("L-04.failed-*.json"))) == 1
