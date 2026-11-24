# 주문 DB (design/db)

주문 관리 웹 서비스가 쓰는 PostgreSQL DB의 원본과 시험입니다.
설계: [docs/superpowers/specs/2026-11-18-order-db-design.md](../../docs/superpowers/specs/2026-11-18-order-db-design.md)

## 파일

| 파일 | 내용 |
|---|---|
| `order_db.dbml` | **원본 (v1.1, 2026-11-20).** 테이블 15개, 노트에 기능 ID · 값 목록. ERD 그림과 DDL을 여기서 만든다 |
| `order_db.sql` | `dbml2sql`로 만든 DDL. **손으로 고치지 않는다** |
| `order_db.dbdiagram` | dbdiagram.io 표 배치(위치만). 같은 화면에서 DBML과 함께 불러와 ERD 배치를 되살린다. v1.1에서 더한 `ws_event` · `bridge_session`은 아직 위치가 없다 |
| `order_db_extra.sql` | DBML로 못 쓰는 것: CHECK · 부분 UNIQUE · 생성 칼럼 · 원장 · 마감 트리거 · `v_in_flight` · 재확인 함수 · NOTIFY · 처음 설정 값 |
| `compose.yaml` | 시험용 PostgreSQL 16 (포트 55432, 내리면 데이터 사라짐) |
| `tools/gen_sql.sh` | Docker(`node:20-alpine`)로 `@dbml/cli` 12.1.0의 `dbml2sql` 실행 |
| `tests/` | pytest 시험 |

DB에 적용하는 순서: `order_db.sql` → `order_db_extra.sql`.

## DDL 다시 만들기

`order_db.dbml`을 고친 뒤:

```bash
bash design/db/tools/gen_sql.sh
```

## 시험

```bash
docker compose -f design/db/compose.yaml up -d
```

```bash
uv run --with pytest --with 'psycopg[binary]' pytest design/db/tests -q
```

```bash
docker compose -f design/db/compose.yaml down
```

다른 PostgreSQL을 쓰려면 `IFP_PG_DSN`(기본 `postgresql://postgres:ifp_test@localhost:55432/postgres`)을 바꿉니다.

## ERD 그림 (제출용)

1. [dbdiagram.io](https://dbdiagram.io)에서 새 다이어그램을 열고 `order_db.dbml` 내용을 붙여 넣습니다.
2. 배치를 다듬고(저장한 배치는 `order_db.dbdiagram`) Export → PNG로 내보냅니다.
3. `design/exports/2팀_ERD_주문DB.png`로 저장하고 `design/README.md` 제출본 표에 한 줄 더합니다.

외부 사이트에 스키마를 올리는 일이므로 팀이 직접 합니다.

## DB가 지키는 규칙 (요약)

- 재고(`product.stock_qty`)는 `stock_ledger`에 기록을 넣을 때만 바뀝니다. 직접 UPDATE하면 거절합니다. 원장 기록은 고치거나 지울 수 없고 반대 기록으로 바로잡습니다.
- 마감 중에는 반출 · 수시 조정을 받지 않고, 입고는 실사를 연 뒤부터 받지 않습니다.
- 실사는 central 배정 보류 반영 + 진행 중 운반 건 0일 때만 열고, 확정은 모든 상품 실사 · 차이 사유가 있어야 됩니다.
- **마감 확정은 한 트랜잭션에서 `SELECT pg_advisory_xact_lock(7001)`을 먼저 잡고** `book_qty` 기록 → 차이만 `CLOSE_ADJUST` → `status = 'CONFIRMED'` 순으로 합니다. 잠금 없이 넣은 `CLOSE_ADJUST`는 거절되고, 확정 순간 재고가 센 수량과 다르면 확정이 거절됩니다. 끝난 마감은 `release_acked_at`만 적을 수 있습니다.
- `fn_recheck_rejects()`는 마감 뒤 재고를 다시 확인해 거절할 주문을 돌려줍니다 (취소 기록은 웹 서비스가).
- 주문 · 운반 건 · 알림이 생기거나 상태가 바뀌면 `ws_event`에 한 줄(사건 순번 `seq`) 넣고 같은 트랜잭션에서 `ifp_events` 채널로 알립니다. 웹 서비스가 `ws_event`에 직접 넣은 사건도 같이 알립니다. 마감은 순번 없이 알리기만 합니다.
- 같은 번호는 한 줄만: `orders.idem_key`, `alert.event_id`, `admin_action.request_id` (UNIQUE). 보고 반영 규칙(`(report_epoch, report_n)` 비교)은 웹 서비스 `apply_report`가 합니다 (설계 문서 5절).
- 주문 요약 상태 계산, 운반 건 나누기, 재고 부족 알림 열기 · 닫기는 웹 서비스가 합니다.
