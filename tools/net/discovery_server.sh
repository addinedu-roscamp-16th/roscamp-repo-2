#!/usr/bin/env bash
# Fast DDS 디스커버리 서버를 앞에서 돌린다 (Ctrl+C로 끔). 부팅 때 켜려면 iponic-discovery@.service.
#   discovery_server.sh 0            fleet(관제) PC, 포트 11811
#   discovery_server.sh 1            central PC, 포트 11811
#   discovery_server.sh <번호> <포트>  통합 시험용 서버 (포트는 Q-18)
# 번호는 net_ds.sh의 서버 목록 순서와 같아야 한다. 주소를 주지 않아 모든 망(유선 · Wi-Fi)에서 듣는다.
set -e
id=${1:?사용법: discovery_server.sh <서버 번호 0|1> [포트]}
port=${2:-11811}
set +u
source "${ROS_SETUP:-/opt/ros/jazzy/setup.bash}" || { echo "discovery_server.sh: ROS setup 없음" >&2; exit 1; }
# 서버 자신은 다른 서버의 클라이언트가 아니다
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
if ss -uln | grep -q ":$port\b"; then
  echo "discovery_server.sh: 포트 $port 이미 사용 중 (서버가 켜져 있을 수 있음)" >&2; exit 1
fi
exec fastdds discovery -i "$id" -p "$port"
