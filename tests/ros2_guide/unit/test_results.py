import json

import pytest

from r2g.proc import child_env
from r2g.results import (
    UNKNOWN_COMMIT,
    env_info,
    existing_targets,
    new_run_dir,
    now_iso,
    samples_name,
    save_doctor,
    tool_commit,
    write_result,
    write_samples,
)


def test_write_and_refuse_overwrite(tmp_path):
    d = new_run_dir(tmp_path, "r1")
    p = write_result(d, "L-01-A", "측정", {"lost": 1}, {"trials": 50})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["id"] == "L-01-A" and data["run_id"] == "r1" and data["result"] == "측정"
    assert set(data["env"]) == {
        "ROS_DISTRO", "RMW_IMPLEMENTATION", "ROS_DOMAIN_ID", "ROS_AUTOMATIC_DISCOVERY_RANGE",
        "host",
    }
    assert data["tool_commit"] and data["started_at"] is None and data["written_at"]
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


def test_env_info_records_discovery_range():
    assert env_info(child_env({}))["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    assert env_info({})["ROS_AUTOMATIC_DISCOVERY_RANGE"] == ""


def test_write_result_records_started_at(tmp_path):
    t0 = now_iso()
    p = write_result(tmp_path, "E-2", "측정", {}, {}, started_at=t0)
    assert json.loads(p.read_text(encoding="utf-8"))["started_at"] == t0


def test_tool_commit_in_repo():
    c = tool_commit()
    assert c == UNKNOWN_COMMIT or (4 <= len(c) <= 40 and c.isalnum())


def test_tool_commit_without_git(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert tool_commit() == UNKNOWN_COMMIT == "알 수 없음"


def test_existing_targets(tmp_path):
    (tmp_path / "L-01-A.json").write_text("{}", encoding="utf-8")
    assert existing_targets(tmp_path, ["L-01-A.json", "L-01-B.json"]) == ["L-01-A.json"]
    assert existing_targets(tmp_path / "missing", ["L-01-A.json"]) == []


def test_write_samples(tmp_path):
    assert samples_name("L-03-규칙") == "L-03-규칙.samples.json"
    p = write_samples(tmp_path, "L-03-규칙", {"rep0": {"samples": [[1, 2]]}})
    assert p.name == "L-03-규칙.samples.json"
    assert json.loads(p.read_text(encoding="utf-8"))["rep0"]["samples"] == [[1, 2]]
    with pytest.raises(FileExistsError):
        write_samples(tmp_path, "L-03-규칙", {})
