# ReportCommand 받는 쪽 · 취소 사유 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 브리지가 fleet의 `ReportCommand`를 받아 `admin_action.final_result`에 한 번만 확정하고 관리자 화면 사건 `command_result`를 내게 합니다. 함께 fleet 취소 보고에 사유 `NOTHING_TO_FILL`을 싣습니다.

**Architecture:** `ReportTrip` 처리와 같은 모양입니다. 판정과 DB 쓰기는 `service_bridge/core/commands.py`의 트랜잭션 하나(`run_tx_guarded`)에 두고, ROS 노드는 변환과 서버 콜백만 맡습니다. 화면 사건은 `ws_event` INSERT 트리거가 NOTIFY하고, `events.route`가 관리자에게 보냅니다.

**Tech Stack:** Python 3.12, ROS 2 Jazzy(rclpy), psycopg 3 + psycopg_pool, PostgreSQL 16, pytest

**Spec:** `docs/superpowers/specs/2026-11-27-report-command-receiver-design.md`

## Global Constraints

- 브랜치 `feat/TT-27-fleet-dev-merge-2026-11-27`, 고정 저장소 `/home/ane/dev_ws/roscamp-repo-2`(worktree 없음). 푸시하지 않음.
- `final_result` 값은 `ACCEPTED` · `REJECTED` · `UNCONFIRMED` · `TIMEOUT` 넷뿐(DB CHECK). 끝 상태는 `ACCEPTED` · `REJECTED` · `TIMEOUT`.
- 응답 `accepted=false`는 DB 오류뿐(`DB_ERROR`). 그 밖은 모두 `true`.
- 알림: `level='WARN'`, `code='LATE_COMMAND_RESULT'`, `source='fleet'`, `event_id = 'LATECMD:{request_id}:{result}'`, `ON CONFLICT (event_id) DO NOTHING`. `late_reason`은 `FINAL_ALREADY` · `NOT_FOUND` · `INVALID_RESULT`.
- 쓸 때 `final_result = result`, `reason`이 비어 있지 않으면 `result_detail = reason`.
- 사건 `command_result`의 `data` = `{request_id, command, target, result, reason, detail}`. `final_result`를 쓴 경우에만 넣음.
- 브리지가 `TIMEOUT`을 받았다고 따로 알림을 내지 않음.
- 서버 이름은 `contract.name("report_command")`(= `/bridge/report_command`).
- 취소 보고: `ReportTrip` 상태 `CANCELLED`, 사유 `NOTHING_TO_FILL`. HELD는 `hold_reason` 그대로.
- 문서 · 주석 · 커밋은 한국어. 커밋 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **주석은 구체적으로 씀** (사용자 요청 2026-11-27). 코드가 무엇을 하는지 되풀이하지 말고, 왜 그렇게 하는지와 근거를 적음.
  - 판정 분기마다 어떤 경우인지와 근거 번호를 씀. 예: `# 결과 모름(UNCONFIRMED) 뒤 끝 상태: 한 번만 확정 (IR-62, 설계 4.2 표 2번 줄)`
  - 값 · 상수에는 출처를 씀. 예: `# DB CHECK ck_admin_action_final_result와 같은 4개`
  - 일부러 하지 않는 일도 씀. 예: `# TIMEOUT 알림은 fleet 몫(W-31)이라 여기서 내지 않음`
  - 피할 것: "처리한다" · "확인한다" · "예외 처리"처럼 무엇을 왜 하는지 없는 주석, 함수 이름을 풀어 쓴 주석
  - 시험은 docstring 한 줄에 무엇을 막는 시험인지 씀. 예: `"""끝 상태 보고의 빈 reason이 접수 응답 때 쓴 거절 사유를 지우지 않는다."""`
  - 리뷰어는 이 기준으로 주석도 봄

## Review Focus

1. 끝 상태 보고의 `reason`이 비어 있음 → 접수 응답 때 쓴 `result_detail`을 지우지 않아야 함 (Task 2 `test_empty_reason_keeps_result_detail`).
2. 모르는 `request_id`가 여러 번 옴 → 알림 하나 (Task 2 `test_unknown_request_id_alerts_once`).
3. `request_id`가 빈 문자열 → `NOT_FOUND` 알림, 아무 행도 바꾸지 않음 (Task 2 `test_empty_request_id_is_not_found`).
4. 행이 없을 때 알림의 `robot_id`는 보고의 `target` (Task 2 `test_unknown_request_id_alerts_once`에서 함께 확인).
5. 같은 `request_id`의 보고 둘이 동시에 옴 → `FOR UPDATE`로 차례로 처리되어 끝 상태가 한 번만 정해짐. 시험으로 재현하지 않고 리뷰에서 잠금 쿼리를 확인.

