# service_bridge

주문 흐름의 서비스 브리지. fleet의 `Hello`와 `ReportTrip` 서버(보고를 DB 주문 상태에 반영)이고 `/system/mode`를 발행합니다. 2단계부터 같은 프로세스에서 주문 접수 웹(`POST /api/orders`, `GET /api/snapshot`, `/ws/customer` · `/ws/admin`)을 열고, 접수한 운반 건을 브리지가 직접 central에 `SubmitTrip`으로 넘깁니다.

- `service_bridge/core/`: ROS 없는 판단 코드(DB 트랜잭션, 상태 전이, 재시도). `pytest`만으로 시험합니다.
- `service_bridge/ros/`: rclpy 노드와 어댑터.
- `service_bridge/web/`: FastAPI 앱(REST · WS · LISTEN 사건 전달). ROS를 부르지 않습니다.
- `service_bridge/main.py`: `bridge` 진입점. 웹(uvicorn)은 메인 스레드, 노드 실행기는 뒷 스레드에서 돕니다.
- `service_bridge/tools/`: 시험 도구(`setup_db`, `submit_trip`, `fake_fleet`, `order`, `ws_watch`).

central 쪽 `SubmitTrip` 처리는 `pc/central`에 있습니다.

## 실행 · 시험

환경 준비(가상환경, `source` 순서, 도메인)와 명령은 [`docs/개발_규칙.md`](../../docs/개발_규칙.md)의 "주문 처리(브리지) 개발 환경"을 따릅니다. 여기에는 옮겨 적지 않습니다.

| 하는 일 | 명령 |
|---|---|
| 브리지(웹 포함) | `ros2 run service_bridge bridge --ros-args -p mode:=NORMAL` (`TEST`면 주문 503 `MAINTENANCE`) |
| central | `ros2 run central central` |
| 시험 DB 만들기 (이름이 `_test`로 끝날 때만) | `ros2 run service_bridge setup_db` |
| 주문 보내기 | `ros2 run service_bridge order --account 1 --items 3:2,5:1 [--key <UUID>] [--url http://localhost:8000]` (202면 0으로 끝남) |
| WS 보기 | `ros2 run service_bridge ws_watch --account 1` 또는 `--admin` (한 줄에 메시지 하나, JSON) |
| 운반 건 직접 넘기기 | `ros2 run service_bridge submit_trip --items A:1,C:1` (central만 손으로 볼 때. 2단계부터는 브리지가 직접 넘기므로 브리지와 같이 쓰면 같은 건을 두고 겹칠 수 있음) |
| fleet 대역 | `ros2 run service_bridge fake_fleet` |
| core 시험 | `~/venv/iponic/bin/python -m pytest pc/service_bridge/test -q` |
| central 시험 | `colcon test --packages-select central` |

웹 주소는 환경 변수 `IPONIC_WEB_HOST`(기본 `0.0.0.0`) · `IPONIC_WEB_PORT`(기본 `8000`), DB는 `IPONIC_DB_DSN`. 끌 때는 Ctrl+C 한 번(uvicorn이 신호를 받고, 웹을 닫은 뒤 노드 · 풀을 차례로 닫음).

손으로 한 바퀴: 브리지(`mode:=NORMAL`) · `central` · `fake_fleet`을 켜고, `ws_watch --account <tester id>`를 켠 뒤 `order --account <tester id> --items <A의 product_id>:2`를 부릅니다. `ws_watch`에 `접수됨` → `준비 중` → `출고 중` → `출고 완료`가 보이면 됩니다. id는 `setup_db`가 출력합니다.

## 관제 fleet과 붙일 때

`fake_fleet`은 관제의 `fleet_manager_node`를 대신하므로 진짜 fleet과 같이 켜지 않습니다. `sim/fake_robots` 런치도 `fake_central:=false`로 띄웁니다(central은 `pc/central`의 것을 씀).

## 설계 · 계획

- 설계: [`docs/superpowers/specs/2026-11-25-bridge-slice1-design.md`](../../docs/superpowers/specs/2026-11-25-bridge-slice1-design.md)
- 계획: [`docs/superpowers/plans/2026-11-25-bridge-slice1.md`](../../docs/superpowers/plans/2026-11-25-bridge-slice1.md)
- 주문 DB: [`design/db/README.md`](../../design/db/README.md)
