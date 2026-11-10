import json

import pytest

from r2g.proc import child_env
from r2g.results import env_info, new_run_dir, save_doctor, write_result


def test_write_and_refuse_overwrite(tmp_path):
    d = new_run_dir(tmp_path, "r1")
    p = write_result(d, "L-01-A", "측정", {"lost": 1}, {"trials": 50})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["id"] == "L-01-A" and data["run_id"] == "r1" and data["result"] == "측정"
    assert set(data["env"]) == {"ROS_DISTRO", "RMW_IMPLEMENTATION", "ROS_DOMAIN_ID", "host"}
    with pytest.raises(FileExistsError):
        write_result(d, "L-01-A", "측정", {}, {})


def test_rejects_unknown_result(tmp_path):
    with pytest.raises(ValueError):
        write_result(tmp_path, "X", "pass", {}, {})


def test_rmw_default(monkeypatch):
    monkeypatch.delenv("RMW_IMPLEMENTATION", raising=False)
    assert env_info()["RMW_IMPLEMENTATION"] == "기본"


def test_new_run_dir_shared(tmp_path):
    assert new_run_dir(tmp_path, "r1") == new_run_dir(tmp_path, "r1")


def test_save_doctor_keeps_existing(tmp_path):
    (tmp_path / "doctor.txt").write_text("old", encoding="utf-8")
    p = save_doctor(tmp_path)
    assert p.read_text(encoding="utf-8") == "old"


def test_save_doctor_without_ros2(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    p = save_doctor(tmp_path)
    assert p.name == "doctor.txt" and p.read_text(encoding="utf-8")


def test_env_info_uses_given_env():
    assert env_info(child_env({}))["ROS_DOMAIN_ID"] == "87"


def test_write_result_records_given_env(tmp_path):
    p = write_result(tmp_path, "E-1", "측정", {}, {}, env=child_env({}))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["ROS_DOMAIN_ID"] == "87"
