> 최신 변경: 노트북의 요청당 5° 제한과 한 관절만 이동하는 제한을 제거했습니다.
> Pi에서 `max_move_deg:=5.0`으로 실행 중이면 그 제한은 별도로 남습니다.
> 실제 관절 한계, 속도/궤적 검증과 양팔·그리퍼 공통 실행 잠금은 유지합니다.

# 실제 JetCobot 두 대: 공통 MoveIt 모델과 독립 SDK 브리지

기존 `~/pinky_ws/real_robot_tools`와 Gazebo 소스는 수정하지 않습니다.
이 패키지의 PC 런치는 **기본적으로 실제 상태 표시 + 계획 전용**입니다.
`execution_arm:=left/right/both`로 명시하면 연결한 팔의 작은 단일 관절 SDK 시험만
실행할 수 있습니다. 듀얼암 동시 실행은 지원하지 않습니다.

## 로봇 이름과 배치

`left`는 새 로봇 **192.168.0.33**, `right`는 기존 로봇 **192.168.0.22**입니다.
화면상의 좌우 방향과 무관한 고유 이름입니다. Pi IP와 이름의 대응은 사용자가 확인해
고정해야 합니다. 이 패키지는 임의로 IP를 선택하거나 자동 배포하지 않습니다.

`config/layout.yaml` 기본값:

| 로봇 | world 기준 베이스 위치 [m] | 베이스 회전 |
|---|---|---|
| left (.33) | (0, +0.30, 0) | 0, 0, 0 |
| right (.22) | (0, 0, 0) | 0, 0, 0 |

`world`는 첫 로봇 베이스의 위치·방향과 같습니다. Gazebo 듀얼 URDF/SRDF를 읽어
런치 시 실제 모델을 생성합니다. 시뮬레이션 플러그인/ros2_control은 제거하고,
관절 제한은 양쪽 모두 아래 실제 범위로 설정합니다.

| 관절 | 범위 [deg] |
|---|---|
| 1 | −168 ~ 168 |
| 2 | −135 ~ 135 |
| 3 | −150 ~ 150 |
| 4 | −145 ~ 145 |
| 5 | −165 ~ 165 |
| 6 | −180 ~ 180 |

Gazebo 듀얼 모델의 6번 관절은 이미 단일 모델 대비 −45° 보정이 포함돼 있습니다.
기본 추가 보정은 0°이며 기존 단일 런치의 총 보정과 같습니다. 현재 실물과
일치하는지 확인하고 필요할 때만 `wrist_roll_adjust_deg`를 로봇별로 변경하세요.
이 값은 모델 보정이며 SDK 값/로봇 영점을 바꾸지 않습니다.

## 1. PC 빌드

```bash
source /opt/ros/jazzy/setup.bash
cd ~/pinky_ws
colcon build --symlink-install --packages-select jetcobot_dual_real
source install/setup.bash
```

기존 `jetcobot_gazebo`, `jetcobot_description`, MoveIt 설치를 재사용합니다.
Gazebo 프로세스를 실행할 필요는 없습니다.

## 2. 각 Pi에 별도 브리지 폴더 복사

**두 로봇의 기존 단일 SDK/상태 브리지는 정상 종료**합니다. 각 Pi에서 동일 시리얼
장치에 두 프로그램을 동시에 연결하지 마세요. 기존 SDK 모드 복구 기록이 남았다면
기존 `restore_sdk_mode.py`로 복원한 뒤 진행합니다.

PC에서 실제 IP를 지정해 각각 복사합니다. 암호는 SSH 프롬프트에서 입력합니다.

```bash
scp -r ~/pinky_ws/src/jetcobot_dual_real/robot_bridge \
  jetcobot@기존로봇IP:~/jetcobot_dual_bridge
scp -r ~/pinky_ws/src/jetcobot_dual_real/robot_bridge \
  jetcobot@새로봇IP:~/jetcobot_dual_bridge
```

Pi에서 MoveIt/Gazebo는 필요하지 않습니다. 기존 ROS 2 Jazzy와 pymycobot
가상환경을 그대로 사용합니다. 여섯 Python 파일을 함께 복사해야 합니다.

## 3. 먼저 관절 상태만 발행

PC와 두 Pi가 서로 통신 가능한 같은 네트워크에 있어야 합니다. 각 터미널에서:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_LOCALHOST_ONLY
```

**새 로봇 Pi (.33, left)**:

```bash
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/read_joint_states.py \
  --ros-args -r __ns:=/left -p robot_side:=left -p state_hz:=2.0
```

**기존 로봇 Pi (.22, right)**:

```bash
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/read_joint_states.py \
  --ros-args -r __ns:=/right -p robot_side:=right -p state_hz:=2.0
```

이 두 명령은 조회만 합니다. 이동, 홈 이동, 토크 해제, 영점 저장, SDK 모드 변경은
하지 않습니다. Pi에서 `/dev/ttyJETCOBOT`가 다르면 `-p port:=실제장치경로`를 지정하세요.
`robot_side`와 `__ns`가 맞지 않으면 시리얼 연결 전에 오류로 종료합니다.

## 4. PC에서 한 RViz에 두 로봇 표시

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_LOCALHOST_ONLY
ros2 launch jetcobot_dual_real dual_real.launch.py
```

Planning Group에서 `arm_left`, `arm_right`, `both_arms`를 선택할 수 있습니다.
`home`은 전 관절 0인 계획 목표이며 시작 시 실제 홈 명령을 보내지 않습니다.
기본 런치에서는 팔과 그리퍼 모두 Plan만 사용합니다. 실행 옵션을 켜면
해당 팔과 그리퍼의 SDK 액션이 함께 연결됩니다.

상태 점검:

```bash
ros2 topic echo /left/real_joint_states --once
ros2 topic echo /right/real_joint_states --once
ros2 topic echo /jetcobot_dual/state_status --once
ros2 topic echo /jetcobot_dual/joint_states --once
```

- Pi 상태 메시지는 원래 `1_Joint` … `6_Joint`를 유지합니다.
- PC 통합 노드가 `left_` / `right_`를 붙여 12축을 공통 모델에 전달합니다.
- 두 팔 중 하나라도 없거나 1.5초 넘게 오래되면 통합 상태를 발행하지 않습니다.
- 반복 메시지로 소스 시각이 멈춘 데이터를 최신으로 위장하지 않습니다.
- PC/Pi의 시각이 맞지 않아도 오래된 데이터로 처리되므로 시스템 시각을 맞추세요.
- `/jetcobot_dual/joint_states`는 실측 팔 12축만 포함합니다.
- `/jetcobot_dual/display_joint_states`는 그리퍼 추정 관절 두 개를 추가합니다.
  그리퍼는 유효한 SDK 값을 기존 근사식으로 변환하며, 없거나 오래되면 해당 그리퍼 관절을
  발행하지 않습니다. 누락된 피드백을 열림 기본값으로 채우지 않습니다.
  이는 보정된 실제 손가락 위치 측정값이 아니며 `state_status`에 출처가 표시됩니다.
- 기존 단일 MoveIt 또는 Gazebo 듀얼 RViz/TF를 함께 켜면 공통 `world`와
  `left_*`/`right_*` TF가 중복될 수 있으므로 초기 점검에서는 종료하세요.
- 영상 수신과 hand-eye optical 프레임 연결은 이번 패키지에 포함하지 않습니다.
  카메라가 차지하는 기존 CAD 형상은 그대로 남지만 보정된 카메라 외부 파라미터는 아닙니다.

다른 배치 파일을 지정할 수도 있습니다.

```bash
ros2 launch jetcobot_dual_real dual_real.launch.py layout:=/전체경로/layout.yaml
```

## SDK 실행 경로 준비 상태

`robot_bridge/sdk_goal_bridge.py`는 기존 SDK bridge의 **독립 사본**입니다.
상태 토픽과 액션을 로봇별로 분리했고, 목표 관절 이름도 좌우 접두사로 구분합니다.

