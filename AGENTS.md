# AI 작업 안내

이 파일은 안내판입니다. 규칙 내용은 여기에 쓰지 않고, 작업에 맞는 문서를 **그 작업을 할 때만** 열어 읽고 따릅니다. (같은 내용이 `CLAUDE.md`에도 있습니다. 하나를 고치면 다른 하나도 고칩니다.)

| 작업 | 먼저 읽을 문서 |
|---|---|
| ROS 2 통신 코드 (토픽 · 서비스 · 액션, QoS, 재시도, `fleet_interfaces`) | `docs/설계/통신_규칙.md` (규칙 `IR-nn`을 따름) |
| 인터페이스 구현 · 변경 (이름 · 타입 · 칸) | `docs/설계/인터페이스_명세.md`와 `interfaces/fleet_interfaces/contract.yaml` (contract가 원본, 기준 버전은 명세 머리 참고) |
| 관제(fleet) · central · `fleet_interfaces`를 명세에 맞추는 작업 | `docs/설계/fleet_작업_목록.md` (`W-nn`) |
| 미확정 항목에 걸림 | `docs/설계/설계_검토_사항.md` (`Q-nn`. "열림"이면 짐작해 구현하지 말고 사용자에게 물음) |
| 서비스 브리지 · central 호출 · `ReportTrip` 처리 · 재시도 · 재시작 (주문 흐름 구현과 그 논의) | `docs/설계/서비스_브리지_기술_조사.md` 7장 (`SB-nn`. "제안"이면 그 안으로 논의하고, 코드에 넣기 전에 사용자에게 확정을 물음) |
| 기능 번호(`SCR` · `OMS` · `BRG` · `CEN` · `FLT` · `PDE` · `PHD` · `PHN` · `COM`-nn)의 뜻 · 범위 확인 | `docs/기능_목록/기능_목록.csv` (원본은 Google 시트, 이 파일은 사본. 고칠 일이 있으면 사용자에게 시트 수정을 요청) |
| 브랜치 · PR · 병합 · 버전 · 태그 | `docs/브랜치_운영_안내.md` (기준 브랜치 `dev`, PR로만 병합) |
| 실물 망 통신 설정 · 디스커버리 서버 · 도메인 | `docs/설계/디스커버리_서버_안내.md` (설정은 `tools/net/net_ds.sh` 한 곳) |
| 빌드 · 실행 · ROS 도메인 | `docs/개발_규칙.md` |
| 화면 · 다이어그램 · 스토리보드 | `design/README.md` |
| 주문 DB | `docs/superpowers/specs/2026-11-18-order-db-design.md`, `design/db/README.md` |

공통:
- 인터페이스를 바꾸면 `contract.yaml` · 명세 · (필요하면) 통신 규칙을 같은 커밋에서 고칩니다.
- 문서와 답변은 한국어로 씁니다.
