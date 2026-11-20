# JetCobot Pi 브리지 공부 노트

작성 2026-11-20. 원본은 `~/pinky_ws/src/jetcobot_dual_real/robot_bridge/`이고, `code/`는 그 **복사본**이다(공부용, 고쳐도 실행에 영향 없음).
Pi에서 실제로 실행되는 파일은 `~/jetcobot_dual_bridge/`에 있으며 PC 원본과 같은지는 따로 확인해야 한다.
`code/read_joint_states.py`에는 오늘 추가한 **서보 온도 읽기**(10 s, idle 때만)가 들어 있다.

---

## 1. 한 줄 요약

브리지는 **팔 하나당 Pi에서 도는 ROS 2 노드 하나**다. 하는 일은 딱 두 가지다.

1. 시리얼(USB, 1 Mbaud)로 로봇에게 **관절 각도를 물어봐서** 토픽으로 PC에 보낸다 (2 Hz).
2. PC(gate)가 보낸 **궤적 액션을 받아서** 로봇 SDK 명령으로 바꿔 보낸다.

```
 PC                                         Pi (팔마다 1개)                         로봇
 ───────────────────────────────           ─────────────────────────────          ──────────
 MoveIt 계획 ─▶ execution_gate ──액션──▶  sdk_drag_hybrid_bridge ──시리얼──▶  Atom 펌웨어 ─▶ 서보 6개
                (충돌 검사, 어느 액션                │  ▲
                 쓸지 선택)                          │  └─ get_angles() 2 Hz
 state_aggregator ◀──/side/real_joint_states────────┘
   └▶ /jetcobot_dual/joint_states ─▶ gate·MoveIt·픽 UI
```

---

## 2. 파일과 상속 구조

실행 파일은 `sdk_drag_hybrid_bridge.py` 하나지만, 클래스 5개가 겹겹이 상속된다. **위에서부터 읽으면 된다.**

| 순서 | 파일 | 클래스 | 이 층에서 추가되는 것 |
|---|---|---|---|
| 1 | `read_joint_states.py` | `ReadOnlyState` | 시리얼 연결 1개(`MyCobot280`), **상태 읽기 타이머**, 상태 토픽 발행 |
| 2 | `hardware_bridge.py` | `HardwareBridge` | 움직임 허용 여부, 피드백 신선도 검사, 궤적 검증, `stop_arm()` |
| 3 | `sdk_goal_bridge.py` | `SDKGoalBridge` | **끝점 이동**(send_angles 1번), 그리퍼 액션, 직선 하강/XY 서비스, `fresh_mode` 관리 |
| 4 | `sdk_drag_bridge.py` | `SDKDragBridge` | **drag 경로**(엔코더 목표 패킷을 0.4 s 이상 간격으로) |
| 5 | `sdk_drag_hybrid_bridge.py` | `SDKDragHybridBridge` | 끝점·drag 액션을 **둘 다** 열어 둠 (시리얼·busy 잠금 공유) |

보조 모듈:

| 파일 | 역할 |
|---|---|
| `trajectory_guard.py` | 받은 궤적 검사(관절 이름, 범위, 시작점이 현재 자세와 맞는지), 관절 한계 `LIMITS_DEG` |
| `trajectory_waypoints.py` | MoveIt 점 수백 개 중 **보낼 점만 고르기**(`select_waypoints`: 8° 간격, 1.5° 이내로 직선에 붙어 있으면 생략), 코너 둥글리기 |
| `drag_trajectory.py` | 각도 → **엔코더 값**(중심 2048, 4096/360°, 관절별 부호), 관절별 속도 비율 계산(`smooth_drag_command`) |
| `path_lookahead.py` | 측정 자세가 계획 경로 어디쯤인지(진행도), 경로에서 얼마나 벗어났는지(복도 오차) |
| `mode_lease.py` | SDK `fresh_mode`를 잠깐 바꾸고, 종료 시 원래 값으로 되돌림(복구 기록 파일 사용) |

---

## 3. 스레드와 시리얼 — 부하를 이해하는 핵심

```
MultiThreadedExecutor(4 스레드)
 ├─ 타이머: read()               state_hz(2 Hz)마다 ─┐
 ├─ drag 액션 실행 루프           0.05 s마다 반복      ├─▶ self.mc (시리얼 1개, thread_lock=True → 한 번에 하나씩)
 ├─ 끝점 액션 실행 루프           0.1 s마다 반복       │
 └─ 그리퍼 액션 / 서비스                              ─┘
```

