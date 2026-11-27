# jetcobot_dual — 양팔 JetCobot 협동 픽앤플레이스

myCobot 280(JetCobot) 두 대가 스테레오 카메라로 상품을 인식해 집고, 틴케이스(바구니)에 담는다. 한 팔이 담는 동안 다른 팔이 집는 동시 진행을 쓴다. 환경은 ROS 2 Jazzy와 MoveIt 2다.

팀 프로젝트에서 이 양팔은 **Phody**(작업대 `WORK`, `arm_left` · `arm_right`)이고, 팀 관제(fleet)의 담기 명령을 받아 움직인다. 관제는 팀 저장소 `addinedu-roscamp-16th/roscamp-repo-2`의 `dev` 브랜치(`pc/fleet_manager` 등)에 있고, Phody 쪽 연결 코드는 `jetcobot_dual_app/scripts/multi_pick/fleet_link.py`, 시험용 가짜 관제는 같은 폴더의 `fake_fleet.py`다. 자세히는 [jetcobot_dual_app/README.md](jetcobot_dual_app/README.md)의 "팀 관제 연결"과 [INTERFACES.md](INTERFACES.md) 6절.

## 구성

| 패키지 | 실행 위치 | 내용 |
| --- | --- | --- |
| `jetcobot_dual_bridge` | **Pi 2대**(팔마다 1대) | pymycobot SDK 브리지. 관절 상태 발행, 경로·끝점·그리퍼 액션, 미세조정 서비스 |
| `jetcobot_dual_control` | PC | 12관절 모델, `state_aggregator`, MoveIt `move_group`, 안전 게이트 `execution_gate`, 장면(케이스·물체), RViz |
| `jetcobot_dual_app` | PC | 픽 UI(스테레오 YOLO-seg 인식, 예전 SAM2 · LightGlue는 `--seg sam`), 팔별 상태 머신, 접근 · 미세조정 · 잡기 · 담기 worker, 팀 관제 연결(`fleet_link.py`) · 가짜 관제(`fake_fleet.py`) |

- `docs/worklog/`: 날짜별 작업 기록(11-06 ~ 11-27)과 [양팔 협동 상태 설계](docs/worklog/2026-11-20_양팔_협동_상태_설계.md). 가장 최근 `*_할일.md`부터 읽는다
- `docs/구간_정의.md`: 동작 구간 A(see_center → 픽 → see_center) · B(see_center ↔ pre_place) · C(pre_place → 놓기 → pre_place)
- `docs/bridge_study.md`: 브리지 구조 해설
- `docs/history/`: 이전 PC 패키지 README와 검증 기록
- [INTERFACES.md](INTERFACES.md): 토픽, 액션, 서비스, 파일 인터페이스

## 의존성

- PC
  - ROS 2 Jazzy, MoveIt 2
  - 같은 워크스페이스의 `jetcobot_gazebo`(로봇 모델). `jetcobot_dual_control`이 이 패키지의 URDF로 12관절 모델을 만든다.
  - 인식용 가상환경 `~/camera_code/.venv`: torch, ultralytics(YOLO), OpenCV, GStreamer(`gi`). 예전 인식(`--seg sam`)을 쓸 때만 SAM2 · LightGlue
  - YOLO-seg 가중치: 이 저장소 `models/bar_mentos_yolo26s_seg_v2/best.pt`
  - SAM2 가중치(`--seg sam`일 때만) `~/sam2_src/checkpoints/sam2.1_hiera_small.pt`
  - 팀 관제 연결(`fleet:=true`)을 쓸 때: 팀 `fleet_interfaces`(Phody 복사본 `~/phody_fleet_ws`)를 따로 빌드
- Pi
  - ROS 2 Jazzy
  - `~/venv/mycobot`: pymycobot과 rclpy를 둘 다 쓸 수 있는 파이썬
  - `sdk_patch/`의 timed-angles 패치 적용
  - 카메라 송신: GStreamer(`gst-launch-1.0`, x264enc), 카메라 장치 `/dev/jetcocam0`