| 구분 | left | right |
|---|---|---|
| 측정 상태 | `/left/real_joint_states` | `/right/real_joint_states` |
| 팔 액션 | `/left/sdk_goal_controller/follow_joint_trajectory` | `/right/sdk_goal_controller/follow_joint_trajectory` |
| 집게 액션 | `/left/gripper_group_controller/follow_joint_trajectory` | `/right/gripper_group_controller/follow_joint_trajectory` |
| 목표 관절 | `left_1_Joint` … `left_6_Joint` | `right_1_Joint` … `right_6_Joint` |

SDK 브리지도 기본 `enable_motion=false`입니다. 상태 전용 대신 액션 이름까지 확인하려면
아래처럼 실행할 수 있지만, 같은 Pi의 `read_joint_states.py`는 먼저 종료해야 합니다.

```bash
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_goal_bridge.py \
  --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=false -p state_hz:=2.0
```

right에는 위 명령의 left 두 곳을 right로 바꿉니다. `native_interpolation` 기본값은
false이며 위 명령은 SDK 모드를 변경하지 않습니다. 향후 명시적으로 동작을 허용하고
native mode를 사용하면 복구 기록은 이 별도 폴더의 `sdk_mode_restore.json`에 저장됩니다.
그 경우 이 폴더의 `restore_sdk_mode.py`를 사용합니다.


**중요한 실행 한계:** 기존과 동일하게 SDK는 최종 목표만 한 번 보냅니다.
MoveIt의 중간 회피 경로/시간을 추종하지 않으며 실제 두 팔 무충돌 동작은 검증되지
않았습니다. 기본 PC 런치는 계획 전용이며, 아래의 명시적 실행 옵션으로 한 팔 시험을 연결합니다.
공유 공간 동시 실행은 이 패키지의 지원 범위가 아닙니다.

## 선택형 SDK 경로 추종 시험 (2026-11-12, 로컬 구현)

기본 실행은 위의 sdk_goal_bridge.py 끝점 전송 그대로다. 별도 robot_bridge/sdk_path_bridge.py를 Pi에서 실행하고 PC 런치에 trajectory_backend:=sdk_path를 명시한 경우에만 새 모드를 사용한다. 두 브리지를 같은 Pi/시리얼 포트에서 동시에 실행하지 않는다. 새 브리지도 기존 /{side}/sdk_goal_controller/follow_joint_trajectory 액션을 제공한다.

새 모드는 MoveIt의 관절 궤적에서 관절당 최대 4° 간격, 계획 경로와 최대 0.5° 이내의 중간 목표를 선택한다. 각 중간 목표는 SDK send_angles로 한 번만 보내고 명령 간격을 최소 0.75초로 둔다. 해당 목표에 대한 새 실측 관절 피드백이 2° 이내이면 다음 목표로 진행하고, 최종 목표는 두 개의 새 피드백으로 확인한다. 오래된 피드백, 계획 경로 이탈, 관절 범위 초과, 취소 또는 시간 초과 시 SDK stop을 요청하고 실패로 종료한다. 명령을 4Hz로 계속 갱신하던 방식과 달리, 동일 목표 재전송은 없다.

끊김을 줄이는 실험 옵션 `-p queue_lookahead:=true`는 SDK 순차 실행 모드 0에서 다음 경유점 하나를 미리 전송한다. 최대 한 점만 앞서 보내며 기존 실측 관절 범위·경로 이탈·타임아웃 감시는 유지한다. 이때 명령 전송이 경유점 실측 확인보다 앞서므로 실측 확인 후 전송하던 기본 모드보다 취소·이탈 시 이미 대기 중인 명령이 하나 있을 수 있다. SDK `stop()`으로 모든 동작 정지를 요청한다. 펌웨어의 경유점 감속 여부와 실제 부드러움은 작은 빈 공간 시험으로 확인해야 한다. 먼저 양쪽 Pi에 새 `sdk_path_bridge.py`를 복사한 뒤 기존 브리지를 정상 종료하고 같은 실행 명령에 `-p queue_lookahead:=true`를 추가한다. 기본값은 false이다.

**실기 관찰 (2026-11-12):** 사용자가 왼팔 `queue_lookahead:=true`가 오른팔 기본 모드보다 확실히 부드러웠다고 비교했다. 다만 왼팔에도 끊기는 느낌은 남았다. 오른팔 기본 모드는 `Waypoint 3/21 did not settle`로 실패했다. 이는 허용 오차 내 실측 도달을 시간 안에 확인하지 못했다는 뜻이며, 원인은 현재 로그만으로 특정할 수 없다. 시간 초과 로그에 각 관절 오차·최소 오차·피드백 나이를 추가했다. 양팔 계획 경로와 하드웨어 상태가 달라 제어 옵션만의 정량 비교는 아니다.

**추가 실기 관찰 (2026-11-12):** 오른팔도 `queue_lookahead:=true`로 실행한 뒤 사용자는 약 3° 경유점 시간 초과 오류가 사라지고 움직임이 더 부드러워졌다고 보고했다. 오른팔의 기존 `waypoint_tolerance_deg:=2.5` 설정이 유지됐는지는 이 메시지만으로 확정할 수 없다. 양팔 모두 선행 전송의 주관적 부드러움 개선 근거가 있으나, 계획 경로 대비 실측 궤적 오차·최종 TCP 오차·반복 안정성은 아직 측정되지 않았다.

이는 계획 관절 경로의 드문 점들을 근사해 따라가는 방식이다. MoveIt의 시간표를 그대로 재생하거나 SDK의 지점 사이 연속 경로가 충돌 없이 유지됨을 증명하지 않는다. 떨림이 사라지는지도 실기로 검증되지 않았다. 실행 게이트는 이 모드에서만 관절 경로 굴곡을 허용하며 시작 상태, 속도 0.08rad/s, 관절 한계, 상태 신선도, 단일 팔 잠금은 계속 확인한다. Pi의 max_move_deg는 전체 계획의 현재 관절각 대비 최대 변화량 제한이다. 처음에는 작은 단일 관절·장애물 없는 시험에서 10°처럼 보수적으로 두고, 계획 검사와 실제 결과를 본 뒤 별도로 결정한다.

로봇 정지와 실측 상태를 먼저 확인한 뒤, 인증 가능한 PC에서 새 파일을 해당 Pi로 복사한다. 예를 들어 오른팔은 다음과 같다.

    scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/{sdk_goal_bridge,sdk_path_bridge,trajectory_waypoints}.py jetcobot@192.168.0.22:~/jetcobot_dual_bridge/

Pi의 기존 sdk_goal_bridge.py 프로세스를 정상 종료한 후 ROS Jazzy와 ROS_DOMAIN_ID=42가 설정된 터미널에서 경로 브리지를 실행한다.

    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_path_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p state_hz:=2.0 -p max_move_deg:=10.0 -p goal_tolerance_deg:=5.0 -p native_interpolation:=true -p gripper_type:=0

PC의 기존 MoveIt 런치를 정상 종료하고 다시 실행한다. 이 장비에서는 신규 ROS 프로세스의 Pi 발견이 간헐적으로 실패한 적이 있어 정적 Pi 주소를 지정한다.

    export ROS_STATIC_PEERS='192.168.0.22;192.168.0.33'
    ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=right trajectory_backend:=sdk_path

기존 모드로 돌아가려면 Pi 경로 브리지를 정상 종료해 SDK 모드 복구 로그를 확인한 뒤 기존 sdk_goal_bridge.py를 실행하고, PC 런치를 기본 trajectory_backend:=sdk_goal로 시작한다. Pi에 새 파일이 배포되었거나 실기로 떨림·경로 정확도가 검증됐다는 기록은 아직 없다.

**경유점 선택 수정 (2026-11-12):** 이전 선택기는 MoveIt의 가속·감속으로 시간 간격이 비선형인 직선 관절 경로도 굽은 경로로 취급해 경유점을 불필요하게 늘릴 수 있었다. 이제 관절 공간의 기하학적 선분에서 최대 0.5° 이탈과 단조 진행을 검사한다. 기본 최대 관절 변화 4°는 유지하므로 큰 관절 이동은 여전히 여러 명령으로 나뉜다. `PATH START` 로그에 최종 관절 변화량과 계획 관절 이동량을 표시한다. 작은 이동은 경유점이 한 개까지 줄 수 있으므로, 최종 5° 허용 오차만으로 실제 이동 없이 성공하는 일을 막기 위해 요청 이동량의 절반 이상을 실측으로 확인한다. 이 수정은 Pi에 `trajectory_waypoints.py`와 `sdk_path_bridge.py` 두 파일을 함께 배포해야 한다. 실기 검증 전이다.