- 시리얼 포트는 **하나**고, 모든 질의·명령이 `thread_lock`으로 **줄을 선다**. 하나가 오래 걸리면 다른 것이 기다린다.
- 실행 루프는 20 Hz·10 Hz로 돌지만 **시리얼로 각도를 직접 읽지 않는다.** 타이머가 마지막으로 읽은 `latest_arm`을 쓴다(`motion_sample()`). 그래서 **실제 새 피드백은 2 Hz**뿐이다.
- 피드백이 `1.5 s + feedback_grace_s`(지금 2.5 → 4 s)보다 오래되면 `Actual arm feedback missing or stale`로 실행을 멈춘다.

### 타이머 `read()`가 한 번에 하는 일 (`read_joint_states.py`)

| 상태 | 시리얼 질의 | 발행 토픽 |
|---|---|---|
| 항상 | `get_angles()` | `/side/real_joint_states`, `/side/display_joint_states` |
| 멈춰 있을 때만 | `get_coords()` 매번 | `/side/sdk_coords` |
| 멈춰 있을 때만 | `get_gripper_value()` 2 s마다 | `/side/gripper_value` |
| 멈춰 있을 때만 | `get_servo_temps()` 10 s마다 (**오늘 추가**) | `/side/servo_temps`, 55°C 이상이면 경고 로그 |

움직이는 동안 좌표·그리퍼·온도를 안 읽는 이유: 시리얼을 이동 명령과 각도 피드백에 양보하려고.
부작용: 긴 이동 직후엔 그리퍼 값이 최대 수 초 묵어서 gate가 그리퍼 명령을 거절할 수 있다(그래서 PC 쪽에서 재시도).

---

## 4. 움직이는 방법 두 가지 — 끝점 vs drag

PC gate가 경로를 보고 **둘 중 하나의 액션**을 고른다.
- 직선으로 가도 충돌이 없으면 → 끝점(`/side/sdk_endpoint_controller/follow_joint_trajectory`)
- 직선이 충돌하면 → MoveIt 우회 경로를 따라가는 drag(`/side/sdk_drag_controller/follow_joint_trajectory`)

| | 끝점 (`SDKGoalBridge._execute_sdk_goal`) | drag (`SDKDragBridge.execute`) |
|---|---|---|
| 보내는 명령 | 마지막 목표 하나를 `send_angles(목표, speed)` 1번 | `SET_ENCODERS_DRAG(엔코더 6개, 속도 6개)` 여러 번 |
| 경로 | **펌웨어가 알아서** 보간 (관절이 동시에 안 끝날 수 있음 → 검사한 직선을 벗어남) | MoveIt 경로에서 8° 간격 점들을 차례로 보냄 |
| 다음 패킷 조건 | — | 0.4 s 이상 지났고, 측정 자세가 다음 점에서 10°(`drag_lead_deg`) 이내 |
| 안전 검사 | 이동 범위, 관절 한계 | + 계획 경로에서 6° 이상 벗어나면 정지(`drag_corridor_deg`) |
| 완료 판정 | 허용오차 안에서 `endpoint_settle_s`(0.5 s) 동안 0.2° 이내로 유지 | 마지막 패킷 뒤 새 샘플 **2번 연속** 허용오차 안 |
| 실패·취소 시 | `mc.stop()` (서보 힘은 **절대 안 뺌**: 중력으로 떨어지니까) | 같음 |
| 액션 피드백 발행 | 10 Hz | 20 Hz (새 샘플이 없어도 매 루프) |

**속도 값**: drag 속도 `drag_speed`(지금 200, 최대값)를 관절별 이동량 비율로 나눠서 보낸다. 손목(J4~J6)은 ×0.8. 경로 양 끝에서는 55%까지 줄였다가 가운데서 100%(`smooth_drag_command`의 ramp).

### `fresh_mode`
- SDK 설정값. 0 = 펌웨어 보간(명령을 부드럽게 이어 감), 1 = 최신 명령 즉시 실행.
- 끝점·drag 모두 **0**을 요구한다. `native_interpolation:=true`면 시작할 때 `ModeLease`가 0으로 바꾸고, 정상 종료(Ctrl+C) 때 원래 값으로 되돌린다.
- 비정상 종료로 복구 기록 파일(`sdk_mode_restore.json`)이 남아 있으면 다음 시작을 거부한다 → `restore_sdk_mode.py`로 정리.

---

## 5. 지금 쓰는 실행 인자 (2026-11-19 표준 명령)

