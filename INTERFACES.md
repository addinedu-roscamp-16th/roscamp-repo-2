# 인터페이스 정리

양팔 JetCobot(myCobot 280) 시스템의 ROS 2 인터페이스(토픽·액션·서비스)와 파일 인터페이스를 정리했다. 기준은 2026-11-20 코드다.

- `{side}`는 `left` 또는 `right`다.
- 모든 노드는 같은 `ROS_DOMAIN_ID`를 써야 한다(현재 83).
- 노드 이름이나 이름 공간은 실행 중인 다른 프로세스와 겹치면 안 된다. 겹치면 서비스 응답이 사라진다(2026-11-20 장애 원인).

## 1. 전체 구조

```
 [Pi left]  jetcobot_dual_bridge (/left/...)  ─┐            ┌─ jetcobot_dual_app (픽 UI + worker)
 [Pi right] jetcobot_dual_bridge (/right/...) ─┼─ WiFi DDS ─┤
   + 카메라 H.264 RTP (UDP 5000/5001)         ─┘            └─ jetcobot_dual_control (PC)
                                                                 state_aggregator → move_group
                                                                 execution_gate (안전 게이트)
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
| `state_hz` | 2.0 | 1–5 |
| `gripper_type` | 0 | |
| `drag_speed` | 200 | |
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

- 한 번에 목표 **하나**만 받는다.
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
| `right_place_trial/coordinated_right_case.py` | UI가 subprocess로 실행 | PLACE: 케이스 이동 → 놓기 → `see_center` 복귀 |
| `moveit_pick/return_see_center.py` 등 | UI가 subprocess로 실행 | 복귀와 그리퍼 |

### 환경 변수

| 변수 | 용도 |
| --- | --- |
| `JETCOBOT_COORD_NODE` | `dual_arm_coordinator` 노드 이름. 백그라운드 계획은 `dual_arm_coordinator_next_plan`을 쓴다. 동시에 뜨는 프로세스끼리 노드 이름이 겹치지 않게 하려는 것이다 |
| `ROS_DOMAIN_ID` | 83 |

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
| `scripts/sam_yaw_output/board_capture_<시각>/` | 폴더 | 보정 보드 캡처. `compare_board_captures.py`, `make_stereo_correction.py`의 입력 |
| `jetcobot_dual_control/config/tin_case.yaml` | YAML | 케이스 위치와 크기 |
| `jetcobot_dual_control/config/pick_objects.yaml` | YAML | 물체 종류별 크기(쥔 물체 충돌 모델) |
| `~/jetcobot_moveit2_real_upload/test_urdf/urdf/jetcobot_dual_real.urdf` | URDF | 오프라인 도구(`tcp_z0.py`, `place_right_in_case.py`)에서만 사용. **저장소 밖** |
| `~/sam2_src/checkpoints/sam2.1_hiera_small.pt` | SAM2 가중치 | **저장소 밖** |

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