## SDK 모드 1 분리 시험 (로컬 구현, 실기 미검증)

`robot_bridge/sdk_refresh_trial_bridge.py`는 기존 경로 브리지와 별도 액션 `/{side}/sdk_refresh_trial_controller/follow_joint_trajectory`를 사용한다. SDK 모드 1에서 실측 관절 위치를 MoveIt 관절 경로에 투영하고, 그 위치보다 앞선 경로 목표를 주기적으로 갱신한다. 경유점 도착을 기다리지 않는다. 기본 갱신 간격은 0.6초, 앞선 거리는 관절 공간 6°, SDK 속도는 10(왼팔) 또는 15(오른팔) 시험값이다. 경로에서 최대 관절 이탈 8°를 넘거나 피드백이 끊기면 중지 요청 후 실패 처리한다. 실제 로봇이 목표 사이를 어떤 곡선으로 이동할지는 SDK/펌웨어에 달렸으므로 충돌 안전 또는 부드러움을 보장하지 않는다. 과거 4Hz 연속 갱신에서 떨림이 있었다.

`enable_motion:=true`, `native_refresh_trial:=true`를 요구하고 원래 SDK 모드는 `sdk_mode_restore.json`에 기록해 정상 종료 시 복구한다. 실행 전 같은 Pi의 다른 시리얼 브리지를 종료해야 한다. 먼저 한 팔만 빈 공간에서 작은 이동으로 시험한다. 그리퍼와 모드 0 XY MoveL 서비스는 이 시험 브리지가 제공하지 않는다. 모드 변경 또는 중지 후에는 실측 각도와 모드 복구 로그를 확인한다.

PC에서 갱신 파일을 시험할 Pi로 복사한다. 로컬 파일은 Pi에 자동 배포되지 않는다.

    scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/{sdk_refresh_trial_bridge,hardware_bridge,path_lookahead,mode_lease,read_joint_states,trajectory_guard,trajectory_waypoints}.py jetcobot@192.168.0.33:~/jetcobot_dual_bridge/
    scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/{sdk_refresh_trial_bridge,hardware_bridge,path_lookahead,mode_lease,read_joint_states,trajectory_guard,trajectory_waypoints}.py jetcobot@192.168.0.22:~/jetcobot_dual_bridge/

기존 Pi 경로 브리지를 정상 종료한 뒤 선택한 팔에서 시작한다. 왼팔:

    export ROS_DOMAIN_ID=42
    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_refresh_trial_bridge.py --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=true -p native_refresh_trial:=true -p state_hz:=2.0 -p goal_tolerance_deg:=5.0 -p refresh_interval_s:=0.6 -p lookahead_deg:=6.0 -p path_corridor_deg:=8.0 -p refresh_sdk_speed:=10

오른팔:

    export ROS_DOMAIN_ID=42
    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_refresh_trial_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p native_refresh_trial:=true -p state_hz:=2.0 -p goal_tolerance_deg:=5.0 -p refresh_interval_s:=0.6 -p lookahead_deg:=6.0 -p path_corridor_deg:=8.0 -p refresh_sdk_speed:=15

PC는 패키지를 빌드하고 기존 MoveIt 런치를 정상 종료한 뒤 다음으로 시작한다.

    source /opt/ros/jazzy/setup.bash
    source ~/pinky_ws/install/setup.bash
    export ROS_DOMAIN_ID=42
    export ROS_STATIC_PEERS='192.168.0.22;192.168.0.33'
    ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=left trajectory_backend:=sdk_refresh_trial collision_padding_m:=0.06 rviz:=true

PC 게이트는 다른 팔의 측정 상태도 요구하고, 실행 중 다른 팔이 3° 이상 움직이면 시험을 취소한다. 양팔 시험은 각 팔의 단독 시험과 모드 복구를 확인한 뒤 `execution_arm:=both`로 전환한다. 이 시험의 성공은 MoveIt 경로 추종·부드러움·충돌 회피의 실기 증거가 아니다. 경로 앞쪽 목표를 SDK로 보내면 실제 관절은 경로의 굽은 부분을 잘라 이동할 수 있다. 6 cm planning padding은 그 실측 경로의 충돌 여유를 증명하지 않는다.

### pymycobot 4.0.7 내부 시간 기반 스트림 시험 (2026-11-13, 실기 미검증)

`sdk_patch/mycobot280_4_0_7_timed_angles.patch`는 **원본 SHA-256 `2e745de4eed76652f7dd9f703eb6d5b7aae9c838b63b0f97cfc32b8bf9452729`에만** 적용하는 패치다. `MyCobot280.send_timed_angles()`를 추가한다. MoveIt의 `(시간, 6관절 각도)` 경로를 시간에 따라 선형 보간하고 2.0초 앞의 목표를 SDK 모드 1에서 주기적으로 보낸다. 경유점 도착을 기다리지 않는다. 브리지의 `timed_stream_trial:=true`는 실측 위치보다 관절 경로 길이 기준 6~8° 앞선 목표로 제한하고, 실제 경로 이탈 8° 또는 목표와의 관절 차이 10°를 넘으면 중단한다. 이것은 같은 `send_angles` 패킷을 보내는 Python 제어 함수이며 펌웨어의 경유점 블렌딩 기능이 아니다. 곡선에서 관절 목표 간 실제 이동은 MoveIt 경로를 벗어날 수 있다. 정지 ACK와 큐 정리도 펌웨어 6.5에서 확인되지 않았다.

기존 Pi 브리지를 종료한 다음 PC에서 두 Pi로 패치와 갱신된 시험 브리지를 복사한다. 첫 시험은 한 팔만 빈 공간에서 작은 경로로 한다.

    scp ~/pinky_ws/src/jetcobot_dual_real/sdk_patch/{install_timed_angles.py,mycobot280_4_0_7_timed_angles.patch} ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/sdk_refresh_trial_bridge.py jetcobot@192.168.0.33:~/jetcobot_dual_bridge/
    scp ~/pinky_ws/src/jetcobot_dual_real/sdk_patch/{install_timed_angles.py,mycobot280_4_0_7_timed_angles.patch} ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/sdk_refresh_trial_bridge.py jetcobot@192.168.0.22:~/jetcobot_dual_bridge/

각 Pi에서 설치기를 실행한다. 원본 해시가 다르거나 백업이 이미 있으면 변경 없이 중단한다. 설치기는 원본을 `mycobot280.py.before_timed_stream`에 백업하고 문법 검사 후 새 파일로 교체한다. 로봇에는 명령을 보내지 않는다.

    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/install_timed_angles.py
    ~/venv/mycobot/bin/python -c 'from pymycobot.mycobot280 import MyCobot280; print(hasattr(MyCobot280, "send_timed_angles"))'

왼팔 Pi 실행 명령:

    export ROS_DOMAIN_ID=42
    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_refresh_trial_bridge.py --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=true -p native_refresh_trial:=true -p timed_stream_trial:=true -p state_hz:=2.0 -p goal_tolerance_deg:=5.0 -p refresh_interval_s:=0.6 -p path_corridor_deg:=8.0 -p refresh_sdk_speed:=10

오른팔 Pi 실행 명령:

    export ROS_DOMAIN_ID=42
    ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_refresh_trial_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p native_refresh_trial:=true -p timed_stream_trial:=true -p state_hz:=2.0 -p goal_tolerance_deg:=5.0 -p refresh_interval_s:=0.6 -p path_corridor_deg:=8.0 -p refresh_sdk_speed:=15

