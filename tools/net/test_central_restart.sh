#!/usr/bin/env bash
# central 재시작 · 디스커버리 서버 연결 시험 (설계 docs/superpowers/specs/2026-11-28-central-restart-design.md 7절).
# 한 PC에서 디스커버리 서버 두 대(127.0.0.1에서만 듣는다)와 시험 DB · 브리지 · central을 띄워 S1 ~ S4를 차례로 본다.
#   포트 11931(서버 0) · 11932(서버 1), 도메인 91 ~ 94(시나리오마다 하나, 6은 실물 망이라 쓰지 않음).
#   DB: 평소 시험 DB(ifp_test)를 지우지 않게 같은 서버의 iponic_restart_test를 쓴다(이름이 _test로 끝나야 setup_db가 지움).
#   웹 포트 8090. fleet은 띄우지 않으므로 넘긴 건은 끝나지 않고 남는다.
# 사전 조건: docker compose -f design/db/compose.yaml up -d (DB 컨테이너), iponic_ws 빌드 완료.
# 실행: tools/net/test_central_restart.sh [결과 폴더, 기본 ./out_central_restart]  (약 3~4분, 하나라도 실패면 종료 코드 1)
set +u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=$(mkdir -p "${1:-./out_central_restart}" && cd "${1:-./out_central_restart}" && pwd)
P0=11931; P1=11932
LIST="127.0.0.1:$P0;127.0.0.1:$P1"      # 순서 = 서버 번호
WEB_PORT=8090
DB_BASE=${IPONIC_TEST_DB_BASE:-postgresql://postgres:ifp_test@localhost:55432}
TEST_DSN="$DB_BASE/iponic_restart_test"
FAILED=0
PGIDS=()                                # 띄운 프로세스 그룹. EXIT에서 전부 끈다

# ---- 환경: ROS -> install -> venv 순서 ----
source /opt/ros/jazzy/setup.bash
source "${IPONIC_WS:-$HOME/iponic_ws}/install/setup.bash"
source "$HOME/venv/iponic/bin/activate"

# 실물 망 · 이전 설정이 섞이지 않게 비운다
clean_env() { unset ROS_LOCALHOST_ONLY ROS_STATIC_PEERS ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE FASTDDS_ENVIRONMENT_FILE; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET; }

# ---- 프로세스 띄우기 · 끄기 (setsid로 그룹을 만들고 그룹째 끈다) ----
spawn() { # 로그이름 도메인 서버목록 명령... -> PGID를 SPAWNED에
  local name=$1 dom=$2 list=$3; shift 3
  ( clean_env; export ROS_DOMAIN_ID=$dom ROS_DISCOVERY_SERVER="$list" IPONIC_DB_DSN="$TEST_DSN" IPONIC_WEB_PORT=$WEB_PORT
    exec setsid "$@" >"$OUT/$name.log" 2>&1 ) &
  SPAWNED=$!; PGIDS+=("$SPAWNED")
}
stop_group() { kill -TERM -- "-$1" 2>/dev/null; sleep 1; kill -KILL -- "-$1" 2>/dev/null; }
stop_all() { local g; for g in "${PGIDS[@]}"; do stop_group "$g"; done; PGIDS=(); }
trap 'stop_all' EXIT

start_server() { # 번호 포트
  ( clean_env; exec setsid fastdds discovery -i "$1" -l 127.0.0.1 -p "$2" >"$OUT/server$1.log" 2>&1 ) &
  SPAWNED=$!; PGIDS+=("$SPAWNED")
}
start_central() { spawn "$1" "$DOM" "$LIST" ros2 run central central; CENTRAL=$SPAWNED; }
start_bridge()  { spawn "$1" "$DOM" "$LIST" python -m service_bridge.main --ros-args -p mode:=NORMAL; BRIDGE=$SPAWNED; }

# ---- 관찰: super client로 토픽 한 번 읽기 ----
echo_once() { # 도메인 토픽 [옵션...] -> 표준출력
  local dom=$1 topic=$2; shift 2
  ( clean_env; export ROS_DOMAIN_ID=$dom ROS_DISCOVERY_SERVER="$LIST" ROS_SUPER_CLIENT=TRUE
    timeout 12 ros2 topic echo --once --no-daemon "$@" "$topic" 2>/dev/null )
}
central_state() { echo_once "$DOM" /central/state --field state | head -1 | tr -d "'\r"; }
central_boot()  { echo_once "$DOM" /central/state --field boot_id | head -1 | tr -d "'\r"; }
wait_state() { # 기대값 최대횟수 -> 0이면 도달 (1초 간격)
  local want=$1 n=$2 s
  while [ "$n" -gt 0 ]; do s=$(central_state); [ "$s" = "$want" ] && return 0; n=$((n-1)); sleep 1; done
  return 1
}

# ---- DB ----
db_scalar() { timeout 10 docker exec -e PGPASSWORD=ifp_test db-pg-1 psql -U postgres -d iponic_restart_test -Atc "$1" 2>/dev/null; }
wait_db() { # SQL(숫자 1 이상이면 통과) 최대횟수
  local n=$2 v
  while [ "$n" -gt 0 ]; do v=$(db_scalar "$1"); [ "${v:-0}" -ge 1 ] 2>/dev/null && return 0; n=$((n-1)); sleep 1; done
  return 1
}
reset_db() { # 계정 · 상품 id를 ACCOUNT, PRODUCT에
  local line
  line=$(IPONIC_DB_DSN="$TEST_DSN" python -m service_bridge.tools.setup_db 2>"$OUT/setup_db.err") || return 1
  echo "$line" >>"$OUT/setup_db.log"
  ACCOUNT=$(echo "$line" | sed -E 's/.*계정 ([0-9]+).*/\1/')
  PRODUCT=$(echo "$line" | sed -E 's/.*상품 \{[^:]*: *([0-9]+).*/\1/')
}
place_order() { python -m service_bridge.tools.order --account "$ACCOUNT" --items "$PRODUCT:1" --url "http://localhost:$WEB_PORT" >>"$OUT/orders.log" 2>&1; }

# ---- 판정 출력 ----
pass() { echo "$1 통과"; }
fail() { echo "$1 실패: $2"; FAILED=1; }

# ---- 시나리오 공통 준비/정리 ----
begin() { # 이름 도메인
  DOM=$2; echo "== $1"; stop_all; start_server 0 $P0; start_server 1 $P1; sleep 2; reset_db || return 1
}

# ---- 사전 점검 ----
ports_free() { ! ss -uln | grep -qE ":($P0|$P1)\b"; }
ports_free || { echo "포트 $P0/$P1 사용 중, 중단"; exit 1; }
ss -ltn | grep -qE ":$WEB_PORT\b" && { echo "웹 포트 $WEB_PORT 사용 중, 중단"; exit 1; }
docker exec -e PGPASSWORD=ifp_test db-pg-1 psql -U postgres -Atc 'select 1' >/dev/null 2>&1 \
  || { echo "DB 컨테이너가 없습니다: docker compose -f design/db/compose.yaml up -d"; exit 1; }
cd "$REPO" || exit 1

# ---- S1 브리지 먼저, central 나중 ----
s1() {
  begin "S1 브리지 먼저 켜고 central 나중" 91 || { fail S1 "DB 준비"; return; }
  start_bridge s1.bridge; sleep 6
  start_central s1.central
  wait_state RUNNING 40 || { fail S1 "central이 RUNNING이 안 됨(마지막 $(central_state))"; return; }
  place_order
  wait_db "select count(*) from trip_dispatch where central_acked_at is not null" 30 \
    || { fail S1 "넘기기 확정(central_acked_at) 없음"; return; }
  pass S1
}

# ---- S2 central 먼저, 브리지 나중 ----
s2() {
  begin "S2 central 먼저 켜고 브리지 나중" 92 || { fail S2 "DB 준비"; return; }
  start_central s2.central; sleep 8
  local s; s=$(central_state)
  [ "$s" = CONNECTING ] || { fail S2 "브리지 없이 CONNECTING이 아님($s)"; return; }
  start_bridge s2.bridge
  wait_state RUNNING 40 || { fail S2 "브리지를 켠 뒤 RUNNING이 안 됨(마지막 $(central_state))"; return; }
  pass S2
}

# ---- S3 끝나지 않은 건 2개, central만 다시 켬 ----
s3() {
  begin "S3 끝나지 않은 건 2개가 있을 때 central만 다시 켬" 93 || { fail S3 "DB 준비"; return; }
  start_bridge s3.bridge; sleep 6; start_central s3.central
  wait_state RUNNING 40 || { fail S3 "처음 RUNNING이 안 됨"; return; }
  place_order       # 건 1: central 확정
  wait_db "select count(*) from trip_dispatch where central_acked_at is not null" 30 || { fail S3 "건 1 확정 안 됨"; return; }
  local boot1; boot1=$(central_boot)
  [ -n "$boot1" ] || { fail S3 "처음 boot_id를 못 읽음"; return; }
  local n0; n0=$(db_scalar "select count(*) from alert where code='CENTRAL_RESTART'")
  [ "$n0" = 0 ] || { fail S3 "끄기 전에 이미 CENTRAL_RESTART가 ${n0}건"; return; }
  stop_group "$CENTRAL"; sleep 5
  place_order       # 건 2: central이 없어 미확정
  sleep 2
  start_central s3.central2
  wait_state RUNNING 40 || { fail S3 "다시 켠 뒤 RUNNING이 안 됨(마지막 $(central_state))"; return; }
  local boot2; boot2=$(central_boot)
  [ -n "$boot2" ] && [ "$boot2" != "$boot1" ] || { fail S3 "boot_id가 안 바뀜($boot1 -> $boot2)"; return; }
  local board db
  board=$(echo_once "$DOM" /central/orders | grep -E '^- order_id:|^  order_id:|^- order_id' | sed -E "s/.*order_id: *//; s/['\"]//g" | tr '\n' ' ')
  db=$(db_scalar "select string_agg(trip_id, ' ' order by created_at, trip_id) from trip" | sed 's/$/ /')
  [ "$(echo "$db" | wc -w)" = 2 ] || { fail S3 "DB trip이 2건이 아님($db)"; return; }
  [ "$board" = "$db" ] || { fail S3 "주문판 순서 [$board] != DB 순서 [$db]"; return; }
  wait_db "select count(*) from alert where code='CENTRAL_RESTART'" 15 || { fail S3 "CENTRAL_RESTART 알림 없음"; return; }
  sleep 5   # 더 쓰이는 알림이 없는지 보려고 기다린 뒤 다시 읽는다
  local n; n=$(db_scalar "select count(*) from alert where code='CENTRAL_RESTART'")
  [ "$n" = 1 ] || { fail S3 "CENTRAL_RESTART가 재시작 뒤 $n건(1건이어야 함)"; return; }
  # 건 2(미확정)가 새 central에 확정될 때까지 기다린 뒤 멈춤 알림과 시도 수를 본다
  wait_db "select count(*) from trip_dispatch d join trip t using(trip_id) where d.central_acked_at is not null and t.created_at = (select max(created_at) from trip)" 40 \
    || { fail S3 "건 2가 새 central에 확정되지 않음"; return; }
  n=$(db_scalar "select count(*) from alert where code='DISPATCH_STALLED' and status='OPEN'")
  [ "$n" = 0 ] || { fail S3 "열린 DISPATCH_STALLED가 $n건"; return; }
  n=$(db_scalar "select max(d.attempts) from trip_dispatch d join trip t using(trip_id) where t.created_at = (select max(created_at) from trip)")
  [ "${n:-99}" -lt 5 ] || { fail S3 "건 2 attempts=$n (5 미만이어야 함)"; return; }
  pass S3
}

# ---- S4 서버 0을 끈 뒤 central만 다시 켬 ----
s4() {
  begin "S4 서버 0을 끈 뒤 central만 다시 켬" 94 || { fail S4 "DB 준비"; return; }
  local srv0=${PGIDS[0]}
  start_bridge s4.bridge; sleep 6; start_central s4.central
  wait_state RUNNING 40 || { fail S4 "처음 RUNNING이 안 됨"; return; }
  stop_group "$srv0"; echo "  (서버 0 끔)"; sleep 2
  stop_group "$CENTRAL"; sleep 2
  start_central s4.central2
  wait_state RUNNING 60 || { fail S4 "서버 1만으로 RUNNING이 안 됨(마지막 $(central_state))"; return; }
  pass S4
}

s1; s2; s3; s4
stop_all; sleep 1
ports_free || { echo "경고: 서버 포트가 남음"; FAILED=1; }
[ "$FAILED" = 0 ] && echo "전체 통과" || echo "실패 있음 (로그: $OUT)"
exit "$FAILED"
