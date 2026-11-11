import pytest

from r2g.parts import done_keys, load_part, save_part


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
