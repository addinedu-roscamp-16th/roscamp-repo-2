# 통신 설정. 노드를 띄우는 터미널마다 source한다 (실행하지 않고 source).
# 설명: docs/설계/디스커버리_서버_안내.md · 규칙: 통신 규칙 IR-81 · IR-82
#
#   source net_ds.sh                                          실물 망: 도메인 6, 디스커버리 서버
#   FLEET_DOMAIN_ID=내번호 FLEET_LOCAL=1 source net_ds.sh       혼자 하는 개발 · 시험: 서버 없이 내 PC 안에서만
#   FLEET_DS='<서버 목록>' FLEET_DOMAIN_ID=시험번호 source net_ds.sh   여러 PC 통합 시험 (서버 포트는 Q-18)
#   SUPER=1 source net_ds.sh                                  ros2 명령 · RViz 터미널 (앞의 값과 같이 붙여도 됨)
#
# 값은 같은 줄에서 source 앞에 붙인다. export하면 셸에 남아 다음 source에도 적용된다.

# ---- 실물 망 서버 (순서 = 서버 번호. 모든 장치가 같아야 함, 여기만 고친다) ----
DS_FLEET=192.168.0.252:11811   # 0번: fleet(관제) PC
DS_CENTRAL=                    # 1번: central PC (주소 미정, Q-20). 정해지면 IP:11811

source /opt/ros/jazzy/setup.bash || return 1
# 다시 source해도 이전 값이 남지 않게 먼저 지운다
unset ROS_LOCALHOST_ONLY ROS_STATIC_PEERS ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=${FLEET_DOMAIN_ID:-6}
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
if [ "${FLEET_LOCAL:-0}" = 1 ]; then
  export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
  if [ "$ROS_DOMAIN_ID" = 6 ]; then
    echo "net_ds.sh: 개인 작업은 도메인 6을 쓰지 않습니다. FLEET_DOMAIN_ID=내번호를 붙이세요" >&2
  fi
else
  export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
  export ROS_DISCOVERY_SERVER=${FLEET_DS:-$DS_FLEET${DS_CENTRAL:+;$DS_CENTRAL}}
  if [ "${SUPER:-0}" = 1 ]; then export ROS_SUPER_CLIENT=TRUE; fi
fi
if [ -n "${FASTRTPS_DEFAULT_PROFILES_FILE:-}${FASTDDS_DEFAULT_PROFILES_FILE:-}" ]; then
  echo "net_ds.sh: Fast DDS XML 설정 변수가 있습니다. 디스커버리 설정을 덮을 수 있습니다" >&2
fi
unset DS_FLEET DS_CENTRAL