PC는 기존 `trajectory_backend:=sdk_refresh_trial collision_padding_m:=0.06` 명령을 사용한다. 첫 시험의 `execution_arm`은 `left` 또는 `right`로 선택한다. 실기에서 떨림, 감속, 계획 경로와의 차이가 확인되기 전에는 양팔 시험으로 확대하지 않는다. 실패 후 SDK `stop()` 호출은 물리 정지 확인이 아니므로 실제 각도와 모드를 점검한다. 원복은 브리지 정상 종료 후 `~/venv/mycobot/lib/python3.12/site-packages/pymycobot/mycobot280.py.before_timed_stream`을 원본 경로에 복사하고 다시 시작한다.

**실기 관찰:** 사용자는 MoveIt `send_goal`을 한 번 보냈다. 오른팔 로그에는 그 하나의 목표가 `199 planned points`로 들어왔고, 시간 기반 SDK 함수가 `TIMED SEND 1..21`의 **21개 `send_angles` 명령**으로 나눠 보낸 것이 표시된다. 1~20번째는 거의 정확히 0.6초 간격이며, 사용자는 여전히 매끄럽지 않은 `뚝뚝` 동작을 보고했다. 따라서 Python SDK 파일에 함수를 추가했다는 사실 자체로 연속 모션이 생기지 않았다. `TIMED SEND 20`의 진도는 64.9/68.0°, 21번째는 6.6초 뒤 65.8/68.0°였다. SDK 함수는 실측 진도가 경로 끝 6° 이내여서 최종 각도와 같은 목표를 일찍 보낼 수 있지만, 종전 코드는 계획 시간이 끝날 때까지 최종 전송으로 인식하지 않아 중간에 불필요한 긴 공백이 생겼다. 로컬 패치에서는 실제 전송 목표가 최종 각도면 즉시 완료로 판정하도록 수정했다. 이미 패치된 Pi에는 이 로컬 수정이 자동 적용되지 않는다. 이 6.6초 공백 수정이 이동 중 끊김을 해결한다는 증거는 없다. 추가 `send_angles` 주기 조정은 실제 전송·관절 속도 계측 없이는 권장하지 않는다. 모드 0 기본 경로 브리지를 유지하거나 원복할 수 있다.

### 드래그 재생 형식 조사 (2026-11-13)

제조사 `drag_trial_teaching.py`의 재생은 기록된 `[encoder 6개, servo speed 6개, 시간 간격]`을 순회하면서 `set_encoders_drag(encoders, speeds)`를 **매 점마다** 호출한다. `drag_start_record()`, `drag_end_record()`, `drag_get_record_data()`는 기록/읽기용이고 MoveIt 궤적 전체를 한 번에 업로드하는 함수는 아니다. 다만 `set_encoders_drag`는 `send_angles`와 다른 저수준 패킷이므로 별도 시험 가치가 있다. 입력은 각도(rad)가 아닌 엔코더(0..4096)와 관절별 서보 속도(로컬 SDK 검증 0..6000)이다. 280용 공개 C++ 코드에는 11.3777777 encoder step/degree와 중심 2048이 있다. `robot_bridge/read_drag_mapping.py`는 다른 시리얼 브리지가 꺼진 상태에서 정지 자세 3회 읽기를 수행하며 구동/토크 해제 명령을 보내지 않는다.

왼팔 정지 측정은 각도 `[-22.76, -14.15, -41.22, -13.88, 1.66, 7.91]°`, 엔코더 `[2307, 2209, 1579, 2206, 2029, 1958]`; 오른팔은 각도 `[-0.26, -0.96, -0.87, -1.75, 0.7, -0.52]°`, 엔코더 `[2051, 2059, 2038, 2068, 2040, 2054]`였다. 두 팔 모두 `round(2048 + sign[i] * 4096/360 * angle_deg[i])`, `sign=[-1,-1,+1,-1,-1,-1]`과 여섯 정수 값이 정확히 일치했다. `robot_bridge/drag_trajectory.py`에 이 변환과 시간/속도 변환을 계획 전용으로 구현하고 양쪽 실측 값으로 테스트했다. 정지 상태의 위치 변환 검증이며 `set_encoders_drag` 패킷이 펌웨어 6.5에서 어떻게 움직이는지는 증명하지 않는다.

**오른팔 1° 단일 패킷 시험은 판별 불가:** 2026-11-13 오른팔의 J1 +1° 목표(엔코더 2051→2040, 속도 25 step/s)에서 첫 약 0.25초 후 관측 각도는 J1 -0.26→-0.08° (+0.18°), J4 -1.75→-2.81° (-1.06°)였다. 스크립트는 다른 관절 측정 변화 >1°를 감지해 `stop()`을 호출했으나 응답은 -1이었다. 사용자는 팔이 눈에 띄게 움직이지 않았다고 보고했다. 후속 읽기 3회에서 오른팔은 J1 +0.08°/2047, J4 -2.90°/2081로 일정했고 서보 속도 여섯 개 모두 0이었다. 목표가 바뀌지 않은 J4의 값은 처음보다 13 encoder step(-1.15°) 달랐다. 이 측정만으로 물리적 이상 동작이라고 결론낼 수 없고, +1° 목표는 가시적 동작/기능 판별에 너무 작다. 로컬 SDK에서 동일한 입력을 직렬 I/O 없이 인코딩하면 프레임 `FE FE 1A 3E` 뒤에 엔코더 `[2040,2059,2038,2068,2040,2054]`와 속도 `[25,0,0,0,0,0]`가 순서대로 들어간다. Python 인자 순서/바이트 경계 오류는 발견되지 않았다. `stop()` ACK는 펌웨어 6.5에서 보증되지 않는다.

다음 `robot_bridge/trial_drag_joint.py`는 오른팔 정지 자세에서 J1 +5°/서보 속도 입력값 60 목표를 계산한다. 기본 실행은 읽기 전용이며 `--execute`를 붙인 경우에만 `SET_ENCODERS_DRAG` 패킷을 한 번 쓰고 5초 동안 각도와 엔코더를 기록한다. J2~J6 중 하나가 시작점에서 3° 넘게 변하면 정지를 요청하고 중단한다. SDK 공개 `set_encoders_drag()`는 응답이 없을 때 재전송할 수 있어 이 시험에는 쓰지 않는다. 이 시험이 성공해도 단일 점 명령의 동작만 확인되는 것이며, MoveIt 경로의 연속 재생과 부드러운 주행은 별도로 검증해야 한다.

오른팔 실기 단일 패킷 결과: 시작 J1 0.08°/엔코더2047, 목표5.08°/1990, 1.26초에 J1 4.57°/1996, 최종 J1 4.48°/1997이었다. 즉 실측 이동 4.4°, 목표 오차 0.6°이며 사용자가 가시적인 움직임을 확인했다. J4는 -2.90→-3.95°로 변했고 다른 관절의 최대 변화는 1.05°였다. 다음 `robot_bridge/trial_drag_sequence.py`는 현재 정지 자세에서 J1 +5/+10/+15°를 0/0.8/1.6초에 한 번씩 보내고 6초간 관절 위치를 기록한다. 이 시험으로 중간 목표를 통과하며 계속 움직이는지 확인한다. 단일 관절의 연속 재생 결과도 다관절 MoveIt 궤적의 부드러움이나 충돌 안전을 입증하지는 않는다.

첫 연속 시험에서는 속도 입력100으로 첫 목표 +5°를 보냈고 0.55초에 J1 4.48→8.61°로 이동했다. 두 번째 목표 +10°를 0.91초에 보낸 직후 측정 코드가 각도/엔코더 불일치를 오류로 처리하고 종료했다. 움직이는 동안 두 값은 별도 시점에 순차로 읽히므로 이 오류는 기구 동작 실패의 증거가 아니다. 세 번째 명령은 보내지 않았다. 수정된 시험에서는 정지 시작 자세에서만 각도/엔코더 일치를 검사하고, 이동 중에는 별도 송신 스레드로 0/0.8/1.6초 송신 시각을 유지하며 위치를 기록한다. 속도 입력은 단일 패킷에서 이동이 확인된 60으로 되돌렸다. `--direction -1`로 현재 J1에서 -5/-10/-15° 방향도 선택할 수 있다. 재시험 전에는 읽기 전용으로 현재 J1 정지 위치를 확인한다.

