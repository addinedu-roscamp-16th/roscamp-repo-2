# tmux 창 하나를 1차 연동용으로 준비한다 (실행하지 않고 source).
#   source tools/run/pane_env.sh <bridge|ros|super> <local|real>
#
#   bridge : ROS → install → 가상환경(~/venv/iponic) → net_ds.sh  (브리지 · 시험 도구 창)
#   ros    : ROS → install → net_ds.sh                          (central · fleet 창)
#   super  : ros와 같고 실물 망이면 super client                  (ros2 topic echo 확인 창)
#   local  : 혼자 시험 — 도메인 $IPONIC_MY_DOMAIN + LOCALHOST, 서버 없음
#   real   : 실물 망 — 도메인 6, 디스커버리 서버(net_ds.sh의 DS_FLEET)
#
# tmux 서버는 처음 켤 때의 환경 변수를 오래 쥐고 있으므로, 창마다 이 파일로 통신 설정을 새로 한다.
# 필요한 값: IPONIC_WS(colcon 작업 공간), local이면 IPONIC_MY_DOMAIN. IPONIC_REPO는 없으면 이 파일 위치로 정함.
# 설명: docs/1차_연동_실행_안내.html "원격 PC에서 tmux로 띄우기"

_pe_role=${1:-}
_pe_net=${2:-}
case "$_pe_role" in bridge|ros|super) ;; *) echo "pane_env.sh: 첫 인자는 bridge · ros · super 중 하나" >&2; unset _pe_role _pe_net; return 1 ;; esac
case "$_pe_net" in local|real) ;; *) echo "pane_env.sh: 둘째 인자는 local · real 중 하나" >&2; unset _pe_role _pe_net; return 1 ;; esac

# ---- 경로 ----
: "${IPONIC_REPO:=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export IPONIC_REPO
if [ -z "${IPONIC_WS:-}" ] || [ ! -f "$IPONIC_WS/install/setup.bash" ]; then
  echo "pane_env.sh: IPONIC_WS(colcon 작업 공간, install/setup.bash가 있는 곳)를 export하세요. 지금: '${IPONIC_WS:-}'" >&2
  unset _pe_role _pe_net; return 1
fi

# ---- ROS → install → (가상환경) ----
source /opt/ros/jazzy/setup.bash && source "$IPONIC_WS/install/setup.bash" || { unset _pe_role _pe_net; return 1; }
if [ "$_pe_role" = bridge ]; then
  source ~/venv/iponic/bin/activate || { echo "pane_env.sh: ~/venv/iponic이 없습니다(개발 규칙 '주문 처리(브리지) 개발 환경')" >&2; unset _pe_role _pe_net; return 1; }
fi

# ---- 통신 설정 (net_ds.sh가 잘못된 조합을 막음) ----
_pe_super=0
[ "$_pe_role" = super ] && _pe_super=1
if [ "$_pe_net" = local ]; then
  if [ -z "${IPONIC_MY_DOMAIN:-}" ]; then
    echo "pane_env.sh: 혼자 시험은 IPONIC_MY_DOMAIN(개인 도메인 번호, 6 아님)을 export하세요" >&2
    unset _pe_role _pe_net _pe_super; return 1
  fi
  FLEET_DOMAIN_ID=$IPONIC_MY_DOMAIN FLEET_LOCAL=1 source "$IPONIC_REPO/tools/net/net_ds.sh" || { unset _pe_role _pe_net _pe_super; return 1; }
else
  SUPER=$_pe_super source "$IPONIC_REPO/tools/net/net_ds.sh" || { unset _pe_role _pe_net _pe_super; return 1; }
fi

cd "$IPONIC_REPO" || true
echo "[$_pe_role · $_pe_net] 도메인 $ROS_DOMAIN_ID, 서버 '${ROS_DISCOVERY_SERVER:-없음}'${ROS_SUPER_CLIENT:+, super client}"
unset _pe_role _pe_net _pe_super
