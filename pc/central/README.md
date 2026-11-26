# central

주문 흐름 1단계의 간이 central. DB 없이 메모리에 주문판만 둔다. 다시 켜면 주문판 · 응답 기억이 사라진다(지원하지 않음, SB-06).

- 서비스: `submit_trip` (`SubmitTrip`, 같은 `trip_id`는 처음 응답 그대로), `report_order` (`ReportOrder`, 끝 상태면 주문판에서 뺌)
- 발행: `order_board` (`OrderBoard`, 바뀔 때마다 + 1초마다)
- 구독: `system_mode` (`SystemMode`, 바뀔 때 로그만)
- 이름 · QoS는 `fleet_interfaces/contract.yaml`의 키로 읽는다.
- 실행: `ros2 run central central`
- 설계: `docs/superpowers/specs/2026-11-25-bridge-slice1-design.md` 7절