수정 후 오른팔 J1 연속 시험은 시작13.44°에서 목표8.44°(0.00초), 3.44°(0.81초), -1.56°(1.61초)를 각각 한 번 보냈다. J1은 1.48초에8.43°로 첫 경유점을 지나고 2.40초에2.90°로 두 번째 경유점을 지나 최종-0.87°에 정지했다. 시작 대비-14.31°, 최종 목표 오차0.69°, 다른 관절 최대 변화1.06°였다. 사용자는 동작을 괜찮게 관찰했다. 중간점에서 정지하지 않고 통과하는 동작이 단일 관절에서 관찰됐지만, 샘플링 간격/관절 피드백 잡음 때문에 순간 속도 연속성까지 정량 검증된 것은 아니다. 다음 단계는 PC MoveIt `FollowJointTrajectory`의 관절 위치·시간·속도를 Pi 브리지에서 `SET_ENCODERS_DRAG` 연속 명령으로 변환하는 별도 실행 백엔드다.

### MoveIt 드래그 경로 백엔드 (로컬 구현, 다관절 실기 미검증)

`robot_bridge/sdk_drag_bridge.py`는 별도 `/{side}/sdk_drag_controller/follow_joint_trajectory` 액션을 제공한다. PC의 `trajectory_backend:=sdk_drag`가 이 액션으로 MoveIt 전체 관절 궤적을 전달한다. Pi는 계획 경로를 최대5° 간격/1.5° 기하 오차로 표본화하고, 각 목표의 속도값은 앞뒤 경로점을 이용해 연속적으로 바꾼다. 기본 전송 간격은0.8초, 최대 속도 입력값은 200(실험 상한), 실측 관절보다 앞선 목표 한계10°다. `drag_wrist_speed_scale`은 J4~J6 서보 속도 입력만 별도로 조절한다(기본1.0). `drag_corner_trim_deg`(기본3.0)는 급한 관절공간 모서리를 원래 MoveIt 경로에서 최대 그 값만큼 둥글게 깎아 실행점으로 만든다. 계획의 시간표·속도를 그대로 재생하지 않으며, 펌웨어의 다관절 응답·경로 오차·충돌 안전은 실기로 확인해야 한다. 정지/취소 시 SDK stop 요청만으로 물리 정지가 보증되지는 않는다. 기존 `sdk_path`와 별개라 전환 가능하다.

PC에서 패키지 빌드 후 양쪽 Pi에 `sdk_drag_bridge.py`, `drag_trajectory.py`, `sdk_goal_bridge.py`, `hardware_bridge.py`, `read_joint_states.py`, `trajectory_guard.py`, `trajectory_waypoints.py`, `path_lookahead.py`, `mode_lease.py`를 함께 복사한다. 기존 Pi 브리지는 먼저 종료한다. 양쪽 Pi에서 SDK 모드0와 각도 피드백을 확인한 다음 `sdk_drag_bridge.py --ros-args -r __ns:=/left/right -p robot_side:=left/right -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p goal_tolerance_deg:=5.0 -p gripper_type:=0`로 실행한다. 스무딩 기본값은 위와 같으며 변경한 경우 Pi를 다시 배포해야 한다. PC는 `ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both trajectory_backend:=sdk_drag collision_padding_m:=0.06 rviz:=true`를 사용한다. 첫 실기 실행은 한 팔씩 짧은 빈 공간 경로로 하며 양팔 동시 실행은 기존 게이트가 거부한다.

## 한 노트북 런치에서 두 팔 모두 연결

양쪽 Pi의 SDK 브리지를 아래 역할로 켭니다. 이미 이 설정으로 켰다면 유지합니다.

- 왼팔 **.33**: `-r __ns:=/left -p robot_side:=left -p enable_motion:=true`
- 오른팔 **.22**: `-r __ns:=/right -p robot_side:=right -p enable_motion:=true`
- 양쪽 모두 기존 SDK 명령의 `max_move_deg:=5.0`, `state_hz:=2.0`,
  `native_interpolation:=true`를 유지합니다. 상태 조회 전용 프로그램과 중복 실행하지 않습니다.

노트북에서 기존 MoveIt 런치를 종료한 뒤:

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_LOCALHOST_ONLY
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both
```

RViz에서 `arm_left` / `arm_right` 그룹만 바꾸면 런치 재시작 없이 각 팔을
Plan/Execute할 수 있습니다. 두 SDK 연결은 함께 유지하되 실행은 **한 번에 한 팔**입니다.
팔 또는 그리퍼 실행 중 다른 팔/그리퍼 실행 요청은 전달 전에 거부합니다. 실행 모드에서는
`both_arms` 그룹을 제거하므로 두 팔 동시 계획의 일부만 실행되는 경로는 없습니다.
각 팔의 `trial_j1_plus_3deg` 목표와 근거리 HOME 복귀를 사용할 수 있습니다.
전체 관절 이동 범위와 속도/피드백/궤적 제한은 아래 단일 선택 모드와 같습니다.

## 좌우 그리퍼 실행

Pi의 듀얼 SDK 브리지는 그리퍼 액션을 제공합니다. 181 응답/가짜 열림 표시 수정은
Pi의 `read_joint_states.py`, `sdk_goal_bridge.py`도 갱신해야 합니다(아래 절차).
갱신 후 양쪽 SDK를 `enable_motion:=true`로 켜두고 PC 런치를 재시작합니다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both
```

RViz에서:

| 로봇 | Planning Group | 열기 Goal State | 닫기 Goal State |
|---|---|---|---|
| 왼팔 .33 | gripper_left | open_left | closed_left |
| 오른팔 .22 | gripper_right | open_right | closed_right |

목표 선택 후 Plan → Execute합니다. 열기부터 확인하세요. 모델의 그리퍼 관절값
+0.535는 SDK 100(열기), −0.83은 SDK 0(닫기)로 기존 Pi 코드에서 변환됩니다.
SDK는 마지막 목표를 한 번 보내며 계획의 중간 시각/속도를 재생하지 않습니다.
현재 Pi 코드는 SDK 속도 50을 사용하고, 그리퍼 값이 목표 근처인지와 정지 피드백을
확인한 뒤 성공을 보고합니다. 파지로 인해 끝까지 닫히지 않으면 성공 조건을
만족하지 못할 수 있습니다.

팔·그리퍼·반대쪽 로봇의 모든 PC 요청은 공통 잠금을 사용합니다. 반대 팔뿐 아니라
그리퍼 실행 중 같은 쪽 팔이 움직여도 취소 요청을 보냅니다. 그리퍼 위치 표시는
계속 SDK 근사 변환이며 실제 손가락 형상/개구 폭이 보정된 것은 아닙니다.
노트북 gate의 요청당 5° 제한과 한 관절 목표 제한은 제거했습니다.
실제 관절 한계와 속도/궤적/피드백 검증은 유지합니다. Pi의 `max_move_deg`는 별도이며
아래 명령 예시는 계속 5°로 설정돼 있습니다. 그리퍼 범위는 [−0.83, +0.535]입니다.

그리퍼 실행 중 SDK 조회로 팔 상태 수신이 잠시 지연되는 경우에 한해 최대 3초까지
기존 측정값을 기다립니다. 새 목표 수락과 팔 이동은 계속 1.5초 기준입니다.
통합 상태 발행기는 1.5초가 넘은 데이터를 계속 발행하지 않으며, PC 그리퍼 gate만
짧은 공백을 기다립니다. 3초 초과, 팔 움직임 또는 SDK 실패 시 취소/오류 잠금은 유지합니다.

## 한 팔씩 실제 SDK 실행 시험

PC의 기존 듀얼 MoveIt 런치는 종료합니다. 새 로봇(.33, left)의 Pi에서
`read_joint_states.py`를 종료하고 아래 명령으로 교체합니다. right Pi의 상태
조회와 카메라 송신은 유지합니다. Pi의 기존 SDK 브리지 파일을 그대로 사용합니다.

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_LOCALHOST_ONLY
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_goal_bridge.py \
  --ros-args -r __ns:=/left -p robot_side:=left \
  -p enable_motion:=true -p state_hz:=2.0 \
  -p max_move_deg:=5.0 -p native_interpolation:=true
