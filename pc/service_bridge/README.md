# service_bridge

주문 흐름의 서비스 브리지(1차 조각). fleet의 `Hello`와 `ReportTrip` 서버(보고를 DB 주문 상태에 반영)이고 `/system/mode`를 발행합니다. central에 운반 건을 넘기는 `SubmitTrip`은 이번 조각에서는 시험 명령 `submit_trip`이 하며, 브리지가 직접 부르는 길은 다음 조각입니다.

- `service_bridge/core/`: ROS 없는 판단 코드(DB 트랜잭션, 상태 전이, 재시도). `pytest`만으로 시험합니다.
- `service_bridge/ros/`: rclpy 노드와 어댑터.
- `service_bridge/main.py`: `bridge` 진입점.
- `service_bridge/tools/`: 시험 도구(`setup_db`, `submit_trip`, `fake_fleet`).

central 쪽 `SubmitTrip` 처리는 `pc/central`에 있습니다.

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

## 화면 (고객 · 관리자 주문 화면)

- 파일 위치: `pc/service_bridge/service_bridge/web/static/` (`common/` 공용 모듈 · `customer/` 고객 주문 · `admin/` 관리자 상태 · `test/` 시험 페이지 · `img/` 상품 그림). 설계는 [`docs/superpowers/specs/2026-11-26-order-screens-design.md`](../../docs/superpowers/specs/2026-11-26-order-screens-design.md)입니다.
- 가짜 데이터로 띄우기 (서버 없이, `static/` 폴더를 그대로 웹 뿌리로 씀):

```bash
python3 -m http.server 8765 -d pc/service_bridge/service_bridge/web/static
```

| 주소 | 내용 |
|---|---|
| `http://localhost:8765/customer/?demo=normal` | 고객 주문 화면 (가짜 데이터) |
| `http://localhost:8765/admin/?demo=normal` | 관리자 상태 화면 (가짜 데이터) |
| `http://localhost:8765/test/` | 순수 함수 시험 페이지 (`결과: 통과 N · 실패 0`) |

- 시험 페이지를 headless chromium으로 한 번에 돌리기: `bash pc/service_bridge/service_bridge/web/static/test/run_headless.sh` (실패 0이면 종료 코드 0).
  - 위 명령은 모두 저장소 루트에서 실행합니다.
  - `run_headless.sh`는 `/snap/bin/chromium`이 있어야 돕니다.
- 장면 이름 (`?demo=<이름>`): `normal` · `stock` · `busy` · `maintenance` · `reconnect` · `race` · `many`.
  - 고객 화면은 일곱 장면을 모두 씁니다.
  - 관리자 화면은 `normal` · `maintenance` · `reconnect` · `many`만 씁니다. 나머지는 `normal`로 떨어집니다.
- 실제 서버 주소: `http://localhost:8000/customer/?account=<account_id 숫자>` (관리자는 `/admin/`). 웹 뿌리 마운트(백엔드 N-07)가 들어간 뒤에 열립니다. `/`는 고객 화면으로 넘어갑니다.
