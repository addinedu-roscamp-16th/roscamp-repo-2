# 실물 JetCobot 양팔 자동 접근 (새 코드)

- 화면: `sam_dual_yaw_moveit.py` (기존 `sam_yaw_moveit.py`는 그대로 사용 가능)
- 계획/실행: `move_dual_sam_yaw_real.py`, `dual_arm_coordinator_real.py`
- 경로 비용·표본화: `motion_math.py`

ROI를 선택하면 좌우 팔의 180° 대칭 파지 자세와 MoveIt 경로를 모두 계획합니다. 반대 팔을 유지한 경로가 하나라도 있으면 그 후보들 중 관절 이동량이 가장 작은 팔을 고릅니다. 둘 다 막히면 작은 비켜주기, 큰 비켜주기, 홈 자세 순으로 반대 팔 이동을 계획하고, 비켜주는 팔을 먼저 움직인 다음 선택한 팔을 움직입니다. 필요 없는 반대 팔 이동은 하지 않습니다.

각 단계의 계획 경로는 선택 후보에 대해서만 MoveIt 충돌 장면에서 다시 확인합니다. 계획 시 후보당 기본 3초, 전체 후보 탐색 기본 25초로 제한합니다. 직선 관절 이동이 가능하면 `sdk_drag_hybrid` 게이트가 끝점 send goal을 선택합니다. 직선이 충돌하고 MoveIt 우회 경로가 유효하면 게이트가 그 경로를 drag로 보냅니다. 실행 직전에도 현재 경로를 다시 검사합니다. 물체 형상은 현재 카메라 추정만으로 MoveIt 충돌 장면에 넣지 않습니다.

## 실행 준비

기존 PC MoveIt 및 Pi `sdk_goal_bridge.py` 프로세스를 정상 종료한 뒤 다음 구성을 사용합니다. 같은 Pi에서 두 SDK 브리지를 동시에 실행하지 않습니다. Pi의 hybrid 파일과 관련 파일은 현재 로컬 소스와 동일한 SHA256임을 확인했습니다.

오른팔 Pi (.22):

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
cd ~/jetcobot_dual_bridge
~/venv/mycobot/bin/python sdk_drag_hybrid_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0 -p drag_speed:=200 -p drag_step_deg:=5.0 -p drag_deviation_deg:=1.5 -p drag_corner_trim_deg:=3.0 -p drag_send_interval_s:=0.4
```

왼팔 Pi (.33):

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
cd ~/jetcobot_dual_bridge
~/venv/mycobot/bin/python sdk_drag_hybrid_bridge.py --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0 -p drag_speed:=200 -p drag_step_deg:=5.0 -p drag_deviation_deg:=1.5 -p drag_corner_trim_deg:=3.0 -p drag_send_interval_s:=0.4
```

PC MoveIt:

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both trajectory_backend:=sdk_drag_hybrid collision_padding_m:=0.06 rviz:=true
```

PC 카메라 프로그램 (기존에 쓰던 Python 환경에서):

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --enable-execute
```

양쪽 ROI 선택 후 계획이 만들어지면 터미널의 `SELECTED`, `peer`와 RViz 경로를 확인합니다. 저장 계획의 `route=auto`는 실행 시 게이트가 send goal/drag를 결정한다는 뜻입니다. `E`는 저장한 접근 계획을 실행합니다. 접근 뒤 `X/Y`는 기존 미세 조정, `G`는 하강·그립, `O`는 그리퍼 열기, `C`는 양팔 see_center입니다.

이 폴더의 `manual_roi_drag.py`는 X/Y 세부조정이 켜진 양팔 화면을 실행합니다. `fine_roi_real.py`는 ROI 픽셀 계산을 맡고, 저장된 fine 요청은 `move_right_sam_yaw.py --fine-request <JSON>`으로 실행합니다. 카메라 보정과 hand-eye 파일은 `calibration/`에 복사했고, SAM 체크포인트·실물 URDF·LightGlue 소스는 기존 경로를 사용합니다. 실행 결과는 기존 `~/camera_code/sam_yaw_output/`에 저장합니다.

이 구현은 물체 하나의 접근을 양팔 중에서 배정합니다. 반대 팔 비켜주기가 필요할 때는 실물 게이트의 한 번에 한 팔 실행 방식에 맞춰 **순서대로** 움직입니다. 두 팔이 서로 다른 물체를 동시에 집는 동작은 포함하지 않습니다. 현재까지 Python 로딩, 문법 검사, 팔 선택/비켜주기 모의 검사만 완료했고 실물 경로 실행은 하지 않았습니다.

최근 ROI `[251.1, 104.6, 109.2]` mm의 계획 전용 확인에서는 MoveIt이 양팔 자세 목표에 `code=99999`를 반환했고, MoveIt 로그에는 목표 트리의 유효한 상태를 샘플링하지 못했다는 메시지가 남았습니다. 이 입력의 실제 도달 불가와 충돌 중 어느 쪽인지는 확인되지 않았습니다.
