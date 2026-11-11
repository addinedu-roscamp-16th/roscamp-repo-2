#!/usr/bin/env bash
# Stage 2 evening batch: preflight, then L-01-W, L-02, L-04, G-11 under one run ID.
# Usage: PI_HOST=sbt-pi run_stage2.sh [--run-id ID] [--resume] [--only L-01-W,L-02,L-04,G-11] [--quick]
# PI_HOST defaults to "local" (one PC, results are not official).
# --resume skips finished combos and re-runs failed ones (moved aside as *.failed-<time>.json).
# Exit code: 2 refused to start (reused run ID, bad option), 3 preflight failed, 1 a runner
# crashed or refused; 실패 or 판정 불가 verdicts never change it. Read the summary or the JSON files.
set -euo pipefail

PI_HOST="${PI_HOST:-local}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RESUME=0
QUICK=0
ONLY="L-01-W,L-02,L-04,G-11"
USAGE="usage: PI_HOST=host $0 [--run-id ID] [--resume] [--only L-01-W,L-02,L-04,G-11] [--quick]"

while [ $# -gt 0 ]; do
    case "$1" in
        --run-id) RUN_ID="${2:?--run-id needs a value}"; shift 2 ;;
        --resume) RESUME=1; shift ;;
        --only) ONLY="${2:?--only needs a value}"; shift 2 ;;
        --quick) QUICK=1; shift ;;
        *) echo "$USAGE" >&2; exit 2 ;;
    esac
done

IFS=',' read -r -a ONLY_LIST <<< "$ONLY"
for name in "${ONLY_LIST[@]}"; do
    case "$name" in
        L-01-W|L-02|L-04|G-11) ;;
        *) echo "알 수 없는 시험 '$name' ($ONLY). $USAGE" >&2; exit 2 ;;
    esac
done
want() {
    local n
    for n in "${ONLY_LIST[@]}"; do [ "$n" = "$1" ] && return 0; done
    return 1
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_ROOT="$HERE/results"
RUN_DIR="$OUT_ROOT/$RUN_ID"

if [ "$RESUME" -eq 0 ]; then
    if compgen -G "$RUN_DIR/L-01-W-*.json" > /dev/null \
        || [ -e "$RUN_DIR/L-02.json" ] || [ -e "$RUN_DIR/L-04.json" ] \
        || [ -e "$RUN_DIR/G-11.json" ] || [ -e "$RUN_DIR/parts" ]; then
        echo "실행 ID '$RUN_ID'에 이미 결과가 있습니다($RUN_DIR). 다른 --run-id를 쓰세요." >&2
        exit 2
    fi
fi

if [ -z "${ROS_DISTRO:-}" ]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/jazzy/setup.bash
    set -u
fi

if [ "$QUICK" -eq 1 ]; then
    L01_TRIALS=3; L02_SECONDS=5; L02_REPEATS=1; L04_SECONDS=20; G11_OBSERVE=3
else
    L01_TRIALS=50; L02_SECONDS=30; L02_REPEATS=3; L04_SECONDS=120; G11_OBSERVE=10
fi
RESUME_ARGS=()
if [ "$RESUME" -eq 1 ]; then RESUME_ARGS=(--resume); fi
COMMON=(--pi-host "$PI_HOST" --out-root "$OUT_ROOT" --run-id "$RUN_ID")

echo "== 2단계 실행 ID $RUN_ID (PI_HOST=$PI_HOST, 시험: $ONLY$([ "$QUICK" -eq 1 ] && echo ', quick')) =="
if [ "$PI_HOST" = "local" ]; then
    echo "경고: PI_HOST=local은 장비 1대라 공식 판정이 아닙니다." >&2
fi

preflight_rc=0
python3 "$HERE/stage2_preflight.py" --pi-host "$PI_HOST" --out-root "$OUT_ROOT" \
    --run-id "$RUN_ID" || preflight_rc=$?
if [ "$preflight_rc" -ne 0 ]; then
    echo "사전 점검 실패(종료 코드 $preflight_rc): $RUN_DIR/preflight.json 확인" >&2
    if [ "$preflight_rc" -eq 3 ]; then exit 3; fi
    exit "$preflight_rc"
fi

rc=0
if want L-01-W; then
    python3 "$HERE/run_l01.py" "${COMMON[@]}" --id-prefix L-01-W --trials "$L01_TRIALS" \
        "${RESUME_ARGS[@]}" || rc=1
fi
if want L-02; then
    python3 "$HERE/run_l02.py" "${COMMON[@]}" --seconds "$L02_SECONDS" --repeats "$L02_REPEATS" \
        "${RESUME_ARGS[@]}" || rc=1
fi
if want L-04; then
    python3 "$HERE/run_l04.py" "${COMMON[@]}" --seconds "$L04_SECONDS" \
        "${RESUME_ARGS[@]}" || rc=1
fi
if want G-11; then
    python3 "$HERE/run_g11.py" "${COMMON[@]}" --observe "$G11_OBSERVE" \
        "${RESUME_ARGS[@]}" || rc=1
fi

python3 "$HERE/stage2_preflight.py" --pi-host "$PI_HOST" --cleanup-only || true

echo "== 결과 요약 ($RUN_DIR) =="
python3 - "$HERE" "$RUN_DIR" "$ONLY" <<'PY' || echo "요약 실패: $RUN_DIR 의 JSON 파일을 직접 확인" >&2
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from r2g import summary  # noqa: E402

for line in summary.summary_lines(Path(sys.argv[2]), sys.argv[3].split(",")):
    print(line)
PY
exit "$rc"
