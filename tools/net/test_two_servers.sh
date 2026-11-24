#!/usr/bin/env bash
# 디스커버리 서버 두 대(이 PC, 127.0.0.1) 시험. 실물 망(도메인 6, 192.168.0.252)과 섞이지 않게
# 서버는 127.0.0.1에서만 듣고, 포트 11921/11922, 도메인 79 ~ 89를 쓴다.
# 결과 해석: docs/설계/디스커버리_서버_안내.md 8절. 실행: tools/net/test_two_servers.sh [결과 폴더] (약 2분)
set +u
source /opt/ros/jazzy/setup.bash
OUT=${1:-./out}; mkdir -p "$OUT"
P0=11921; P1=11922
LIST="127.0.0.1:$P0;127.0.0.1:$P1"      # 순서 = 서버 번호 (0: fleet 역할, 1: central 역할)
SWAP="127.0.0.1:$P1;127.0.0.1:$P0"
clean_env() { unset ROS_LOCALHOST_ONLY ROS_STATIC_PEERS ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET; }
start_server() { # id port -> pgid in var
  ( clean_env; exec setsid fastdds discovery -i "$1" -l 127.0.0.1 -p "$2" >"$OUT/server$1.log" 2>&1 ) &
  echo $!
}
stop_server() { kill -TERM -- "-$1" 2>/dev/null; sleep 1; kill -KILL -- "-$1" 2>/dev/null; }
run_pair() { # name domain talker_list listener_list [kill_pgid_after_s]
  local n=$1 d=$2 tl=$3 ll=$4 kp=${5:-}
  ( clean_env; export ROS_DOMAIN_ID=$d ROS_DISCOVERY_SERVER="$ll"; exec timeout 16 ros2 run demo_nodes_cpp listener >"$OUT/$n.listener.log" 2>&1 ) &
  local lp=$!
  sleep 2
  ( clean_env; export ROS_DOMAIN_ID=$d ROS_DISCOVERY_SERVER="$tl"; exec timeout 12 ros2 run demo_nodes_cpp talker >"$OUT/$n.talker.log" 2>&1 ) &
  local tp=$!
  if [ -n "$kp" ]; then sleep 4; stop_server "$kp"; echo "  ($n: 4초 뒤 서버 0 끔)"; fi
  wait $tp; wait $lp
  local sent heard
  sent=$(grep -c 'Publishing' "$OUT/$n.talker.log"); heard=$(grep -c 'I heard' "$OUT/$n.listener.log")
  printf '%-34s 보냄 %2s  받음 %2s\n' "$n" "$sent" "$heard"
}
ports_free() { ! ss -uln | grep -qE ":($P0|$P1)\b"; }
ports_free || { echo "포트 사용 중, 중단"; exit 1; }

echo "== C1 서버 둘 다 켬, 정상 순서"
S0=$(start_server 0 $P0); S1=$(start_server 1 $P1); sleep 2
run_pair C1_both_up 81 "$LIST" "$LIST"

echo "== C2 서버 0 끈 뒤 새로 띄운 노드 (서버 1만 살아 있음)"
stop_server "$S0"; sleep 1
run_pair C2_only_server1 82 "$LIST" "$LIST"

echo "== C3 서버 1 끄고 서버 0만 켬"
stop_server "$S1"; S0=$(start_server 0 $P0); sleep 2
run_pair C3_only_server0 83 "$LIST" "$LIST"

echo "== C4 서버 둘 다 켬, listener만 순서 뒤집음"
S1=$(start_server 1 $P1); sleep 2
run_pair C4_listener_swapped 84 "$LIST" "$SWAP"

echo "== C5 서버 둘 다 켬, 통신 중 서버 0 끔 (이미 찾은 노드)"
run_pair C5_kill_server0_midway 85 "$LIST" "$LIST" "$S0"

S0=$(start_server 0 $P0); sleep 2
echo "== C6 서버 둘 다 켬, talker는 서버 1만 · listener는 서버 0만 앎 (공통 서버 없음)"
run_pair C6_disjoint_both_up 86 ";127.0.0.1:$P1" "127.0.0.1:$P0"

echo "== C7 super client가 전체 그래프를 보나 (서버 둘 다)"
( clean_env; export ROS_DOMAIN_ID=87 ROS_DISCOVERY_SERVER="$LIST"; exec timeout 14 ros2 run demo_nodes_cpp talker >"$OUT/C7.talker.log" 2>&1 ) & tp=$!
sleep 4
plain=$( (clean_env; export ROS_DOMAIN_ID=87 ROS_DISCOVERY_SERVER="$LIST"; timeout 8 ros2 topic list --no-daemon 2>&1) | grep -c chatter)
super=$( (clean_env; export ROS_DOMAIN_ID=87 ROS_DISCOVERY_SERVER="$LIST" ROS_SUPER_CLIENT=TRUE; timeout 8 ros2 topic list --no-daemon 2>&1) | grep -c chatter)
wait $tp
printf '%-34s 보통 노드 /chatter %s  super client /chatter %s\n' C7_topic_list "$plain" "$super"

echo "== C8 · C9 한쪽은 서버 0만 아는 옛 설정 (이미 배포된 장치)"
run_pair C8_listener_old_single_server0 88 "$LIST" "127.0.0.1:$P0"
run_pair C9_talker_old_single_server0 89 "127.0.0.1:$P0" "$LIST"

echo "== C10 통신 중 서버 두 대 모두 끔 (이미 찾은 노드)"
( clean_env; export ROS_DOMAIN_ID=80 ROS_DISCOVERY_SERVER="$LIST"; exec timeout 16 ros2 run demo_nodes_cpp listener >"$OUT/C10.listener.log" 2>&1 ) & lp=$!
sleep 2
( clean_env; export ROS_DOMAIN_ID=80 ROS_DISCOVERY_SERVER="$LIST"; exec timeout 12 ros2 run demo_nodes_cpp talker >"$OUT/C10.talker.log" 2>&1 ) & tp=$!
sleep 4; stop_server "$S0"; stop_server "$S1"; echo "  (C10: 4초 뒤 서버 0 · 1 모두 끔)"
wait $tp; wait $lp
printf '%-34s 보냄 %2s  받음 %2s  마지막 받은 것: %s\n' C10_kill_both_midway "$(grep -c Publishing "$OUT/C10.talker.log")" "$(grep -c 'I heard' "$OUT/C10.listener.log")" "$(grep 'I heard' "$OUT/C10.listener.log" | tail -1 | sed 's/.*\[Hello/[Hello/')"

echo "== C11 서버 두 대가 모두 꺼진 뒤 새로 띄운 노드"
run_pair C11_both_down_new_nodes 79 "$LIST" "$LIST"

sleep 1
ports_free && echo "정리: 서버 포트 비었음" || { echo "경고: 포트 남음"; ss -ulnp | grep -E ":($P0|$P1)\b"; }
