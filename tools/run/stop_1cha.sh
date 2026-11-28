#!/usr/bin/env bash
# tmuxp로 띄운 1차 연동 세션을 끈다: 창마다 Ctrl+C를 보내고, 셸로 돌아올 때까지(최대 15초) 기다린 뒤 세션을 닫는다.
#   tools/run/stop_1cha.sh [세션 이름]        기본 iponic-local
#   TMUX_SOCKET=이름 tools/run/stop_1cha.sh …  tmux -L 이름으로 띄운 세션일 때
# kill-session만 하면 SIGHUP으로 바로 끊겨 브리지가 웹 · 노드 · DB 풀을 차례로 닫지 못하므로 Ctrl+C를 먼저 보낸다.
set -u
SESSION=${1:-iponic-local}
T=(tmux)
[ -n "${TMUX_SOCKET:-}" ] && T=(tmux -L "$TMUX_SOCKET")

if ! "${T[@]}" has-session -t "=$SESSION" 2>/dev/null; then
  echo "세션 $SESSION 없음"; exit 0
fi

# 셸이 아닌 것을 돌리고 있는 창(브리지 · central · fleet 등)에만 Ctrl+C
busy_panes() {
  "${T[@]}" list-panes -s -t "=$SESSION" -F '#{pane_id} #{pane_current_command}' \
    | awk '$2 != "bash" && $2 != "zsh" && $2 != "sh" {print $1}'
}
for p in $(busy_panes); do "${T[@]}" send-keys -t "$p" C-c; done

for _ in $(seq 1 15); do
  [ -z "$(busy_panes)" ] && break
  sleep 1
done
left=$(busy_panes)
if [ -n "$left" ]; then
  echo "15초 안에 안 꺼진 창이 있어 Ctrl+C를 한 번 더 보냄: $left"
  for p in $left; do "${T[@]}" send-keys -t "$p" C-c; done
  sleep 3
fi
"${T[@]}" kill-session -t "=$SESSION"
echo "세션 $SESSION 닫음"