## 파일

| 파일 | 할 일 |
|---|---|
| `pc/fleet_manager/fleet_manager/report_queue.py` | 취소 사유 (Task 1) |
| `pc/fleet_manager/test/test_report_queue.py` | 철자 시험 (Task 1) |
| `pc/service_bridge/service_bridge/core/types.py` | `CommandIn` · `CommandOut` (Task 2) |
| `pc/service_bridge/service_bridge/core/commands.py` | 새 파일: 판정 · 반영 (Task 2) |
| `pc/service_bridge/service_bridge/core/events.py` | `command_result` 보낼 곳 (Task 2) |
| `pc/service_bridge/test/test_commands.py` | 새 파일 (Task 2) |
| `pc/service_bridge/test/test_snapshot_events.py` | route 시험 (Task 2) |
| `pc/service_bridge/service_bridge/ros/convert.py` | `command_in` (Task 3) |
| `pc/service_bridge/service_bridge/ros/bridge_node.py` | 서버 · 콜백 (Task 3) |
| `pc/service_bridge/test/test_bridge_node.py` | 왕복 시험 (Task 3) |
| `docs/설계/인터페이스_명세.md` 3.1 · `docs/설계/설계_검토_사항.md` Q-07 · `pc/service_bridge/README.md` | 문서 (Task 3) |

시험 DB: `docker compose -f design/db/compose.yaml up -d`. 브리지 시험 명령은 `source /opt/ros/jazzy/setup.bash && source install/setup.bash && ~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q`.

---

### Task 1: 취소 보고에 사유 `NOTHING_TO_FILL`

**Files:**
- Modify: `pc/fleet_manager/fleet_manager/report_queue.py` (`_payload`)
- Test: `pc/fleet_manager/test/test_report_queue.py` (`test_order_status_is_spelled_for_the_bridge`)

**Interfaces:**
- Produces: `report_queue.NOTHING_TO_FILL = "NOTHING_TO_FILL"` 상수. `report_queue`는 ROS 없이 import되어야 하므로 `adapters`를 import하지 않음(설계 5장의 구현자 판단을 이 계획에서 정함).

- [ ] **Step 1: 철자 시험의 기대값을 바꿈**

```python
@pytest.mark.parametrize("status, hold, trip_status, reason", [
    (CANCELED, "", "CANCELLED", "NOTHING_TO_FILL"), (HELD, HOLD_ROBOT_FAULT, HELD, HOLD_ROBOT_FAULT)])
```

- [ ] **Step 2: 실패 확인**

Run: `source /opt/ros/jazzy/setup.bash && source install/setup.bash && python3 -m pytest pc/fleet_manager/test/test_report_queue.py -q -k spelled`
Expected: CANCELED 경우 FAIL (`'' != 'NOTHING_TO_FILL'`)

- [ ] **Step 3: `_payload`에서 `CANCELED`면 사유를 `NOTHING_TO_FILL`로, 그 밖은 `cmd.hold_reason`**

`adapters.trip_status`와 같은 규칙이라는 주석 한 줄을 남김.

- [ ] **Step 4: 통과 확인**

Run: 위와 같은 명령, 그다음 `python3 -m pytest pc/fleet_manager/test -q`
Expected: PASS

- [ ] **Step 5: 커밋** — `fix(fleet_manager): 취소 보고에 사유 NOTHING_TO_FILL (dev trip_status와 같은 규칙)`

---

### Task 2: `ReportCommand` 판정 · DB 반영 · 사건

**Files:**
- Modify: `pc/service_bridge/service_bridge/core/types.py`, `pc/service_bridge/service_bridge/core/events.py`
- Create: `pc/service_bridge/service_bridge/core/commands.py`, `pc/service_bridge/test/test_commands.py`
- Test: `pc/service_bridge/test/test_snapshot_events.py`

