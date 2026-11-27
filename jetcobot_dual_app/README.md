# jetcobot_dual_app (PC)

여러 상품을 스테레오로 인식하고, 왼팔과 오른팔이 번갈아 집어 바구니에 담는 UI와 worker다. 인터페이스는 [../INTERFACES.md](../INTERFACES.md) 4절에 있다.

```bash
ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left
```

| launch 인자 | 기본값 | 뜻 |
| --- | --- | --- |
| `python` | `~/camera_code/.venv/bin/python` | worker를 돌릴 파이썬 |
| `script` | `multi_pick/sam_multi_real` | UI 스크립트 |
| `first_arm` | `left` | 먼저 집는 팔 |
| `concurrent_pick_place` | `false` | `true` = 한 팔이 담는 동안 다른 팔이 집기(동시 진행) |
| `fine_moveit_planning` | `true` | 미세조정을 MoveIt으로 계획. 동시 진행에서는 `false`로 쓴다(게이트를 거치게) |
| `fleet` | `false` | `true` = 팀 관제(fleet)와 연결. 아래 "팀 관제 연결" |
| `args` | `--stereo-fixed-see-center` | UI 스크립트에 넘기는 나머지 인자 |

| 폴더 | 내용 |
| --- | --- |
| `scripts/multi_pick/` | `sam_multi_real.py`(UI·흐름), `state_machine.py`(팔별 상태와 구역 점유), `stereo_self_correct.py`(매 캡처 오른쪽 카메라 회전 보정), 다중 ROI 인식 |
| `scripts/moveit_pick/` | 단일 상품 인식과 계획 엔진(`sam_dual_yaw_moveit.py`), 접근(`move_dual_sam_yaw_real.py`), 미세조정·잡기(`move_right_sam_yaw.py`), 자동 미세조정, 쥔 물체 모델, 보정 도구, `calibration/` |
| `scripts/right_place_trial/` | PLACE(`coordinated_right_case.py`), `see_center` 복귀, 양팔 경로 진행 |
| `scripts/left_detail.py`, `left_mentos_detail.py` | 미세조정용 물체 검출 |

- 동작 흐름과 옵션의 자세한 설명은 [README_state_pick.md](README_state_pick.md)(원본 `state_pick/README.md`)에 있다.

## 팀 관제(fleet) 연결 (Phody)

이 앱은 팀 프로젝트에서 **Phody**(작업대 `WORK`의 양팔, `arm_left` · `arm_right`)다. 관제가 담기 명령을 보내면 상품을 바구니에 담는다. `fleet:=false`(기본)면 관제 코드는 아예 불러오지 않는다.

### 관제는 어디 있나

| 무엇 | 어디 | 담당 |
| --- | --- | --- |
| **Pode 관제(fleet)** — Phody에 담기 명령 · 관리 명령을 보냄 | 팀 저장소 `addinedu-roscamp-16th/roscamp-repo-2`, `dev` 브랜치의 `pc/fleet_manager` · `pc/fleet_core` | Pode 관제 담당 |
| **중앙 관제(UI)** — 관리자 화면 버튼 · 알림 · 주문 처리 | 같은 저장소 `pc/central` · `pc/service_bridge` | 중앙 관제 담당 |
| **팀 정의**(메시지 · 서비스 · 액션, 이름 원본 `contract.yaml`) | 같은 저장소 `interfaces/fleet_interfaces`. Phody는 복사본 `~/phody_fleet_ws/src/fleet_interfaces`(팀 원본 그대로, `SOURCE.txt`에 커밋 번호)를 빌드해서 쓴다 | 인터페이스 담당 |
| **디스커버리 서버**(실물 망) | 팀 저장소 `tools/net/net_ds.sh`(서버 목록), fleet PC `192.168.0.252:11811` | Pode 관제 담당 |

### Phody 쪽 코드는 어디 있나

