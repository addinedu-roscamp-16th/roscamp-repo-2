"""End-of-batch summary for run_stage2.sh: one line per expected stage 2 result file."""
import json
from collections.abc import Iterable
from pathlib import Path

from r2g import parts

W_PREFIX = "L-01-W"
W_VARIANTS = ("A", "B", "C", "F", "D", "E-VOL", "E-TL", "S")  # = run_l01.W_ORDER
PART_TESTS = (("L-02", "조합"), ("L-04", "조합"), ("G-11", "방향"))


def expected_ids(only: Iterable[str]) -> list[str]:
    only = set(only)
    ids = [f"{W_PREFIX}-{v}" for v in W_VARIANTS] if W_PREFIX in only else []
    return ids + [t for t, _ in PART_TESTS if t in only]


def _risk_text(obs: dict) -> str:
    risk = obs.get("risk")
    if risk is True:
        return "통합 운영 위험: 있음"
    if risk is False:
        return "통합 운영 위험: 없음"
    combos = obs.get("combos") or {}
    n = sum(1 for v in combos.values() if not isinstance(v, dict) or v.get("heartbeat") is None)
    return f"통합 운영 위험: 확인 못 한 조합 {n}"


def summary_lines(run_dir: Path, only: Iterable[str]) -> list[str]:
    run_dir = Path(run_dir)
    units = dict(PART_TESTS)
    lines = []
    for test_id in expected_ids(only):
        path = run_dir / f"{test_id}.json"
        if not path.exists():
            lines.append(f"{test_id}: 결과 파일 없음")
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            obs = d.get("observations") or {}
            result = d["result"]
        except (OSError, ValueError, KeyError, AttributeError) as e:
            lines.append(f"{test_id}: 읽기 실패 ({e!r})")
            continue
        extra = []
        if test_id.startswith(f"{W_PREFIX}-"):
            n = len(obs.get("excluded_trials") or [])
            if n:
                extra.append(f"제외된 시도 {n}건")
        else:
            n = len(parts.failed_keys(run_dir, test_id))
            if n:
                extra.append(f"실패한 {units[test_id]} {n}개")
        if test_id == "L-04":
            extra.append(_risk_text(obs))
        lines.append(f"{test_id}: {result}" + (f" ({', '.join(extra)})" if extra else ""))
    return lines
