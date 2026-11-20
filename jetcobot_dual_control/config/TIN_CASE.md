# 오픈 틴케이스 MoveIt 충돌 모델

실물 크기: 외경 **98 × 74 × 60 mm**, 내경 **95 × 71 × 57 mm**. 뚜껑 없이 바닥판(3 mm)과 네 벽(각 1.5 mm)만 충돌 물체로 등록한다. 바닥판 중심을 포함해 하나의 MoveIt 물체 `open_tin_case`로 관리한다.

## 위치 변경

[`tin_case.yaml`](tin_case.yaml)의 다음 세 값만 실측 위치로 바꾸면 된다.

```yaml
frame_id: right_base_link
bottom_center_xyz_mm: [-230.0, 155.0, -10.0]
yaw_deg: 90.0
```

- `frame_id`: `right_base_link` 또는 `left_base_link`. 현재 값은 **오른팔 베이스 기준**이다.
- `bottom_center_xyz_mm`: 케이스 **외부 바닥면의 중앙** 위치 `[X, Y, Z]` (mm). 현재 값은 X=-23 cm, Y=+15.5 cm, Z=-1 cm다. 물체 중심 높이나 내부 바닥 높이가 아니다.
- `yaw_deg`: 베이스 Z축 주위 회전. 현재 90°이므로 외경 98 mm 쪽이 +Y 방향(가로), 74 mm 쪽이 -X 방향(세로)이다.

현재 좌표 `[-230, 155, -10]` mm는 전달받은 배치 위치다. 실제 케이스의 기준점이 외부 바닥면 중앙인지 확인한 뒤 계획을 새로 만들어야 한다. 위치를 바꿀 때는 `tin_case.yaml`을 수정하고 PC MoveIt 런치를 다시 시작하거나, 실행 중인 MoveIt에 아래 명령을 다시 보내면 같은 ID의 충돌 물체 위치를 갱신한다. 로봇 동작은 보내지 않는다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/pinky_ws/install/setup.bash
ros2 run jetcobot_dual_real tin_case_scene --ros-args -p config_file:=/home/ane/pinky_ws/src/jetcobot_dual_real/config/tin_case.yaml
```

처음 한 번 PC에서 패키지 빌드 후 MoveIt 런치를 다시 시작해야 이 노드가 자동 실행된다:

```bash
cd ~/pinky_ws
colcon build --symlink-install --packages-select jetcobot_dual_real
source install/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch jetcobot_dual_real dual_real.launch.py \
  execution_arm:=both trajectory_backend:=sdk_drag_hybrid \
  collision_padding_m:=0.06 rviz:=true
```

케이스 충돌 물체 없이 MoveIt을 시작할 때는 같은 명령에 `tin_case_enabled:=false`를 추가한다. 실행 중인 MoveIt을 재시작한 뒤 사용하며, 이 인자는 `tin_case_scene` 노드만 끈다.

MoveIt RViz Planning Scene에서 `open_tin_case`가 **뚜껑 없이** 나타나는지 확인한다. 이 모델은 충돌 검사 용도다. 케이스 내부에 물체를 내려놓는 경로, 파지 해제 타이밍, 실제 케이스 위치 측정은 별도 구현이 필요하다. 케이스가 놓인 뒤 기존 접근 계획을 그대로 쓰지 말고 새 planning scene에서 계획을 다시 만든다.

픽업 물체(초코바·멘토스)의 치수와 팔 부착 명령은 [`PICK_OBJECTS.md`](PICK_OBJECTS.md)에 정리했다.
