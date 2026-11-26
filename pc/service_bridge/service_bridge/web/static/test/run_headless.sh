#!/usr/bin/env bash
# ---- 시험 페이지를 headless chromium으로 돌려 결과를 보임 (실패 0이면 종료 0) ----
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1])')"
timeout 60 python3 -m http.server "$PORT" -d "$DIR" >/dev/null 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1
OUT="$(timeout 60 /snap/bin/chromium --headless=new --no-sandbox --disable-gpu \
  --virtual-time-budget=5000 --dump-dom "http://localhost:$PORT/test/" 2>/dev/null)"
RES="$(echo "$OUT" | sed 's/<[^>]*>//g' | grep -E '^(결과|실패):')"
echo "$RES"
echo "$RES" | grep -qE '^결과: 통과 [0-9]+ · 실패 0$'
