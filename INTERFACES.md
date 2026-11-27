# 인터페이스 정리

양팔 JetCobot(myCobot 280) 시스템의 ROS 2 인터페이스(토픽·액션·서비스)와 파일 인터페이스를 정리했다. 기준은 2026-11-27 코드다(처음 정리 2026-11-20). 팀 관제(fleet)와 주고받는 것은 [6절](#6-팀-관제fleet-인터페이스-phody)에 있다.

- `{side}`는 `left` 또는 `right`, `{arm}`은 팀 이름 `arm_left` 또는 `arm_right`다.
- 모든 노드(PC 터미널 전부 + Pi 두 대)는 같은 통신 설정을 써야 한다. `source ~/iponic_net/phody_net.sh dev`(개발: 도메인 83, 디스커버리 서버 없음) 또는 `real`(팀 실물 망: 도메인 6 + 디스커버리 서버 `192.168.0.252:11811`, 팀 `tools/net/net_ds.sh` 그대로).
- 노드 이름이나 이름 공간은 실행 중인 다른 프로세스와 겹치면 안 된다. 겹치면 서비스 응답이 사라진다(2026-11-20 장애 원인).

## 1. 전체 구조

```
 [Pi left]  jetcobot_dual_bridge (/left/...)  ─┐            ┌─ jetcobot_dual_app (픽 UI + worker)
 [Pi right] jetcobot_dual_bridge (/right/...) ─┼─ WiFi DDS ─┤
   + 카메라 H.264 RTP (UDP 5000/5001)         ─┘            └─ jetcobot_dual_control (PC)
                                                                 state_aggregator → move_group
                                                                 execution_gate (안전 게이트)

 [팀 Pode 관제 PC] fleet_manager  ←── /arm_*/state, /workcell/fill_basket, 관리 명령 ──→  app 안의 fleet_link (fleet:=true)
```

명령은 이 순서로만 흐른다.

1. 앱이 MoveIt에 계획을 요청한다.
2. 앱 또는 `move_group`이 **게이트**에 실행 목표를 보낸다.
3. 게이트가 검사한 뒤 Pi 브리지 액션으로 전달한다.

예외로 앱이 Pi에 직접 보내는 명령이 있다. 미세조정과 하강에 쓰는 SDK 서비스, 느린 관절 이동이다(5절).

## 2. Pi 브리지 (`jetcobot_dual_bridge`, 이름 공간 `/{side}`)

### 발행 토픽

| 토픽 | 타입 | 주기·조건 | 내용 |
| --- | --- | --- | --- |
| `/{side}/real_joint_states` | `sensor_msgs/JointState` | `state_hz`(기본 2 Hz) | 측정 관절 6개 `1_Joint..6_Joint` [rad]. **유일한 기준 상태** |
| `/{side}/display_joint_states` | `sensor_msgs/JointState` | 같음 | 표시용. 그리퍼 근사 각도 포함 |
| `/{side}/gripper_value` | `std_msgs/Int32` | 쉬는 중 2초마다 | SDK 그리퍼 값 0(닫힘)–100(열림). 읽기 실패 시 -1 |
| `/{side}/sdk_coords` | `std_msgs/Float64MultiArray` | 쉬는 중 | `[unix_time, x, y, z, rx, ry, rz]` (mm, deg, SDK 좌표) |
| `/{side}/servo_temps` | `std_msgs/Float64MultiArray` | 쉬는 중 10초마다 | `[unix_time, J1..J6]` °C. 55 °C 이상이면 `SERVO HOT` 경고 |

- 쉬는 중에는 한 주기에 추가 질의(그리퍼, 온도, 좌표)를 **하나만** 한다. 각도 발행이 밀리지 않게 하려는 것이다.
- QoS: 발행은 RELIABLE, depth 10. PC 구독은 `qos_profile_sensor_data`(BEST_EFFORT)다.

### 액션 서버 (`control_msgs/FollowJointTrajectory`)

| 이름 | 용도 |
| --- | --- |
| `/{side}/sdk_drag_controller/follow_joint_trajectory` | 경로 추종. 경유점을 축약해(`drag_step_deg`) 엔코더 패킷으로 전송 |
| `/{side}/sdk_endpoint_controller/follow_joint_trajectory` | 끝점 이동. `send_angles` 1회 후 `endpoint_settle_s` 동안 안정 확인 |
| `/{side}/sdk_goal_controller/follow_joint_trajectory` | SDK 목표 이동(기존 방식) |
| `/{side}/sdk_slow_goal_controller/follow_joint_trajectory` | 느린 관절 목표 1개. 오른팔 미세조정에 사용 |
| `/{side}/gripper_group_controller/follow_joint_trajectory` | 그리퍼. 목표 값을 펌웨어 기본 설정으로 실행 |

- 결과의 `error_code`가 0이 아니면 실패다. 원인은 `error_string`에 들어 있다.
- 실패하거나 취소되면 `mc.stop()`만 호출한다. 서보 토크는 유지된다.

### 서비스

| 이름 | 타입 | 용도 |
| --- | --- | --- |
| `/{side}/sdk_linear_xy_tenth_mm` | `example_interfaces/AddTwoInts` | 펌웨어 직선 XY 이동. `a`=dx, `b`=dy [0.1 mm]. 왼팔 미세조정에 사용 |
| `/{side}/sdk_linear_xy_slow_tenth_mm` | `AddTwoInts` | 느린 XY 단계 이동 |
| `/{side}/sdk_linear_xy_stop` | `std_srvs/Trigger` | 느린 XY 정지 |
| `/{side}/sdk_linear_descend_tenth_mm` | `AddTwoInts` | 직선 하강. `a`=거리 [0.1 mm] |
| `/{side}/sdk_linear_descend_60mm` | `Trigger` | 60 mm 하강(기존) |
| `/{side}/handeye_free_mode` | `std_srvs/SetBool` | 핸드아이 캡처용 자유 모드 |

주의: 오른팔 `sdk_linear_xy_tenth_mm`은 이동할 때마다 rx +3.4°, ry +0.7°, Z +6 mm 보상을 더한다. 여러 번 이동하면 그리퍼가 기운다. 그래서 오른팔 미세조정은 이 서비스를 쓰지 않는다.

### 파라미터 (`config/bridge.yaml`)

| 파라미터 | 값 | 주의 |
| --- | --- | --- |
| `enable_motion` | true | |
| `native_interpolation` | true | |
| `state_hz` | 2.0 | 1–5. 4 Hz는 11-24에 시도했지만 안 됨 |
| `gripper_type` | 0 | |
| `drag_speed` | 200 | |
| `endpoint_sdk_speed` | 30 | 끝점 이동 `send_angles` 속도(11-24, 전에는 10 고정) |
| `drag_step_deg` | 8.0 | PC `hybrid_drag_step_deg`와 **같아야 함** |
| `drag_deviation_deg` | 1.5 | |
| `drag_corner_trim_deg` | 0.0 | PC `hybrid_drag_corner_trim_deg`와 **같아야 함** |
| `drag_send_interval_s` | 0.4 | |
| `endpoint_settle_s` | 0.5 | 0–2 |
| `feedback_grace_s` | 2.5 | |
| `robot_side` | launch `side` 인자 | |

## 3. PC 제어 (`jetcobot_dual_control`, 이름 공간 `/jetcobot_dual`)

### `state_aggregator`

| 입출력 | 이름 | 타입 | 내용 |
| --- | --- | --- | --- |
| 구독 | `/{side}/real_joint_states`, `/{side}/gripper_value` | | |
| 발행 | `/jetcobot_dual/joint_states` | `JointState` | 12관절 `left_1_Joint..right_6_Joint`. **두 팔이 모두 최신일 때만** 발행 |
| 발행 | `/jetcobot_dual/display_joint_states` | `JointState` | RViz 표시용 |
| 발행 | `/jetcobot_dual/state_status` | `std_msgs/String` | 상태 요약 |

- 파라미터 `stale_seconds`=1.5. 이보다 오래된 팔이 있으면 발행을 보류하고 `Combined state withheld; stale arms=...` 경고를 낸다.

### `move_group` (MoveIt)

- 그룹: `arm_left`, `arm_right`, `both_arms`(12관절).
- 서비스: `plan_kinematic_path`, `check_state_validity`, `get_planning_scene`, `apply_planning_scene`, `compute_fk`, `compute_ik`.
- 액션: `move_action`, `execute_trajectory`.
- 토픽: `display_planned_path`.
- 모두 `/jetcobot_dual/` 아래에 있다.
- 컨트롤러(`moveit_manage_controllers: false`): `coordinated_execution:=true`면 `guarded_both_controller`, 아니면 `guarded_{side}_controller`. 여기에 `guarded_{side}_gripper_controller`가 붙는다.

### `execution_gate` (노드 `/jetcobot_dual/selected_arm_execution_gate`)

| 입출력 | 이름 | 타입 | 내용 |
| --- | --- | --- | --- |
| 액션 서버 | `/jetcobot_dual/guarded_both_controller/follow_joint_trajectory` | `FollowJointTrajectory` | 12관절 경로 1개. 구간별 충돌 검사 후 양쪽 Pi에 동시 전송 |
| 액션 서버 | `/jetcobot_dual/guarded_{side}_controller/...`, `guarded_{side}_gripper_controller/...` | 같음 | 한 팔 실행과 그리퍼 |
| 액션 클라이언트 | `/{side}/sdk_drag_controller`, `/{side}/sdk_endpoint_controller`, `/{side}/gripper_group_controller` | | Pi로 전달 |
| 서비스 | `/jetcobot_dual/reset_execution_fault` | `std_srvs/SetBool` | 실패 후 걸린 잠금(fault latch) 해제. 현재 자세가 충돌 상태면 거부 |
| 사용 | `/jetcobot_dual/check_state_validity`, `/jetcobot_dual/joint_states` | | 충돌 검사와 실측 상태 |

- 실행 자리: 12관절 경로(B 구간, `guarded_both_controller`)는 **단독**(두 팔이 다 비어야 시작), 한 팔 관절 목표와 그리퍼는 **팔별 lane**에서 두 팔이 동시에 돈다(11-25). 한 lane씩 상대 팔 경로와 충돌 검사 후 Pi로 보낸다.
- **고장 잠금(fault latch)**: Pi로 이미 보낸 동작이 취소되거나 실패하면(B · 한 팔 · 그리퍼 모두) 잠기고, `reset_execution_fault`로 풀 때까지 모든 목표를 거절한다. 두 팔 정지 · 새 관절 상태를 확인한 뒤에만 풀린다.
- 동작이 끝난 뒤 새 관절 상태를 30초까지 기다린다(`_wait_combined_update`, 11-26에 5 → 30초).
- 한 팔만 실행할 때 다른 팔이 3° 넘게 움직이면 중단한다.
- 상태 공백은 `state_gap_grace_s`까지 허용한다.
- 충돌 검사 응답이 없으면 한 번 다시 요청한 뒤 실패 처리한다.
- 경로 축약 방식은 Pi와 같은 코드(`jetcobot_dual_bridge/scripts/trajectory_waypoints.py`)를 불러 써서 일치시킨다.

### 장면 노드

- `tin_case_scene`: `config/tin_case.yaml`의 케이스를 계획 장면에 넣는다.
- `pick_object_scene`: `config/pick_objects.yaml` 목록의 물체를 계획 장면에 넣는다.
- 둘 다 `apply_planning_scene`을 쓴다.

## 4. 앱 (`jetcobot_dual_app`)

### 프로세스

| 스크립트 | 실행 주체 | 역할 |
| --- | --- | --- |
| `multi_pick/sam_multi_real.py` | `pick.launch.py` | UI, 스테레오 인식, 상태 머신, worker 관리 |
| `moveit_pick/move_dual_sam_yaw_real.py` | UI가 subprocess로 실행 | 접근 계획과 실행. 백그라운드 다음 상품 계획에도 사용 |
| `moveit_pick/move_right_sam_yaw.py` | UI가 subprocess로 실행 | 미세조정(`--fine-request`), 하강·닫기·리프트(`--phase grasp`) |
| `right_place_trial/coordinated_right_case.py` | UI가 subprocess로 실행 | B: `see_center` ↔ `pre_place`(`--transport-stage enter`/`return`) |
| `right_place_trial/stage3_place.py` | UI가 subprocess로 실행 | C: `pre_place` → 놓기 → `pre_place` |
| `right_place_trial/trial_see_center.py` | UI가 subprocess로 실행 | 멈춘 뒤 C 키: `see_center` 복귀 |
| `moveit_pick/return_see_center.py` 등 | UI가 subprocess로 실행 | 복귀와 그리퍼 |

### 환경 변수

| 변수 | 용도 |
| --- | --- |
| `JETCOBOT_COORD_NODE` | `dual_arm_coordinator` 노드 이름. 백그라운드 계획은 `dual_arm_coordinator_next_plan`을 쓴다. 동시에 뜨는 프로세스끼리 노드 이름이 겹치지 않게 하려는 것이다 |
| `ROS_DOMAIN_ID` 등 | `~/iponic_net/phody_net.sh dev\|real`이 정한다(맨 위) |

### 카메라

| 항목 | 값 |
| --- | --- |
| 송신(Pi) | `ros2 launch jetcobot_dual_bridge camera.launch.py side:=<left/right> host:=<PC IP>` (`gst-launch-1.0`) |
| 수신(PC) | 픽 UI의 GStreamer `udpsrc port=<포트>` → appsink(BGR) |
| 포트 | 왼쪽 5000, 오른쪽 5001. Pi `port:=`와 UI `--left-port`/`--right-port`가 같아야 함 |
| 목적지 | **`host` = 픽 UI PC의 IP**(현재 192.168.0.15). PC IP가 바뀌면 Pi 쪽 `host`도 바꿔야 함 |
| 형식 | RTP H.264(payload 96, clock 90000), 640×480 YUY2 30 fps, 1500 kbit/s, 키프레임 30프레임마다, mtu 1200 |
| 장치 | `/dev/jetcocam0` |

- 대역폭: Pi마다 송신 약 100–200 KB/s(11-20 측정). ROS 토픽보다 훨씬 크고, 같은 WiFi를 쓴다.

### 파일 인터페이스

| 파일 | 형식 | 내용 |
| --- | --- | --- |
| `scripts/moveit_pick/calibration/camera_calibration.npz` | npz `K`, `DIST` | 카메라 내부 파라미터 |
| `scripts/moveit_pick/calibration/T_{side}_gripper_camera_20261117.json` | JSON `T_gripper_camera` 4×4 [m] | 핸드아이(`6_Link` → 카메라) |
| `scripts/moveit_pick/calibration/stereo_see_center_correction_20261117.json` | JSON | `see_center` 고정 스테레오 보정(오른쪽 카메라 월드 자세에 왼쪽에서 곱함). `make_stereo_correction.py --write`로 갱신. 이전 파일은 `_before_<시각>.json` |
| `scripts/sam_yaw_output/multi_capture_<시각>/` | 폴더 | 캡처 1회 결과. 영상, `triangulation.npz`, `run_NN_object_K/` 아래 worker 로그와 `moveit_target.json` |
| `.../moveit_target.json` | JSON | 상품 1개 목표: `center_xyz_mm`(`right_base_link`), 접근 관절, `multi_object`(`assumed_start_rad`, `peer_holding`) |
| `.../auto_fine_paused_{side}_<시각>.jpg` | JPEG | 미세조정이 멈춘 순간의 카메라 원본(11-27) |
| `scripts/moveit_pick/calibration/table_markers.json` | JSON | 작업판 ArUco 마커 위치(카메라 자세 계산, 11-24) |
| `models/bar_mentos_yolo26s_seg_v2/best.pt` (없으면 `bar_mentos_yolo26s_seg`) | YOLO-seg 가중치 | 기본 인식(`--seg yolo`, 11-24) |
| `jetcobot_dual_app/config/fleet_names_pending.yaml` | YAML | 팀 contract에 아직 없는 이름(상품 코드 표, 임시 이어서 하기 서비스) |
| `scripts/sam_yaw_output/board_capture_<시각>/` | 폴더 | 보정 보드 캡처. `compare_board_captures.py`, `make_stereo_correction.py`의 입력 |
| `jetcobot_dual_control/config/tin_case.yaml` | YAML | 케이스 위치와 크기 |
| `jetcobot_dual_control/config/pick_objects.yaml` | YAML | 물체 종류별 크기(쥔 물체 충돌 모델) |
| `~/jetcobot_moveit2_real_upload/test_urdf/urdf/jetcobot_dual_real.urdf` | URDF | 오프라인 도구(`tcp_z0.py`, `place_right_in_case.py`)에서만 사용. **저장소 밖** |
| `~/sam2_src/checkpoints/sam2.1_hiera_small.pt` | SAM2 가중치 | 예전 인식(`--seg sam`)에서만. **저장소 밖** |

### 앱이 쓰는 ROS 이름

- `/jetcobot_dual/` 아래:
  - 서비스: `compute_fk`, `compute_ik`, `check_state_validity`, `get_planning_scene`, `apply_planning_scene`, `plan_kinematic_path`.
  - 액션: `move_action`, `execute_trajectory`, `guarded_*`.
  - 파라미터 조회: `move_group/get_parameters`, `selected_arm_execution_gate/get_parameters`.
- Pi에 직접:
  - `/{side}/real_joint_states`, `/{side}/gripper_value`
  - `/{side}/sdk_linear_xy_tenth_mm`, `/{side}/sdk_linear_descend_tenth_mm`, `/{side}/sdk_linear_xy_stop`
  - `/{side}/sdk_slow_goal_controller`, `/{side}/sdk_endpoint_controller`

## 5. 게이트를 거치지 않는 명령 (주의)

| 명령 | 보호 장치 |
| --- | --- |
| 미세조정 XY(왼팔 SDK 직선 이동, 오른팔 느린 관절 이동) | 앱에서 MoveIt 충돌 검사, 기울기 8°(오른팔은 시작 기울기 기준), 관절 변화량 제한 |
| 직선 하강 | 앱에서 높이와 작업 공간 검사 |

이 경로는 게이트의 "한 번에 하나" 규칙 밖에 있다. 두 팔을 동시에 독립 동작시키는 기능을 넣을 때 다시 검토해야 한다.

## 6. 팀 관제(fleet) 인터페이스 (Phody)

팀 프로젝트에서 이 양팔은 **Phody**(작업대 `WORK`)다. 앱을 `fleet:=true`로 띄우면 `jetcobot_dual_app/scripts/multi_pick/fleet_link.py`가 앱 안에서 자기 노드 · 스레드로 돌며 아래를 주고받는다. 이름 · 타입 · QoS의 원본은 팀 `interfaces/fleet_interfaces/contract.yaml`(지금 0.9, Phody 복사본 `~/phody_fleet_ws/src/fleet_interfaces`)이다.

- 관제는 둘이다: **Pode 관제**(팀 저장소 `pc/fleet_manager` · `pc/fleet_core`) = Phody에 명령을 보냄, **중앙 관제(UI)**(`pc/central` · `pc/service_bridge`) = 관리자 화면 · 주문.
- 시험용 가짜 관제: `jetcobot_dual_app/scripts/multi_pick/fake_fleet.py`. 실행 · 명령은 [jetcobot_dual_app/README.md](jetcobot_dual_app/README.md).

### Phody가 내보내는 것

| 이름 | 타입 | 내용 |
| --- | --- | --- |
| `/arm_left/state`, `/arm_right/state` | `fleet_interfaces/ArmState`, QoS STATE, 5 Hz | 아래 표 |
| `/workcell/fill_basket` (액션 서버) | `fleet_interfaces/FillBasket` | 담기 명령을 받아 결과 · 진행을 돌려줌 |
| `/{arm}/set_mode` · `/{arm}/set_lock` · `/{arm}/fault_resolved` · `/{arm}/resume_arm` (서비스 서버) | `SetMode` · `SetLock` · `RobotCommand` · `RobotCommand` | 관리 명령. 같은 `request_id`는 같은 답(최근 32건, 60초) |

**`ArmState` 칸**

| 칸 | 값 |
| --- | --- |
| `state` | `IDLE` · `PICK_WAITING` · `PICKING` · `PLACE_WAITING` · `PLACING`(B 진입 ~ C ~ B 복귀) · `PAUSED` · `ERROR`. 한 팔이 멈추거나 장애면 두 팔 모두 같은 값. 스스로 복구 중에는 하던 상태 |
| `detail` | 멈춘 이유 + 일꾼 로그의 마지막 오류 줄. 게이트 잠금 추정이면 앞에 `gate fault latched (motion canceled); fault_resolved needed`, 스스로 복구 중이면 `self-recovery n/2`, 관제 신호가 30초 끊기면 앞에 `HEARTBEAT_LOST` |
| `mode` | 받은 운영 모드. 켜질 때 `UNKNOWN`(이때 담기는 `MODE_MISMATCH`로 거절) |
| `boot_id` | **앱 재시작과 Pi 브리지 재시작 때 새로 만듦**(브리지는 `/{side}/real_joint_states` publisher GID가 바뀌는 것으로 앎). 재시작 때 모드 `UNKNOWN`, 락 잠김 |
| `lock` | 잠김 = true(켜질 때). `set_lock`으로 바뀜 |
| `holding` | 상품을 쥐고 B · C에 있는 팔(바구니에 놓은 뒤에는 false) |
| `order_id` · `placed_total` | 하던 담기 목표의 주문 번호 · 이 목표에서 두 팔이 담은 수 |
| `last_request_id` | 마지막으로 받아들인 관리 명령 |
| `gripper` · `forced` | 아직 비움 |

**`FillBasket`**

| 부분 | 값 |
| --- | --- |
| goal | `station_id`는 `WORK`, `items[].kind`는 팀 상품 코드(`choco_bar` → Phody 클래스 `chocolate_bar`). 모르는 작업대 · 상품이면 `UNKNOWN_TARGET` |
| feedback | `arm_id`(캡처 중에는 빈 값), `phase` = `CAPTURING` · `PICKING` · `TO_BASKET` · `PLACING` · `RETURNING`, `placed_total`. 0.5초마다, 움직이는 팔마다 |
| result | `success`(다 처리했는지), `placed` = `placed_confirmed`(바구니 위에서 그리퍼를 연 수), `missing`, `uncertain`(끝날 때 쥐고 있었거나 떨어뜨린 상품), `reason` |
| reason | `''`(성공), `RECOGNITION_ERROR`(요청보다 적게 보임 · 캡처 실패, success=false), `CANCELED`(관제 취소, Pi 브리지 재시작), `NOT_READY`(다른 일 중), `MODE_MISMATCH`, `LOCK_RELEASED`, `UNKNOWN_TARGET`. `PICK_FAILED` · `ARM_FAULT`는 당분간 보내지 않음(Pode 관제와 합의) |

- 멈춰도(사람 P, 스스로 멈춤, 락 해제) **목표는 끝내지 않고** `PAUSED`로 [복구]를 기다린다.

**관리 명령**

| 서비스 | Phody 동작 | 거절 |
| --- | --- | --- |
| `set_mode` | `NORMAL` · `TEST` 저장 | 그 밖의 값 `MODE_MISMATCH` |
| `set_lock` | 해제(`locked=false`) = 멈춤 → 두 팔 see_center + 그리퍼 열기(힘 유지), 목표는 PAUSED. 잠금은 값만 | 없음 |
| `fault_resolved` | PC 게이트 `/jetcobot_dual/reset_execution_fault`(data=true) 호출. 받았다고 먼저 답하고 결과는 `detail`의 잠금 문구가 사라지는 것으로 보임 | 게이트 서비스 없음 `NOT_READY` |
| `resume_arm` (**임시**, contract 1.0에서 `/{arm}/set_pause`(`SetPause`)로 바뀜) | [복구] = C로 see_center(C가 안 되면 두 팔 see_center + 그리퍼 열기) → 다시 캡처 → 남은 상품만 담기. 두 팔에 2초 안에 오면 한 번만 | 앱이 안 멈춤 `NOT_STOPPED`, 락 풀림 `LOCK_RELEASED`, 게이트 잠금 `NOT_READY` |

### Phody가 받는 것

| 이름 | 타입 | 내용 |
| --- | --- | --- |
| `/fleet/heartbeat` | `std_msgs/Header`, QoS HEARTBEAT | 한 번이라도 받은 뒤 30초 끊기면 `HEARTBEAT_LOST`(상태 · 앱 화면), 작업은 계속 |

### Phody 앱 안의 관련 동작 (관제와 상관없이 항상)

- 스스로 멈추면(계획 · 실행 · 미세조정 실패, Pi 거절, 관절 상태 30초 끊김) U를 2번 스스로 시도(게이트 잠금이면 먼저 스스로 해제) → 안 되면 PAUSED로 알림. 사람 P · 관제 명령으로 멈춘 것은 제외.
- 계획만 실패하면 다른 팔은 계속, 실패한 물체는 큐 뒤로 반대 팔에, 실패한 팔은 다른 물체. 두 팔 다 실패하면 다른 팔 담기가 끝난 뒤 멈춤.
- 미세조정이 바를 못 찾으면 사진을 남기고, 다른 팔 담기가 끝난 뒤 멈춤(위 스스로 복구).
- 다 담으면(관제 목표가 끝날 때마다, 수동 모드도) R처럼 첫 화면으로 자동 리셋.

자세한 결정과 실물 확인: `docs/worklog/2026-11-27_작업정리.md`, `docs/Phody_관제_확인목록_2026-11-27.md`.
