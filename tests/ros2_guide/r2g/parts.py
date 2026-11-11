"""Per-combination partial results, so an interrupted run can resume."""
import json
import os
import tempfile
import time
from pathlib import Path

FAILED_TAG = ".failed-"


def part_path(run_dir: Path, test_id: str, key: str) -> Path:
    return Path(run_dir) / "parts" / test_id / f"{key}.json"


def save_part(run_dir: Path, test_id: str, key: str, data: dict) -> Path:
    """Write via a temp file in the same folder + os.replace; refuse an existing part."""
    path = part_path(run_dir, test_id, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(str(path))
    fd, tmp = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            raise FileExistsError(str(path))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def _read(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_part(run_dir: Path, test_id: str, key: str) -> dict | None:
    """The saved part, or None when it is missing, unreadable or not a JSON object."""
    return _read(part_path(run_dir, test_id, key))


def _part_files(run_dir: Path, test_id: str) -> list[Path]:
    folder = Path(run_dir) / "parts" / test_id
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if FAILED_TAG not in p.name)


def done_keys(run_dir: Path, test_id: str) -> set[str]:
    """Keys with a readable part. Retired (`.failed-`) and corrupt files do not count."""
    return {p.stem for p in _part_files(run_dir, test_id) if _read(p) is not None}


def retire_file(path: Path) -> Path:
    """Rename `<stem>.json` to `<stem>.failed-<YYYYmmdd-HHMMSS>[-n].json`; returns the new path."""
    path = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    new = path.with_name(f"{path.stem}{FAILED_TAG}{stamp}{path.suffix}")
    n = 1
    while new.exists():
        n += 1
        new = path.with_name(f"{path.stem}{FAILED_TAG}{stamp}-{n}{path.suffix}")
    os.replace(path, new)
    return new


def failed_keys(run_dir: Path, test_id: str) -> list[str]:
    """Keys whose part has ok == False or is unreadable (retired files excluded)."""
    keys = []
    for p in _part_files(run_dir, test_id):
        data = _read(p)
        if data is None or data.get("ok") is False:
            keys.append(p.stem)
    return keys


def retire_failed(run_dir: Path, test_id: str) -> list[str]:
    """Move parts with ok == False (or unreadable) aside so --resume runs them again."""
    keys = failed_keys(run_dir, test_id)
    for key in keys:
        retire_file(part_path(run_dir, test_id, key))
    return keys


def retire_for_resume(run_dir: Path, test_id: str) -> list[str]:
    """retire_failed; when something was retired, the stale `<test_id>.json` goes aside too
    so the runner writes a new result from the re-run parts."""
    retired = retire_failed(run_dir, test_id)
    final = Path(run_dir) / f"{test_id}.json"
    if retired and final.exists():
        retire_file(final)
    return retired