- 네트워크
  - 세 대(PC, Pi 2대) 모두 같은 통신 설정: `source ~/iponic_net/phody_net.sh dev`(개발: 도메인 83, 서버 없음) 또는 `real`(팀 실물 망: 도메인 6 + 디스커버리 서버, 팀 `tools/net/net_ds.sh` 그대로). `~/iponic_net/`은 PC와 두 Pi에 같은 내용으로 둔다
  - Pi WiFi 절전 끄기(`sudo iw dev wlan0 set power_save off`, 켜져 있으면 관절 상태가 1.5초 넘게 끊김, 11-27 확인)

## 빌드

### PC
```bash
cd ~/pinky_ws
colcon build --symlink-install --packages-select jetcobot_dual_bridge jetcobot_dual_control jetcobot_dual_app
source install/setup.bash
```
PC 게이트는 Pi와 같은 경로 축약 코드를 쓴다. 그래서 PC에서도 `jetcobot_dual_bridge`를 빌드한다.

### Pi (각 Pi에서)
```bash
mkdir -p ~/jetcobot_ws/src
# PC에서 패키지를 보낸 뒤 (PC 터미널):
#   scp -r ~/pinky_ws/src/jetcobot_dual/jetcobot_dual_bridge jetcobot@<Pi IP>:~/jetcobot_ws/src/
source /opt/ros/jazzy/setup.bash
cd ~/jetcobot_ws && colcon build --packages-select jetcobot_dual_bridge
echo 'source ~/jetcobot_ws/install/setup.bash' >> ~/.bashrc
```

## How to use (실행 순서)

모든 터미널(PC, 두 Pi)에서 먼저 `source ~/iponic_net/phody_net.sh dev`를 한다(팀 실물 망이면 `real`). 예전처럼 `export ROS_DOMAIN_ID=83`만 해도 개발 모드와 같다.

0. **Pi 카메라 송신**(각 Pi, 터미널 하나씩 따로)
   ```bash
   ros2 launch jetcobot_dual_bridge camera.launch.py side:=left  host:=192.168.0.15   # 왼팔 Pi → UDP 5000
   ros2 launch jetcobot_dual_bridge camera.launch.py side:=right host:=192.168.0.15   # 오른팔 Pi → UDP 5001
   ```
   - **`host:=`는 픽 UI를 실행하는 PC의 IP로 바꿔야 한다.** 위의 `192.168.0.15`는 현재 PC 값이다. PC에서 `hostname -I`로 확인한다. 빈 값이면 launch가 오류를 내고 멈춘다.
   - 포트는 `side`로 정해진다(왼쪽 5000, 오른쪽 5001). PC UI의 `--left-port`, `--right-port`와 같아야 하며, 바꾸려면 `port:=`를 쓴다.
   - 그 밖의 인자: `device:=/dev/jetcocam0`, `width:=640 height:=480 fps:=30 bitrate:=1500`
   - 직접 실행할 때와 같은 파이프라인이다.
     ```bash
     gst-launch-1.0 -v v4l2src device=/dev/jetcocam0 ! video/x-raw,format=YUY2,width=640,height=480,framerate=30/1 ! videoconvert ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=1500 key-int-max=30 ! rtph264pay config-interval=1 pt=96 mtu=1200 ! udpsink host=<PC IP> port=<5000|5001> sync=false
     ```
1. **Pi 브리지**(각 Pi)
   ```bash
   ros2 launch jetcobot_dual_bridge bridge.launch.py side:=left    # 왼팔 Pi
   ros2 launch jetcobot_dual_bridge bridge.launch.py side:=right   # 오른팔 Pi
   ```
   - 파라미터는 `config/bridge.yaml`에 있다(11-19 최종 명령과 같은 값).
   - 지금 실물에서는 Pi의 `~/jetcobot_dual_bridge/`에서 직접 띄운다:
     ```bash
     source ~/iponic_net/phody_net.sh dev && ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_drag_hybrid_bridge.py left    # 왼팔 Pi
     source ~/iponic_net/phody_net.sh dev && ~/venv/mycobot/bin/python ~/jetcobot_dual_bridge/sdk_drag_hybrid_bridge.py right   # 오른팔 Pi
     ```
   - 다른 파일을 쓰려면 `params_file:=/경로.yaml`, 다른 파이썬을 쓰려면 `python:=/경로/python`.
