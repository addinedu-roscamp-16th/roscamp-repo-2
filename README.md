# jetcobot_dual — 양팔 JetCobot 협동 픽앤플레이스

myCobot 280(JetCobot) 두 대가 스테레오 카메라로 상품을 인식해 번갈아 집고, 틴케이스(바구니)에 담는다. 환경은 ROS 2 Jazzy와 MoveIt 2다.

## 구성

| 패키지 | 실행 위치 | 내용 |
| --- | --- | --- |
| `jetcobot_dual_bridge` | **Pi 2대**(팔마다 1대) | pymycobot SDK 브리지. 관절 상태 발행, 경로·끝점·그리퍼 액션, 미세조정 서비스 |
| `jetcobot_dual_control` | PC | 12관절 모델, `state_aggregator`, MoveIt `move_group`, 안전 게이트 `execution_gate`, 장면(케이스·물체), RViz |
| `jetcobot_dual_app` | PC | 픽 UI(스테레오 SAM2·LightGlue 인식), 팔별 상태 머신, 접근·미세조정·잡기·담기 worker |

- `docs/worklog/`: 날짜별 작업 기록(11-06 ~ 11-20)과 [양팔 협동 상태 설계](docs/worklog/2026-11-20_양팔_협동_상태_설계.md)
- `docs/bridge_study.md`: 브리지 구조 해설
- `docs/history/`: 이전 PC 패키지 README와 검증 기록
- [INTERFACES.md](INTERFACES.md): 토픽, 액션, 서비스, 파일 인터페이스

## 의존성

- PC
  - ROS 2 Jazzy, MoveIt 2
  - 같은 워크스페이스의 `jetcobot_gazebo`(로봇 모델). `jetcobot_dual_control`이 이 패키지의 URDF로 12관절 모델을 만든다.
  - 인식용 가상환경 `~/camera_code/.venv`: torch, SAM2, LightGlue, OpenCV, GStreamer(`gi`)
  - SAM2 가중치 `~/sam2_src/checkpoints/sam2.1_hiera_small.pt`
- Pi
  - ROS 2 Jazzy
  - `~/venv/mycobot`: pymycobot과 rclpy를 둘 다 쓸 수 있는 파이썬
  - `sdk_patch/`의 timed-angles 패치 적용
  - 카메라 송신: GStreamer(`gst-launch-1.0`, x264enc), 카메라 장치 `/dev/jetcocam0`
- 네트워크
  - 세 대 모두 같은 `ROS_DOMAIN_ID`(83)
  - WiFi 절전 끄기, `ROS_STATIC_PEERS` 권장([2026-11-20 기록](docs/worklog/2026-11-20_작업정리.md) 5절)

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

모든 터미널에서 `export ROS_DOMAIN_ID=83`(또는 `.bashrc`에 넣기)를 먼저 한다.

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
   - 다른 파일을 쓰려면 `params_file:=/경로.yaml`, 다른 파이썬을 쓰려면 `python:=/경로/python`.
2. **PC 제어**(MoveIt, 게이트, RViz)
   ```bash
   ros2 launch jetcobot_dual_control system.launch.py
   ```
   - 11-19 표준 인자가 기본값으로 들어 있다. 개별 인자는 `rviz:=false`처럼 바꿀 수 있다.
   - 정상 기동 문구: `both SDK connections enabled; coordinated controller enabled`
   - **Ctrl+C로 끈다. Ctrl+Z 금지**(멈춘 프로세스가 남아 다음 실행이 막힌다).
3. **픽 UI**
   ```bash
   ros2 launch jetcobot_dual_app pick.launch.py first_arm:=left
   ```
   - 양쪽 영상에서 상품마다 ROI를 지정 → `M` 매칭 → 첫 접근 계획 확인 → `E` 실행.
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

[2026-11-20 작업 기록](docs/worklog/2026-11-20_작업정리.md)의 1절(설계 대비 구현)과 8절(다음 할 일)을 본다.
