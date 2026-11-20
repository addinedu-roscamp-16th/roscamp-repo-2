# 픽업 물체 MoveIt 모델

크기 설정은 [`pick_objects.yaml`](pick_objects.yaml)에 있다. 단위는 **mm**이고, 물체 로컬 X가 긴 가로축, Y가 집게로 물리는 짧은 세로축, Z가 높이다. 카메라가 저장한 `object_long_yaw_deg`로 로컬 X 방향을 회전시킨다.

| 물체 | 입력 크기 | MoveIt 형상 |
|---|---|---|
| 초코바 `chocobar` | 9×3.5×1.5 cm | 90×35×15 mm 직육면체 1개 |
| 멘토스 2개 `mentos_pair` | 각각 지름 1.6 cm, 길이 5 cm; 전체 약 6×3.2×1.6 cm | 지름 16 mm·길이 50 mm 원통 2개. Y로 나란히 붙이고 X는 서로 10 mm 어긋나게 둬서 전체 X=60, Y=32, Z=16 mm |

멘토스의 10 mm 어긋남은 **전체 길이 60 mm**라는 설명을 맞추기 위한 가정이다. 실제 배열이 다르면 YAML의 `centers_xy_mm`를 고친다. 초코바 치수는 cm로 해석했다. 두 물체 모두 **짧은 Y 방향을 잡는 것**으로 정의했다.

## 새 캡처에 물체 종류 기록

평소 UI 명령 끝에 물체 종류를 하나 지정한다.

```bash
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --object-type chocobar
# 또는
~/camera_code/.venv/bin/python ~/camera_code/moveit_pick/manual_roi_drag.py --object-type mentos_pair
```

UI가 저장하는 `moveit_target.json`에 `object_type`이 들어간다. 이 인자는 물체 종류의 **기록용**이며, 현재 픽업 동작과 팔 선택 기준은 바꾸지 않는다. 이 화면은 물체를 자동 분류하지 않는다.

## MoveIt 장면 등록

PC에서 `jetcobot_dual_real` 패키지를 빌드하고 MoveIt 런치를 켠 뒤 아래 명령을 사용한다. `CAPTURE`를 **방금 픽업에 사용한** `capture_...` 폴더의 실제 경로로 바꾼다. 이 명령은 장면만 바꾸며 로봇을 움직이지 않는다.

```bash
cd ~/pinky_ws
colcon build --symlink-install --packages-select jetcobot_dual_real
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
export ROS_DOMAIN_ID=42
```

`G`로 그리퍼를 닫아 물체를 잡은 **직후, L로 들어 올리기 전**에 실행하면 `gripper_close_complete.json`에서 실제 선택 팔을 읽어 그 팔의 TCP에 물체를 붙인다.

```bash
ros2 run jetcobot_dual_real pick_object_scene --ros-args \
  -p target_file:=/home/ane/camera_code/sam_yaw_output/CAPTURE/moveit_target.json \
  -p mode:=attach
```

이후 MoveIt 계획은 부착된 물체를 팔과 함께 충돌 검사한다. `object_type`을 기록하지 않은 이전 캡처라면 `-p object_type:=chocobar` 또는 `-p object_type:=mentos_pair`를 추가한다. 사용자가 물체를 놓은 후 장면에서 부착 물체를 제거할 때는 같은 명령에서 `mode:=remove`를 쓴다. **로봇이 케이스 안에 내려놓고 그리퍼를 열고 빠져나오는 동작은 아직 구현하지 않았다.**

위치와 형상을 미리 RViz에서 볼 필요가 있을 때만 `mode:=world`를 쓴다. 집게가 물체에 닿아야 하는 현재 픽업 계획에서는 그 충돌 물체 때문에 계획이 막힐 수 있으므로, 기존 픽업 중에는 `world` 등록을 기본으로 하지 않았다.

카메라의 `center_xyz_mm`는 현재 물체 표면 근처의 추정점으로 취급하고, MoveIt 형상 중심을 물체 높이의 절반만큼 아래에 둔다. 실제 접촉면 Z·두 멘토스의 상대 위치는 실물과 비교해 보정해야 한다. 부착은 그리퍼 닫기 액션 성공 기록과 현재 TF를 이용한 **기하학적 추정**이며, 물체가 실제로 집게 안에 있는지는 카메라/사용자 확인이 필요하다.
