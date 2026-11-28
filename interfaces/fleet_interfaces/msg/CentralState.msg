# ---- central 운영 단계 (/central/state) ----
string STATE_CONNECTING="CONNECTING"   # 브리지를 찾는 중
string STATE_SYNCING="SYNCING"         # 끝나지 않은 운반 건을 받아 주문판을 세우는 중
string STATE_RUNNING="RUNNING"         # 주문판 발행 · SubmitTrip · ReportOrder 처리 중
string boot_id                         # central이 켜질 때마다 새로 만드는 값 (uuid4 hex 32자)
string state
builtin_interfaces/Time stamp          # 발행 시각 (보내는 쪽, IR-42)
