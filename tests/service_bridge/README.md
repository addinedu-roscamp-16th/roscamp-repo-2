# 서비스 브리지 단위 테스트

서비스 브리지(화면 ↔ ROS 2)가 따라야 할 규칙을 ROS 없이 확인하는 단위 테스트입니다.

- 이 테스트는 팀 제품 코드를 검증하지 않습니다. `reference/`의 시험용 최소 구현으로 **규칙 자체**를 검증합니다.
- 규칙 중 팀 확인이 필요한 항목은 [코딩 규칙 검토 사항](코딩_규칙_검토_사항.md)에 모았습니다.

## 실행

저장소 폴더에서 실행합니다. ROS와 colcon이 없어도 됩니다.

```bash
pip install pytest
python -m pytest tests/service_bridge -q
```

`tests/` 아래에는 `package.xml`이 없으므로 `colcon test`에는 잡히지 않습니다.

## 구성

| 경로 | 내용 |
|---|---|
| `reference/sbt_bridge/core/` | 시험용 코어(표준 라이브러리만): `db`, `outbox`, `orders`, `alert_inbox`, `ws_queue` |
| `unit/test_outbox.py` | UT-01 주문 상태 전이, UT-02(미실행: Action 경로 없음) |
| `unit/test_orders.py` | UT-03 Idempotency-Key 멱등 접수, 커밋 실패 회귀 시험 2건 |
| `unit/test_alert_inbox.py` | UT-04 중복 알림 재ACK, UT-05 커밋 뒤 ACK, UT-05-대조 |
| `unit/test_ws_queue.py` | 보조 시험: WS 송신 큐, 최신 상태 유지, `since_seq` 재생, credit 흐름 제어 |
| `conftest.py` | import 경로, `plan_id` 마커, `record` fixture(메모리 기록만) |
| `evidence/` | 원본 공식 실행 결과 사본(통합·부하 포함). 설계 검토용 근거로만 씀 |

계획서 항목은 `@pytest.mark.plan_id("UT-01")`처럼 표시합니다.

## 출처

| 항목 | 내용 |
|---|---|
| 원본 저장소 | `service_bridge_test` (개인 저장소), 커밋 `b2953c6` (2026-11-09) |
| 옮긴 것 | `src/sbt_bridge/sbt_bridge/core/`, `tests/unit/` 중 4개 파일 |
| 옮기지 않은 것 | `test_qos_module.py`(QoS 원본은 `contract.yaml`), 결과 기록기, 통합·부하 테스트, 모의 객체, 시험용 인터페이스 |
| 바꾼 것 | `test_outbox.py`의 소스 경로(`PKG`) 한 줄. 결과 기록기를 `conftest.py`의 간단한 fixture로 대체. 팀 `ruff.toml`(줄 길이 100)에 맞춰 긴 줄 15곳을 줄바꿈(동작 변경 없음). 1차 검토 반영(검토 사항 3.5 P-01): `orders.py`의 커밋 실패 시 롤백 추가(**동작 변경**, 원본에는 미반영), 회귀 시험 2건 추가, UT-01 보조 시험 2건에 `plan_id` 표시 |
| 원본 결과 | 공식 실행 `20261109-211124`: 계획서 항목 6건 중 통과 5건, 미실행 1건(UT-02) |

## 나중에 할 일

실제 서비스 브리지 코드가 `pc/central`에 생기면 다음 순서로 옮깁니다.

1. 테스트의 import를 제품 코드로 바꿉니다.
2. 테스트를 `pc/central/test/`로 옮깁니다(팀 관례: 패키지별 `test/`).
3. `reference/`를 지웁니다.