**Interfaces:**
- Produces:
  - `types.CommandIn(request_id: str, command: str, target: str, result: str, reason: str, detail: str, stamp: str = "")` (frozen dataclass)
  - `types.CommandOut(accepted: bool, result: str = "", reason: str = "")` — `result`는 `APPLIED` · `DUPLICATE` · `LATE`, `reason`은 `late_reason` 또는 `DB_ERROR`
  - `commands.apply_command(pool: ConnectionPool, rep: CommandIn, deadline: float) -> CommandOut`
  - `commands.FINAL = frozenset({"ACCEPTED", "REJECTED", "TIMEOUT"})`, `commands.RESULTS = FINAL | {"UNCONFIRMED"}`
  - `events.route("command_result", seq, data, ts)` → `[Outgoing("admin", None, envelope("command_result", data, ts, seq))]`

판정 순서(설계 4.2 표 그대로):
1. `result`가 `RESULTS` 밖 → 알림 `INVALID_RESULT`, `LATE`
2. `SELECT action_id, robot_id, final_result FROM admin_action WHERE request_id = %s FOR UPDATE` 없음 → 알림 `NOT_FOUND`, `LATE`
3. 지금 값 `NULL`, 또는 지금 `UNCONFIRMED`이고 받은 값이 끝 상태 → 씀 + `command_result` 사건, `APPLIED`
4. 받은 값 == 지금 값, 또는 받은 값 `UNCONFIRMED`이고 지금 끝 상태 → `DUPLICATE`
5. 그 밖(지금 끝 상태, 받은 값이 다른 끝 상태) → 알림 `FINAL_ALREADY`, `LATE`

알림 `robot_id`는 행의 `robot_id`가 있으면 그 값, 없으면 `rep.target`(빈 문자열이면 NULL). `detail` jsonb = `{**asdict(rep), "late_reason": why}`.

- [ ] **Step 1: 실패하는 시험을 씀** (`test_commands.py`, 픽스처 `pool, conn, seeded`)

시험용 행: `INSERT INTO admin_action (account_id, action, robot_id, request_id, result, result_detail) VALUES (seeded["account_id"], 'LOCK_RELEASE', 'pode1', 'R-1', 'SENT', %s)`.

| 시험 이름 | 확인 |
|---|---|
| `test_first_result_is_written_with_its_event` | `ACCEPTED` → `CommandOut(True, "APPLIED")`, `final_result='ACCEPTED'`, `command_result` 사건 1개, data가 `{request_id, command, target, result, reason, detail}` |
| `test_unconfirmed_then_final_is_written` | `UNCONFIRMED`(reason `ROBOT_TIMEOUT`) → `APPLIED`, 그다음 `REJECTED`(reason `ROBOT_REJECTED`) → `APPLIED`, `result_detail='ROBOT_REJECTED'`, 사건 2개 |
| `test_same_result_again_is_duplicate` | `ACCEPTED` 두 번 → 둘째 `DUPLICATE`, 사건 1개, 알림 0개 |
| `test_unconfirmed_after_final_is_duplicate` | `TIMEOUT` 뒤 `UNCONFIRMED` → `DUPLICATE`, `final_result='TIMEOUT'` |
| `test_other_final_after_final_alerts_once` | `ACCEPTED` 뒤 `REJECTED` 두 번 → 둘 다 `CommandOut(True, "LATE", "FINAL_ALREADY")`, `final_result='ACCEPTED'`, `LATE_COMMAND_RESULT` 알림 1개, `event_id='LATECMD:R-1:REJECTED'` |
| `test_unknown_request_id_alerts_once` | `R-NONE` 두 번 → `LATE`/`NOT_FOUND`, 알림 1개, 알림 `robot_id` = 보고 `target`(`arm_left`) |
| `test_empty_request_id_is_not_found` | `request_id=''` → `NOT_FOUND`, `admin_action` 행은 그대로 |
| `test_result_outside_the_four_alerts` | `result='DONE'` → `LATE`/`INVALID_RESULT`, `final_result`는 NULL 그대로 |
| `test_empty_reason_keeps_result_detail` | 행의 `result_detail='NOT_STOPPED'`, `ACCEPTED`(reason `''`) → `result_detail='NOT_STOPPED'` 그대로 |
| `test_timeout_raises_no_alert` | `TIMEOUT` → `APPLIED`, 알림 0개 |
| `test_db_error_is_not_accepted` | `test_reports.py`의 DB 오류 시험(132 ~ 138행)과 같은 닿지 않는 풀 → `CommandOut(False, reason="DB_ERROR")` |

