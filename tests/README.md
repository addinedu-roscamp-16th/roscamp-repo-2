# tests

여러 패키지에 걸치거나 아직 제품 패키지가 없는 시험을 둡니다. 패키지 하나만 시험하는 것은 그 패키지의 `test/`에 둡니다(예: `pc/fleet_core/test`).

| 폴더 | 내용 | 실행 |
|---|---|---|
| `service_bridge/` | 서비스 브리지 규칙 단위 테스트 (ROS 없음) | `python -m pytest tests/service_bridge -q` |

`tests/` 아래에는 `package.xml`이 없으므로 colcon 빌드와 `colcon test`에 잡히지 않습니다.
