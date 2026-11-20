# jetcobot_dual_control (PC)

양팔 12관절 모델, 상태 합치기, MoveIt, 안전 게이트를 담당한다. 인터페이스는 [../INTERFACES.md](../INTERFACES.md) 3절에 있다.

```bash
ros2 launch jetcobot_dual_control system.launch.py          # 표준(11-19 최종 인자)
ros2 launch jetcobot_dual_control dual_real.launch.py ...   # 인자를 직접 지정
```

| 파일 | 역할 |
| --- | --- |
| `jetcobot_dual_control/model.py` | `jetcobot_gazebo` URDF와 `config/layout.yaml`로 양팔 URDF·SRDF 생성 |
| `state_aggregator.py` | 두 팔 측정 상태 → `/jetcobot_dual/joint_states`(둘 다 최신일 때만) |
| `execution_gate.py` | MoveIt 컨트롤러 역할. 실측 상태와 충돌을 검사한 뒤 Pi 액션으로 전달. 목표는 한 번에 하나 |
| `coordinated_route.py`, `clearance.py` | 12관절 경로 구간 나누기, 팔 간 여유 거리 |
| `tin_case_scene.py`, `pick_object_scene.py` | 계획 장면에 케이스와 물체 추가 |
| `config/` | `layout.yaml`(두 베이스 배치), `tin_case.yaml`, `pick_objects.yaml`, RViz 설정 |

- symlink-install로 빌드하면 파이썬 수정은 launch 재시작만으로 반영된다.
- 원본 패키지 `jetcobot_dual_real`의 이전 README와 검증 기록은 `../docs/history/`에 있다.
