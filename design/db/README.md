# 주문 DB (design/db)

주문 관리 웹 서비스가 쓰는 PostgreSQL DB의 원본과 시험입니다.
설계: [docs/superpowers/specs/2026-11-18-order-db-design.md](../../docs/superpowers/specs/2026-11-18-order-db-design.md)

## 파일

| 파일 | 내용 |
|---|---|
| `order_db.dbml` | **원본.** 테이블 13개, 노트에 기능 ID · 값 목록. ERD 그림과 DDL을 여기서 만든다 |
| `order_db.sql` | `dbml2sql`로 만든 DDL. **손으로 고치지 않는다** |
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
2. 배치를 다듬고 Export → PNG로 내보냅니다.
3. `design/exports/2팀_ERD_주문DB.png`로 저장하고 `design/README.md` 제출본 표에 한 줄 더합니다.

외부 사이트에 스키마를 올리는 일이므로 팀이 직접 합니다.

## DB가 지키는 규칙 (요약)

- 재고(`product.stock_qty`)는 `stock_ledger`에 기록을 넣을 때만 바뀝니다. 직접 UPDATE하면 거절합니다. 원장 기록은 고치거나 지울 수 없고 반대 기록으로 바로잡습니다.
- 마감 중에는 반출 · 수시 조정을 받지 않고, 입고는 실사를 연 뒤부터 받지 않습니다.
- 실사는 central 배정 보류 반영 + 진행 중 운반 건 0일 때만 열고, 확정은 모든 상품 실사 · 차이 사유가 있어야 됩니다.
- `fn_recheck_rejects()`는 마감 뒤 재고를 다시 확인해 거절할 주문을 돌려줍니다 (취소 기록은 웹 서비스가).
- 주문 · 운반 건 · 알림 · 마감이 바뀌면 `ifp_events` 채널로 알립니다.
- 주문 요약 상태 계산, 운반 건 나누기, 재고 부족 알림 열기 · 닫기는 웹 서비스가 합니다.