```

PC에서(빌드 후 새 터미널은 install 환경을 다시 source):

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
unset ROS_LOCALHOST_ONLY
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=left
```

초기 목표는 RViz에서 **arm_left → Goal State `trial_j1_plus_3deg`**를 선택합니다.
이 목표는 시작 시 측정한 현재 자세에서 1번 관절만 +3°인 계획 목표입니다.
Velocity/Acceleration Scaling은 각각 0.1로 놓고 Plan으로 확인한 뒤 Execute합니다.
팔 자세가 바뀌면 이 목표는 더 이상 새 현재값에서 +3°가 아니므로 다시 계획하고
측정 상태와 비교하세요. 홈 또는 임의의 멀리 있는 마커 목표로 시험하지 마세요.

- `left/right`에서는 선택한 팔 컨트롤러 하나만 등록됩니다. `both`에서는 두 컨트롤러를
  모두 등록하며, 공통 gate가 한 번에 한 팔 요청만 허용합니다.
- 실행 모드에서는 `both_arms` 그룹을 제거해 부분 실행으로 갈라지는 요청을 막습니다.
- PC gate는 다른 팔 관절 이름, 실제 하드웨어 한계 초과, 시작 자세 불일치,
  0.08rad/s 초과 및 SDK가 재현할 수 없는 미리보기 우회/반전 궤적을 거부합니다.
  일반 목표와 HOME 모두 여러 관절 이동을 허용합니다. 하드웨어 한계와 Pi의 이동 제한은 적용됩니다.
- 두 팔 실측 상태가 모두 최신이어야 요청을 전달하며, 실행 중 반대 팔의 관절값이
  0.5° 넘게 바뀌거나 상태가 끊기면 원격 취소를 요청합니다. 취소 요청이 실제 정지를
  검증하는 것은 아닙니다. 실행 도중 사람이 다른 팔을 움직이지 마세요.
- 원격 전달 뒤 실패 시 fault를 유지해 후속 실행을 거부합니다. 실제 로봇 정지와
  상태를 확인한 뒤 PC 런치를 재시작해야 합니다. PC 프로세스 강제 종료/네트워크
  단절 중 물리적 정지가 보장되지는 않습니다.
- 이 SDK 시험 gate는 충돌 없는 실제 경로 추종을 보장하지 않습니다. 작은 단일 관절
  시험이라도 실제 팔 주변 공간을 확인하고 Plan의 우회 경로에 의존하지 마세요.
- 같은 PC/ROS domain에서는 실행 gate 하나만 허용합니다. 동시에 두 실행 런치를
  켜지 마세요. 외부 프로그램에서 Pi SDK 액션을 직접 호출하는 것은 별도 경로입니다.
- 오른팔 시험은 left Pi를 조회 전용으로 되돌린 뒤 right Pi에 SDK 브리지를 실행하고,
  Pi 명령의 `/left`, `robot_side:=left`를 각각 `/right`, `robot_side:=right`로,
  PC 옵션을 `execution_arm:=right`로 바꿉니다. 그룹은 `arm_right`를 선택합니다.
- SDK native mode를 사용한 Pi 브리지는 Ctrl+C로 정상 종료해 원래 모드 복원을
  확인하세요. 복구 기록이 남으면 해당 듀얼 브리지 폴더의 restore 스크립트를 사용합니다.

## 그리퍼 181 응답과 가짜 OPEN 표시 수정

실제 양쪽 Pi의 `get_gripper_value(3)`이 181을 반환해 0..100 피드백으로 사용할 수
없었습니다. 종전 PC의 0.535(열림) 기본값 때문에 MoveIt이 목표가 이미 만족됐다고
판정해 SDK 명령을 생략했습니다. 그 기본값은 제거했습니다.

- `gripper_type:=0`(새 기본값)은 **get_gripper_value()처럼 인자를 생략**합니다.
  API에 숫자 0을 보내는 뜻이 아닙니다. 기존 setter와 프로젝트 원본 조회 방식에 맞췄습니다.
- 1/3/4는 해당 SDK 타입을 명시적으로 보냅니다. 타입을 바꾸는 것은 조회 방식만
  바꾸며 그리퍼 보정값이나 서보 설정을 쓰지 않습니다.
- 유효하지 않은 값은 `/left|right/gripper_value`에 −1로 알려 기존 추정값도 무효화합니다.
  실제 위치 181을 임의로 100으로 제한하거나 환산하지 않습니다.
- PC는 유효 피드백이 없으면 그리퍼를 열린 상태로 발행하지 않습니다.
  실행 gate도 최근 5초 내 0..100 피드백이 없으면 그리퍼 요청을 거부합니다.
- 그리퍼가 실행되는 동안 Pi가 읽은 유효한 값도 발행해 표시가 오래되지 않게 합니다.

PC MoveIt과 양쪽 Pi SDK를 Ctrl+C로 정상 종료한 후 **PC에서**:

```bash
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/{read_joint_states,sdk_goal_bridge}.py \
  jetcobot@192.168.0.33:~/jetcobot_dual_bridge/
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/{read_joint_states,sdk_goal_bridge}.py \
  jetcobot@192.168.0.22:~/jetcobot_dual_bridge/
```

**왼팔 Pi .33:**

```bash
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_goal_bridge.py \
  --ros-args -r __ns:=/left -p robot_side:=left \
  -p enable_motion:=true -p state_hz:=2.0 -p max_move_deg:=5.0 \
  -p goal_tolerance_deg:=5.0 \
  -p native_interpolation:=true -p gripper_type:=0
```

**오른팔 Pi .22:**

```bash
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_goal_bridge.py \
  --ros-args -r __ns:=/right -p robot_side:=right \
  -p enable_motion:=true -p state_hz:=2.0 -p max_move_deg:=5.0 \
  -p goal_tolerance_deg:=5.0 \
  -p native_interpolation:=true -p gripper_type:=0
```

각 Pi는 기존처럼 ROS Jazzy 환경과 ROS_DOMAIN_ID=42를 설정한 터미널에서 실행합니다.

`sam_yaw_moveit.py`의 60 mm 수직 하강은 Pi의 새 `/{side}/sdk_linear_descend_60mm` 서비스에서 SDK `send_coords(..., mode=1)`로 실행합니다. `native_interpolation:=true`와 갱신된 `sdk_goal_bridge.py`가 필요합니다. 서비스가 없으면 하강과 그리퍼 닫기를 중단합니다.

ROI 수평 정렬은 카메라 기하로 계산한 XY 보정량(최대 80 mm)을 한 번에 명령합니다. `/{side}/sdk_linear_xy_tenth_mm` 서비스는 `example_interfaces/AddTwoInts`의 a/b에 X/Y 보정량을 0.1 mm 단위 정수로 받으며, 응답 sum=1이면 성공입니다. Pi에 최신 `sdk_goal_bridge.py`를 복사하고 브리지를 재시작해야 합니다. 이동 후 자동 하강·그리퍼 닫기는 하지 않으며, 실시간 카메라에서 ROI를 다시 그려 u=320과의 픽셀 오차를 확인합니다.
PC는 `execution_arm:=both`로 재시작한 뒤, 먼저 다음 읽기 전용 명령으로 확인합니다.

```bash
ros2 topic echo /left/gripper_value --once
ros2 topic echo /right/gripper_value --once
ros2 topic echo /jetcobot_dual/state_status --once
```

0..100과 `sdk_estimate`가 확인돼야 그리퍼 실행을 진행합니다. −1이나 181 관련
경고가 계속되면 기본 getter도 해당 장비의 위치를 유효하게 읽지 못한 것이므로,
추가 펌웨어/그리퍼 종류 확인이 필요합니다. 이 수정의 하드웨어 효과는 재배포 후
확인해야 하며, 가짜 상태로 우회하지 않습니다.