| 파일 | 내용 |
| --- | --- |
| `scripts/multi_pick/fleet_link.py` | 관제 연결 노드. 앱 안에서 자기 스레드로 돈다. 팔 상태 발행, 담기 액션 서버, 관리 명령 서버, heartbeat, 재시작 감지 |
| `scripts/multi_pick/sam_multi_real.py` | 앱. 관제 명령을 실제 동작으로 바꾸는 곳은 `_fleet_tick` · `_fleet_start_fill` · `_fleet_check_goal_end` · `_fleet_end` · `_fleet_recapture`, 스스로 복구는 `_schedule_self_recovery` · `_poll_self_recovery` |
| `scripts/multi_pick/fake_fleet.py` | **가짜 관제**(시험용). 팀 관제 없이 터미널에서 Phody에 명령을 보낸다 |
| `config/fleet_names_pending.yaml` | 팀 contract에 아직 없는 이름: 상품 코드 표(`choco_bar` → `chocolate_bar`), 임시 이어서 하기 서비스 이름 |
| `test/test_fleet_link.py` | 관제 연결 단위 테스트 |
| `~/iponic_net/phody_net.sh` (PC · Pi) | 통신 설정. `dev` = 도메인 83 · 서버 없음, `real` = 도메인 6 · 팀 디스커버리 서버(팀 `net_ds.sh` 그대로) |

### 주고받는 것 (contract 0.9)

| 이름 | 방향 | 뜻 |
| --- | --- | --- |
| `/arm_left/state`, `/arm_right/state` (`ArmState`) | Phody → 관제, 5 Hz | 팔 상태(`IDLE` · `PICKING` · `PLACING` · `PAUSED` 등), 모드, 락, 쥐고 있음, 사유(`detail`) |
| `/workcell/fill_basket` (`FillBasket` 액션) | 관제 → Phody | 담기 명령. 진행 단계(`CAPTURING` · `PICKING` · `TO_BASKET` · `PLACING` · `RETURNING`)와 결과(담은 수, 못 담은 수, 쥔 채 끝난 수) |
| `/{arm}/set_mode` (`SetMode`) | 관제 → Phody | 운영 모드(`NORMAL` · `TEST`). 받기 전에는 담기를 거절 |
| `/{arm}/set_lock` (`SetLock`) | 관제 → Phody | 락 해제 = 두 팔 see_center + 그리퍼 열기(힘 유지) |
| `/{arm}/fault_resolved` (`RobotCommand`) | 관제 → Phody | 장애 처리 완료 = PC 실행 게이트 고장 잠금 해제 |
| `/{arm}/resume_arm` (`RobotCommand`, **임시**) | 관제 → Phody | [복구] = see_center 복귀 → 다시 캡처 → 남은 상품. contract 1.0의 `/{arm}/set_pause`(`SetPause`)로 바뀔 예정 |
| `/fleet/heartbeat` | 관제 → Phody | 30초 끊기면 `detail`에 `HEARTBEAT_LOST`, 앱 화면에 표시, 작업은 계속 |

자세한 규칙과 결정: `docs/worklog/2026-11-26_fleet연결_작업지시.md`, `docs/worklog/2026-11-27_작업정리.md`, `docs/Phody_관제_확인목록_2026-11-27.md`.

### 실행

모든 장치(PC 터미널 전부 + Pi 두 대)를 같은 모드로 띄운다. 개발은 `dev`, 팀 실물 망은 `real`.

```bash
# 앱 (관제 연결 켬)
source ~/iponic_net/phody_net.sh dev && source ~/phody_fleet_ws/install/setup.bash && source ~/pinky_ws/install/setup.bash &&
ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left concurrent_pick_place:=true fine_moveit_planning:=false fleet:=true
```

```bash
# 가짜 관제 (새 터미널, 개발 때만. 실물 망에서는 진짜 관제가 붙으므로 띄우지 않음)
source ~/iponic_net/phody_net.sh dev && source ~/phody_fleet_ws/install/setup.bash &&
python3 ~/pinky_ws/src/jetcobot_dual/jetcobot_dual_app/scripts/multi_pick/fake_fleet.py
```

가짜 관제 명령: `mode NORMAL|TEST`, `fill N [kind]`(기본 `choco_bar`), `cancel`, `resume [left|right]`, `lock off|on [left|right]`, `fault [left|right]`, `hb off|on`, `state`, `quit`.

```bash
# 관제 연결 단위 테스트 (torch가 있는 venv 파이썬으로)
source /opt/ros/jazzy/setup.bash && source ~/phody_fleet_ws/install/setup.bash && source ~/pinky_ws/install/setup.bash &&
cd ~/pinky_ws/src/jetcobot_dual/jetcobot_dual_app && ~/camera_code/.venv/bin/python -m pytest test/test_fleet_link.py test/test_pi_move_limit.py -q
```
- 테스트: `cd test && python -m pytest`. 이전 흐름을 가정한 테스트가 있어 갱신이 필요하다.