| 인자 | 값 | 의미 |
|---|---|---|
| `robot_side`, `__ns` | left / right | 토픽 이름 앞에 붙음 |
| `enable_motion` | true | false면 읽기만 |
| `native_interpolation` | true | fresh_mode 0 사용 |
| `state_hz` | 2.0 | 상태 읽기 주기 (허용 1~5) |
| `gripper_type` | 0 | SDK 그리퍼 인자 생략 |
| `drag_speed` | 200 | drag 최대 속도 값 (20~200) |
| `drag_step_deg` | 8.0 | 보낼 점 간격 상한 (≤8) |
| `drag_deviation_deg` | 1.5 | 생략해도 되는 직선 이탈 한도 |
| `drag_corner_trim_deg` | 0.0 | 코너 둥글리기 끔 (떨림 줄이려고) |
| `drag_send_interval_s` | 0.4 | 패킷 최소 간격 (≥0.4) |
| `endpoint_settle_s` | 0.5 | 끝점 도착 후 정지 확인 시간 |
| `feedback_grace_s` | 2.5 | 피드백 끊김 허용 추가 시간 (통신 끊김 대비) |

---

## 6. 토픽·액션·서비스 목록 (팔 하나 기준, `side` = left/right)

**토픽 (Pi → PC)**: `/side/real_joint_states`, `/side/display_joint_states`, `/side/gripper_value`, `/side/sdk_coords`, `/side/servo_temps`(신규)
- QoS: Pi 발행 = RELIABLE, depth 10 / PC 구독(gate, aggregator) = `sensor_data`(BEST_EFFORT). 이 조합은 정상 연결된다.

**액션 (PC → Pi)**: `sdk_endpoint_controller`, `sdk_drag_controller`, `sdk_slow_goal_controller`, `gripper_group_controller` (모두 `/side/.../follow_joint_trajectory`)

**서비스**: `/side/handeye_free_mode`(손으로 움직이는 모드), `/side/sdk_linear_descend_60mm`, `/side/sdk_linear_descend_tenth_mm`, `/side/sdk_linear_xy_tenth_mm`, `/side/sdk_linear_xy_slow_tenth_mm`, `/side/sdk_linear_xy_stop`

---

## 7. 지금 문제와 브리지의 관계 (2026-11-20 정리)

### 문제 1: 팔이 가만히 있으면 PC가 토픽을 못 받음 (브리지 재시작하면 회복)
- Pi 안에서 echo는 되므로 **발행은 살아 있음** → Pi↔PC 사이 DDS 연결 문제.
- 설정 상태: PC에 DDS XML 없음, `RMW_IMPLEMENTATION` 비어 있음 → 기본 RMW, WiFi 위 **멀티캐스트 탐색**.
- 유력 원인(추정): WiFi 절전 + 멀티캐스트 손실 → 탐색 공지가 lease 시간 동안 안 오면 PC가 상대를 지움. 재시작하면 새 공지로 다시 연결.
- 확인: 문제 순간 PC에서 `ros2 topic info -v /right/real_joint_states` → `Publisher count: 0`이면 탐색 끊김.
- 대책 후보: Pi WiFi 절전 끄기(`iw dev wlan0 set power_save off`), DDS 피어 IP 직접 지정 또는 Discovery Server.
- 주의: 모든 터미널의 `ROS_DOMAIN_ID`가 같아야 함 (PC `~/.bashrc`는 42, 실행 명령은 83을 쓰는 곳이 있음).

### 문제 2: 떨림, J4가 순간 처진 뒤 그 자리에 고정
- 브리지에는 서보 힘을 빼는 코드가 없음. 멈춰 있을 때 위치 명령을 다시 보내지도 않음 → 처진 자리 그대로 유지되는 것과 맞음.
- 측정(멈춘 상태): 왼팔 J4 **53°C**, 오른팔 J4 42°C / J5 45°C, 나머지 29~41°C. 전압 J1~3 ≈12.3 V, J4~6 ≈7.1~7.6 V, 서보 상태 0.
- 어제는 오른팔, 오늘은 왼팔이 처짐 → 서보 하나 고장이 아니라 **양팔 J4 부하·발열** 쪽이 유력. 처짐 직후 온도·상태를 찍어 확정할 것.
- 부하를 키우는 요소: `drag_speed` 200(최대), 급정지(`stop()`), see_center에서 손목 뻗은 채 장시간 대기.

---

## 8. 읽는 순서 추천

1. `read_joint_states.py` 전체 (짧음) — 상태 읽기와 시리얼 공유 이해
2. `hardware_bridge.py`의 `current_sample` / `motion_sample` / `validated` / `stop_arm`
3. `sdk_goal_bridge.py`의 `__init__`(액션·서비스 목록)과 `_execute_sdk_goal`(끝점)
4. `sdk_drag_bridge.py`의 `execute`(drag 루프) + `drag_trajectory.smooth_drag_command`
5. `sdk_drag_hybrid_bridge.py` — 두 액션을 어떻게 합쳤는지 (짧음)