## 검증

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
python3 -m pytest -q ~/pinky_ws/src/jetcobot_dual_real/test
```

현재 세션의 실제 수행 결과와 남은 한계는 `VALIDATION.md`를 참조하세요.

## Hybrid dual-arm execution

Use `trajectory_backend:=sdk_hybrid` to select an SDK endpoint command only for a straight MoveIt joint path with at least 0.10 m conservative separation between both arms' collision-mesh bounding spheres. Curved or closer paths use sparse SDK waypoints. Missing collision geometry also selects waypoints. The gate rechecks measured positions before forwarding and runs only one arm at a time.

On each Pi, run `sdk_hybrid_bridge.py` instead of the standalone goal or path bridge. It is the only process that should open that arm's serial connection. Keep the previous Pi path-bridge parameters, including `enable_motion:=true`, `native_interpolation:=true`, and the matching `robot_side`/ROS namespace. Do not add a small arbitrary `max_move_deg` cap; the bridge default is 180 degrees while physical joint ranges remain enforced. It exposes separate `/SIDE/sdk_endpoint_controller/follow_joint_trajectory` and `/SIDE/sdk_path_controller/follow_joint_trajectory` actions with a shared busy lock. Its path defaults to one-point lookahead, a 6-degree maximum joint step, and 0.5-degree geometric deviation from the MoveIt plan.

Copy the updated bridge files to both Pis, then start one bridge per Pi after stopping the previous serial bridge:

```bash
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/*.py jetcobot@192.168.0.33:~/jetcobot_dual_bridge/
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/*.py jetcobot@192.168.0.22:~/jetcobot_dual_bridge/
# On left Pi (.33):
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_hybrid_bridge.py --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0
# On right Pi (.22):
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_hybrid_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0
```

Use ROS_DOMAIN_ID=42 and the established network discovery settings on each machine.

Build and source `jetcobot_dual_real` on the PC, then run:

```bash
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both trajectory_backend:=sdk_hybrid
```

The SDK does not replay MoveIt timing or prove the continuous physical path. The 0.10 m separation is a conservative routing threshold, not a certified collision distance. Base placement, calibration, gripper shape, held objects, smoothness, and stop behavior need physical verification before close-arm operation.

### Collision-scene endpoint/drag routing (experimental)

`trajectory_backend:=sdk_drag_hybrid` uses MoveIt's `/jetcobot_dual/check_state_validity` service to sample the direct joint-space segment from measured start to planned endpoint. If every sample is valid, it sends only start and endpoint through the SDK goal action. If a sample collides, it reduces and rounds the already planned MoveIt detour using the drag bridge's waypoint reducer, then checks each segment of that exact reduced path against the same MoveIt scene before selecting the drag action. A missing service, timeout, invalid start, or colliding reduced path rejects the motion; it never falls back to the endpoint route after finding a direct-path collision.

The PC collision checker samples joint interpolation at `hybrid_check_step_deg` (default 1 degree). This is discrete scene validation, not continuous collision detection and not a guarantee of the SDK's firmware interpolation. The MoveIt scene must contain the relevant obstacles and current opposite-arm state. The PC reducer settings must match the Pi drag settings: `hybrid_drag_step_deg` ↔ `drag_step_deg`, `hybrid_drag_deviation_deg` ↔ `drag_deviation_deg`, and `hybrid_drag_corner_trim_deg` ↔ `drag_corner_trim_deg`. Keep these equal; mismatched settings invalidate the pre-dispatch path check.

This backend needs one `sdk_drag_hybrid_bridge.py` process per active Pi. It exposes endpoint and drag actions on one shared serial connection. Stop the existing bridge for that arm before starting it:

```bash
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/*.py jetcobot@192.168.0.33:~/jetcobot_dual_bridge/
scp ~/pinky_ws/src/jetcobot_dual_real/robot_bridge/*.py jetcobot@192.168.0.22:~/jetcobot_dual_bridge/
# Left Pi (.33)
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_drag_hybrid_bridge.py --ros-args -r __ns:=/left -p robot_side:=left -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0 -p drag_speed:=200 -p drag_step_deg:=5.0 -p drag_deviation_deg:=1.5 -p drag_corner_trim_deg:=3.0 -p drag_send_interval_s:=0.4
# Right Pi (.22)
~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_drag_hybrid_bridge.py --ros-args -r __ns:=/right -p robot_side:=right -p enable_motion:=true -p native_interpolation:=true -p state_hz:=2.0 -p gripper_type:=0 -p drag_speed:=200 -p drag_step_deg:=5.0 -p drag_deviation_deg:=1.5 -p drag_corner_trim_deg:=3.0 -p drag_send_interval_s:=0.4
```

Build and source the updated PC workspace, then start the new route with:

```bash
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=both trajectory_backend:=sdk_drag_hybrid collision_padding_m:=0.06 rviz:=true hybrid_check_step_deg:=1.0 hybrid_drag_step_deg:=5.0 hybrid_drag_deviation_deg:=1.5 hybrid_drag_corner_trim_deg:=3.0
```

Start with one arm and a short, well-cleared plan. The endpoint route intentionally does not replay MoveIt's intermediate trajectory points. The drag route follows a reduced approximation; its sampled checks do not prove the actual continuous hardware path collision-free.

### 선행 경유점 실측 통과 판정 수정 (2026-11-12)

SDK mode 0에서 `queue_lookahead:=true`이면 다음 경유점이 이미 대기 중이므로 중간 목표에 정확히 정착하지 않고 다음 선분으로 진행할 수 있다. 이전 구현은 현재 목표의 2° 오차만 기다려 `Waypoint did not settle`로 실패할 수 있었다. 이제 계획된 다음 관절 선분의 20% 이상으로 실측 진행하고 그 선분과의 최대 관절 오차가 기존 경유점 허용 오차 이내일 때만 통과로 처리한다. 경로 밖으로 벗어나거나 진전이 없으면 실패와 SDK stop 요청을 유지한다. 계획 경로는 Pi에서 명령 전에 모두 선택하지만 SDK에 전체 경로를 일괄 업로드하는 기능은 사용하지 않는다. 한 점만 선행 전송한다.

이 수정은 기존 `sdk_path_bridge.py`에도 적용되므로, 가장 잘 동작하던 `trajectory_backend:=sdk_path` 구성을 유지하면서 비교할 수 있다. Pi에는 갱신된 `sdk_goal_bridge.py`, `sdk_path_bridge.py`, `trajectory_waypoints.py`를 함께 복사해야 한다. 하이브리드 모드를 쓰는 경우 `sdk_hybrid_bridge.py`도 함께 복사한다. 실제 경로·충돌 거리와 부드러움은 실기 검증 전이다.

### 선택형 두 점 선행 전송 시험 (2026-11-12)

한 점 선행 로그에서 2번 지점 실측은 이미 다음 지점의 83%까지 진행했는데 4번 명령은 그 뒤에 전송됐다. 5번 지점도 다음 목표를 11% 지난 뒤 7번 명령이 전송됐다. 이 경우 PC 피드백을 기다리는 사이 SDK 큐가 비어 순간 정지할 가능성이 있다. 펌웨어 자체 감속 여부는 이 로그만으로 확정할 수 없다.

`queue_lookahead_depth:=2`는 `queue_lookahead:=true`일 때 현재 지점보다 최대 두 지점을 선행 전송한다. 기본값 1은 기존 동작이다. 취소나 실패 시 SDK stop 다음 `clear_queue()`를 요청한다. SDK에 `clear_queue`가 없으면 두 점 선행 모드 시작을 거부한다. 대기 중인 명령이 늘어 정지 반응과 실제 경로의 불확실성이 커지므로 두 팔이 충분히 떨어진 빈 공간에서만 첫 시험을 한다. 실제 SDK 큐 삭제와 물리적 정지는 실기에서 확인해야 한다.

왼팔 시험 옵션: `-p queue_lookahead:=true -p queue_lookahead_depth:=2 -p path_step_deg:=6.0 -p min_command_interval_s:=0.5`. 기존 브리지 종료 후 갱신된 `sdk_path_bridge.py`를 Pi에 복사하고 재시작한다. PC `trajectory_backend:=sdk_path`는 그대로 유지한다.

## 2026-11-12 close-arm planning margin

A right-arm SDK path stopped at waypoint 2/42: the measured joint-space deviation from the next segment was 2.0021° against a 2.00° corridor. The user reported that the planned route was very close to the other arm and may have touched. Do not raise waypoint tolerance or retry that route to clear the error. Inspect the physical arms and calibration first.

The PC launch now sets MoveIt `robot_description_planning.default_robot_padding` to 0.05 m per collision link by default. This is a provisional planning margin, not measured physical clearance or a guarantee that the SDK follows the trajectory. It can cause previously valid goals to fail planning. Keep `execution_arm:=none` until the new route, actual base placement, and the suspected contact are checked. After the left arm has been moved to a clear position, start plan-only mode on the PC:

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42 ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export ROS_STATIC_PEERS="192.168.0.22;192.168.0.33"
ros2 launch jetcobot_dual_real dual_real.launch.py execution_arm:=none collision_padding_m:=0.05 rviz:=true
```

Plan again from the current measured states. Review the entire route in RViz and verify physical separation at the closest approach. A valid plan in MoveIt is not evidence of safe physical execution, especially while SDK timing and path following differ from the planned trajectory. Do not reduce this margin just to force a route through a narrow gap.

## 2026-11-12 right path speed trial

The path bridge now accepts `path_sdk_speed` (SDK integer 1..100, default 10) for waypoint `send_angles`. This does not change endpoint, gripper, or Cartesian correction speeds. The right-arm 2-point lookahead log showed 34 commands and a measured 2.61° deviation from the second-next segment at waypoint 2, above the existing 2° waypoint corridor. Speed alone does not prove the source of that deviation.

The earlier right-arm trial used `-p path_sdk_speed:=15 -p path_step_deg:=6.0 -p min_command_interval_s:=0.5 -p queue_lookahead:=true -p queue_lookahead_depth:=2`, with `waypoint_tolerance_deg` held at 2.0. This is the historical comparison setting, not the next trial recommendation. Do not run the close-arm trajectory while physical contact remains unresolved.

## 2026-11-13 command interval comparison

The user reported rhythmic pauses and rebound with the 0.5 s minimum send interval. Compare a 1.0 s minimum interval first, keeping the same right-arm path, `path_sdk_speed:=15`, `path_step_deg:=6.0`, `queue_lookahead:=true`, `queue_lookahead_depth:=2`, and `waypoint_tolerance_deg:=2.0`. Change only `-p min_command_interval_s:=0.5` to `-p min_command_interval_s:=1.0` in the Pi bridge startup command. This is a comparison setting, not a verified improvement. Restart the bridge because it reads the parameter at startup. Record each `PATH SEND` gap, measured path deviation, and whether the physical motion pauses or rebounds. The minimum send interval is not a fixed movement duration; longer gaps can also increase pauses if the SDK queue empties.

Before another two-point lookahead motion, confirm the right Pi bridge version and that no previous queued motion remains. The previous `clear_queue()` reply was `-1`, so queue removal was not confirmed. If `clear_queue()` fails again, stop the trial and inspect the Pi SDK/serial response. Review the complete MoveIt route and physical arm separation before executing it.

**Observed 2026-11-13:** The right bridge logged `PATH SEND` gaps of 1.00–1.01 s with 24 SDK commands, so the 1.0 s minimum interval was applied. At waypoint 11/24, fresh Pi feedback showed the arm beyond the next segment, but 2.65° from the following segment, outside the 2.0° waypoint corridor; the bridge timed out and requested SDK stop. `clear_queue()` again returned `-1`, so queued-command removal remains unconfirmed and the bridge rejects further arm goals. The PC execution gate had already aborted about 1.8 s earlier because the combined joint state became stale. Two later read-only right-arm joint samples about 10 s apart were identical; this does not prove that the SDK queue was cleared. Do not repeat the two-point lookahead trial or restart the bridge to clear its lockout until the robot and Pi SDK/serial response are inspected. Objective smoothness remains unmeasured.

The user subsequently reported that the 1.0 s trial felt smoother specifically because the arm did not stop at each waypoint before the failure. This is a qualitative observation; no full-path completion or measured smoothness comparison was obtained. Keep two-point lookahead as the intended architecture for smooth MoveIt path following, while resolving stop/queue acknowledgement and measured path deviation before another execution trial.

**Follow-up:** A direct right-Pi SDK call initially raised a serial read exception while the right path bridge was still publishing; two processes may have accessed the port. After the bridge stopped publishing, a direct SDK call returned `get_angles` and `get_fresh_mode=0` successfully, but `clear_queue()` still returned `-1`; angles before and after were identical. The SDK documentation specifies `1` for a confirmed queue clear, while its local implementation can return `-1` when no valid reply is received. Treat queue state as unknown; do not use two-point lookahead until an exclusive-port test and firmware/SDK compatibility check establish a reliable clear response.

The exclusive-port follow-up showed no `fuser` owner, `pymycobot` 4.0.7, `get_system_version()` 6.5, and `check_async_or_sync()` 1. Manufacturer changelog says the myCobot 280 `stop()` return value applies to Atom firmware 7.0 and later; it does not state that upgrading will fix `clear_queue()`. On this reported 6.5 firmware, do not treat a stop request or an unconfirmed queue clear as proof of physical stop. Firmware changes require a separate model-specific review.

The real dual-arm model overrides the source JetCobot URDF joint limits with myCobot-style limits. In particular, real MoveIt/Pi J5 is currently -165..165 deg, while the unmodified JetCobot URDF uses about -143.2..143.2 deg and Yahboom's public JetCobot overview gives a general -153..153 deg rotation range. These are not evidence of the exact firmware limits on this user's two arms. Read each arm's `get_joint_min_angle()` and `get_joint_max_angle()` values without motion before changing model limits. The previous claim that matching MoveIt and Pi limits established physical correctness was too strong.

Right-Pi firmware readback then returned J1 -168..168, J2 -135..135, J3 -150..150, J4 -145..145, J5 -165..165, and J6 -180..180 deg, exactly matching the current real MoveIt and Pi limits. Thus the right-arm 180-deg movement-envelope rejection and extreme pose-marker route are not explained by a right-arm joint-limit mismatch. Left-Pi limits were not read in this follow-up. The right-arm pose goal/IK branch and actual planned joint trajectory still need plan-only inspection. Queue-clear acknowledgement remains unresolved independently.

**2026-11-13 waypoint pass-through observation:** With 1.0 s send gaps, depth 2, 6-degree selected steps, and SDK speed 15, the right arm advanced toward selected waypoint 3 while the bridge still evaluated waypoint 1. Its 12.47-degree error was relative to the old waypoint 1, whereas deviation from the nearby later planned segment was 2.30 degrees. Because this exceeded the 2.0-degree intermediate tolerance, the follower sent no further commands and timed out at waypoint 1/16. Fresh angle readings repeated the same pose, so the arm was stopped while the follower waited. This does not establish that the route was physically safe: the actual continuous SDK trajectory and inter-arm clearance were not measured. A subsequent trial at `waypoint_tolerance_deg:=3.0` and PC `collision_padding_m:=0.06` reached its goal according to the user, but it paused at selected waypoints. The hard 6-degree joint corridor and planned-path waypoint geometry remain unchanged. A wider angle tolerance and 6 cm planning padding are not a collision guarantee. Resolve the unconfirmed `clear_queue()` and post-failure recovery before executing another queued route.

**Smooth-path preference (2026-11-13):** The user reports the path now reaches its goal but stops at every selected SDK waypoint. The desired result is a smooth curve that stays near the MoveIt avoidance path; exact waypoint arrival is not required. The last reported working trial used a 3.0-degree intermediate waypoint tolerance and 0.06 m PC collision padding. Two-point lookahead and the 1.0 s minimum send interval felt smoother than 0.5 s but did not eliminate stops. Sparser waypoint selection can reduce the number of stops; it cannot make SDK mode 0 blend through each target. The separate `sdk_refresh_trial_bridge.py` now projects measured position onto the planned joint path and refreshes an ahead target in SDK mode 1. Offline geometry tests pass; no Pi deployment or hardware smoothness/collision validation has occurred. Keep the working mode-0 path bridge available for return after the trial.
