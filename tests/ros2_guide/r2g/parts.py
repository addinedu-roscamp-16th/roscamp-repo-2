"""Per-combination partial results, so an interrupted run can resume."""
import json
from pathlib import Path


def part_path(run_dir: Path, test_id: str, key: str) -> Path:
    return Path(run_dir) / "parts" / test_id / f"{key}.json"


def save_part(run_dir: Path, test_id: str, key: str, data: dict) -> Path:
    path = part_path(run_dir, test_id, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:  # "x": FileExistsError if present
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_part(run_dir: Path, test_id: str, key: str) -> dict | None:
    path = part_path(run_dir, test_id, key)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def done_keys(run_dir: Path, test_id: str) -> set[str]:
    folder = Path(run_dir) / "parts" / test_id
    if not folder.is_dir():
        return set()
    return {p.stem for p in folder.glob("*.json")}
