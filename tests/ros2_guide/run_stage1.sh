#!/usr/bin/env bash
# Stage 1 batch runner: L-01 (all variants) and L-03 under one run ID.
# Usage: run_stage1.sh [--trials N] [--seconds S] [--run-id ID]
set -euo pipefail

TRIALS=50
SECONDS_PER_COMBO=20
RUN_ID="$(date +%Y%m%d-%H%M%S)"

while [ $# -gt 0 ]; do
    case "$1" in
        --trials) TRIALS="${2:?--trials needs a value}"; shift 2 ;;
        --seconds) SECONDS_PER_COMBO="${2:?--seconds needs a value}"; shift 2 ;;
        --run-id) RUN_ID="${2:?--run-id needs a value}"; shift 2 ;;
        *) echo "usage: $0 [--trials N] [--seconds S] [--run-id ID]" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_ROOT="$HERE/results"
RUN_DIR="$OUT_ROOT/$RUN_ID"

if [ -z "${ROS_DISTRO:-}" ]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/jazzy/setup.bash
    set -u
fi

rc=0
python3 "$HERE/run_l01.py" --trials "$TRIALS" --out-root "$OUT_ROOT" --run-id "$RUN_ID" || rc=1
python3 "$HERE/run_l03.py" --seconds "$SECONDS_PER_COMBO" --repeats 5 --out-root "$OUT_ROOT" --run-id "$RUN_ID" || rc=1

echo "== 결과 요약 ($RUN_DIR) =="
python3 - "$RUN_DIR" <<'PY'
import json
import sys
from pathlib import Path

for path in sorted(Path(sys.argv[1]).glob("L-*.json")):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        print(f"{d['id']}: {d['result']}")
    except (OSError, ValueError, KeyError) as e:
        print(f"{path.stem}: 읽기 실패 ({e})")
PY
exit "$rc"
