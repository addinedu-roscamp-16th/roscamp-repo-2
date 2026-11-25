# service_bridge

주문 흐름의 서비스 브리지(1차 조각). 웹이 낸 주문을 DB에 두고, central에 `CreateTrip`을 부르고, fleet이 보내는 `ReportTrip`을 받아 주문 상태에 반영합니다.

- `service_bridge/core/`: ROS 없는 판단 코드(DB 트랜잭션, 상태 전이, 재시도). `pytest`만으로 시험합니다.
- `service_bridge/ros/`: rclpy 노드와 어댑터.
- `service_bridge/main.py`: `bridge` 진입점.
- `service_bridge/tools/`: 시험 도구(`setup_db`, `submit_trip`, `fake_fleet`).

central 쪽 `CreateTrip` 처리는 `pc/central`에 있습니다.

## 실행 · 시험

환경 준비(가상환경, `source` 순서, 도메인)와 명령은 [`docs/개발_규칙.md`](../../docs/개발_규칙.md)의 "주문 처리(브리지) 개발 환경"을 따릅니다. 여기에는 옮겨 적지 않습니다.

| 하는 일 | 명령 |
|---|---|
| 브리지 | `ros2 run service_bridge bridge --ros-args -p mode:=TEST` |
| central | `ros2 run central central` |
| 시험 DB 만들기 (이름이 `_test`로 끝날 때만) | `ros2 run service_bridge setup_db` |
| 운반 건 넣기 | `ros2 run service_bridge submit_trip --items A:1,C:1` |
| fleet 대역 | `ros2 run service_bridge fake_fleet` |
| core 시험 | `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q` |
| central 시험 | `colcon test --packages-select central` |

## 관제 fleet과 붙일 때

`fake_fleet`은 관제의 `fleet_manager_node`를 대신하므로 진짜 fleet과 같이 켜지 않습니다. `sim/fake_robots` 런치도 `fake_central:=false`로 띄웁니다(central은 `pc/central`의 것을 씀).

## 설계 · 계획

- 설계: [`docs/superpowers/specs/2026-11-25-bridge-slice1-design.md`](../../docs/superpowers/specs/2026-11-25-bridge-slice1-design.md)
- 계획: [`docs/superpowers/plans/2026-11-25-bridge-slice1.md`](../../docs/superpowers/plans/2026-11-25-bridge-slice1.md)
- 주문 DB: [`design/db/README.md`](../../design/db/README.md)