`test_snapshot_events.py`에 `test_route_command_result_admin_only` 추가.

- [ ] **Step 2: 실패 확인**

Run: `docker compose -f design/db/compose.yaml up -d` 후 브리지 시험 명령에 `pc/service_bridge/test/test_commands.py pc/service_bridge/test/test_snapshot_events.py`
Expected: `commands` 모듈이 없어 FAIL

- [ ] **Step 3: `types.py` 두 자료형, `commands.py`(`apply_command` → `run_tx_guarded`, 판정은 `_apply(conn, rep)`), `events.route` 분기를 구현**

`_late(conn, rep, robot_id, why)`는 `reports._late`와 같은 모양으로 `commands.py` 안에 둠(로그 `log.warning` 포함).

- [ ] **Step 4: 통과 확인**

Run: Step 2와 같은 명령
Expected: PASS

- [ ] **Step 5: 커밋** — `feat(service_bridge): ReportCommand 결과를 admin_action에 한 번만 확정 · command_result 사건`

---

### Task 3: 브리지 노드 서버 · 문서

**Files:**
- Modify: `pc/service_bridge/service_bridge/ros/convert.py`, `pc/service_bridge/service_bridge/ros/bridge_node.py`
- Test: `pc/service_bridge/test/test_bridge_node.py`, `pc/service_bridge/test/test_convert.py`
- Docs: `docs/설계/인터페이스_명세.md`(3.1 `data` 표), `docs/설계/설계_검토_사항.md`(Q-07 "남은 것"), `pc/service_bridge/README.md`(첫 줄의 서버 목록)

**Interfaces:**
- Consumes: `commands.apply_command`, `types.CommandIn` (Task 2)
- Produces: `convert.command_in(req: ReportCommand.Request) -> CommandIn` (`stamp=iso(req.stamp)`), `BridgeNode._on_command(request, response)`

- [ ] **Step 1: 실패하는 시험을 씀**
  - `test_convert.py::test_command_in_copies_fields`: 모든 칸과 `stamp` ISO 변환
  - `test_bridge_node.py::test_report_command_round_trip`: `admin_action` 행(`R-1`)을 넣고, `/bridge/report_command`에 `ACCEPTED`를 보내 `accepted=True`, DB `final_result='ACCEPTED'`
  - `test_no_servers_until_db_ready`에 `"/bridge/report_command" not in services` 한 줄 추가

- [ ] **Step 2: 실패 확인**

Run: 브리지 시험 명령에 `pc/service_bridge/test/test_convert.py pc/service_bridge/test/test_bridge_node.py`
Expected: `command_in` 없음 · 서비스 없음으로 FAIL

- [ ] **Step 3: `convert.command_in`, 노드 서버(`self._services`에 `ReportCommand` · MutuallyExclusive 그룹), `_on_command`(예외 시 `accepted=False` + 오류 로그)를 구현하고, 시작 로그를 "Hello · ReportTrip · ReportCommand 서버 열림"으로 바꿈**

- [ ] **Step 4: 문서**
  - 명세 3.1 `data` 표: `` | `command_result` | `{request_id, command, target, result, reason, detail}`. `ReportCommand` 값 그대로, `final_result`를 쓴 때만 (2026-11-27) | ``
  - Q-07 "남은 것"에서 `command_result` 삭제, 결정 줄에 "2026-11-27 `command_result`"
  - README 첫 줄에 `ReportCommand` 서버 추가

- [ ] **Step 5: 통과 확인**

Run: 브리지 시험 명령 전체(`pc/service_bridge/test`)
Expected: 모두 PASS

- [ ] **Step 6: 커밋** — `feat(service_bridge): /bridge/report_command 서버, 명세 command_result data`

---

### Task 4: 전체 시험

- [ ] **Step 1:** `colcon build --symlink-install --packages-select fleet_manager service_bridge` 뒤 fleet_core · fleet_manager · central 시험(`ROS_DOMAIN_ID=87 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`)과 브리지 시험 전체
- Expected: 모두 PASS, skip 0 (도메인 설정 시)
