# central

주문 처리(처리 순서 · 진행 상황 보고). 관제 PC에서 돈다. DB는 없고, 주문 · 재고 · 화면은 주문 관리 웹 서비스가 맡는다 (A안, `docs/superpowers/specs/2026-11-18-order-db-design.md`).

- 발행: `/central/orders` (`OrderBoard`), `/central/events` (`FleetEvent`, FMS도 발행)
- 서비스: `/central/submit_order` (`SubmitOrder`), `/central/set_run_mode` (`SetRunMode`), `/central/report_order` (`ReportOrder`)
- 호출: `/fleet/confirm_clear` (`ConfirmClear`)