2. **PC 제어**(MoveIt, 게이트, RViz)
   ```bash
   ros2 launch jetcobot_dual_control system.launch.py tin_case_enabled:=false
   ```
   - 11-19 표준 인자가 기본값으로 들어 있다. 개별 인자는 `rviz:=false`처럼 바꿀 수 있다.
   - 정상 기동 문구: `both SDK connections enabled; coordinated controller enabled`
   - **Ctrl+C로 끈다. Ctrl+Z 금지**(멈춘 프로세스가 남아 다음 실행이 막힌다).
3. **픽 UI**
   ```bash
   ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left concurrent_pick_place:=true fine_moveit_planning:=false
   ```
   - 팀 관제와 연결하려면 끝에 `fleet:=true`를 붙이고, 그 전에 `source ~/phody_fleet_ws/install/setup.bash`를 한다. 관제 없이 시험할 때는 가짜 관제 `fake_fleet.py`를 띄운다([jetcobot_dual_app/README.md](jetcobot_dual_app/README.md)).
   - 수동: 캡처 → `M` YOLO 인식 · 매칭 → 첫 접근 계획 확인 → `E` 실행. 멈춤 `P`, 이어서 하기 `U`, 멈춘 뒤 see_center 복귀 `C`, 처음 화면 `R`.
   - 추가 인자는 `args:='--stereo-fixed-see-center --no-self-correct'`처럼 문자열 하나로 넘긴다.
   - 다른 도구 실행 예:
     ```bash
     ros2 launch jetcobot_dual_app pick.launch.py script:=moveit_pick/compare_board_captures python:=python3 args:='-n 4'
     ```
   - 캡처와 로그는 `jetcobot_dual_app/scripts/sam_yaw_output/`에 쌓인다(git 제외).

게이트 잠금(fault latch) 해제:
```bash
ros2 service call /jetcobot_dual/reset_execution_fault std_srvs/srv/SetBool '{data: true}'
```

## 기존 경로에서 옮겨 온 것 (2026-11-20)

이 저장소는 아래 원본을 **복사**해 정리한 것이다. 원본은 그대로 두었다. 검증을 마치면 원본 실행을 이 저장소 실행으로 바꾼다.

| 원본 | 여기 |
| --- | --- |
| `~/pinky_ws/src/jetcobot_dual_real/robot_bridge/`, `sdk_patch/` | `jetcobot_dual_bridge/scripts/`, `sdk_patch/` |
| `~/pinky_ws/src/jetcobot_dual_real/` (패키지) | `jetcobot_dual_control/` (패키지·모듈 이름 변경) |
| `~/camera_code/state_pick/` + `left_detail.py`, `left_mentos_detail.py` | `jetcobot_dual_app/scripts/` |
| Pi `~/jetcobot_dual_bridge/*.py` 직접 실행 | `ros2 launch jetcobot_dual_bridge bridge.launch.py` |

주의할 점:

- **원본 `jetcobot_dual_real`과 이 패키지는 이름이 달라서 같은 워크스페이스에서 함께 빌드된다.** 그래도 **둘을 동시에 실행하면 안 된다.** ROS 노드와 토픽 이름(`/jetcobot_dual/...`)이 같다.
- 앱의 경로 고정(하드코딩)은 패키지 경로 조회로 바꿨다. 패키지가 빌드되어 있지 않으면 이 저장소의 원본 소스 경로를 대신 쓴다.
- `jetcobot_dual_app/test`는 상태 머신 변경(11-20) 이전 흐름을 가정한 테스트가 있어 갱신이 필요하다.

## 현재 상태와 남은 일

- 최근 정리: [2026-11-27 작업 정리](docs/worklog/2026-11-27_작업정리.md)(팀 관제 연결 2차), 다음 할 일: [2026-11-28 할 일](docs/worklog/2026-11-28_할일.md)
- 그 전: [2026-11-20 작업 기록](docs/worklog/2026-11-20_작업정리.md)의 1절(설계 대비 구현)과 8절(다음 할 일)
