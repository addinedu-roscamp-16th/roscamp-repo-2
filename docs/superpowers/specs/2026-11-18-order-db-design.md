# 주문 관리 DB 설계 (ERD)

- 작성일: 2026-11-18 · 고침: 2026-11-19 (기능 목록 11-19 변경 · fleet-v1 검토 · 마감 토의 결정 · 주문 나눔 · 시험 모드 반영) · 2026-11-25 (SB-04 확정: `trip_dispatch` 테이블 추가 4.16, V2 접수 부분 확정: 주문 접수도 7001 공유, `basket_capacity` 원본은 DB · 관제 전달은 `Hello` + 서비스 4.13) · 2026-11-24 (SB-01 · SB-03 · SB-05 확정: 같거나 작은 번호는 `DUPLICATE`, 허용 전이 표, 잠금 순서 7001 → orders → trip → 원장 행, 잠금 7001 설명) · 2026-11-20 (DB v1.1: 통신 규칙 검토 3장 #18 · #20 · #21 · #22 · #25 · #34 · #37 반영 — 보고 번호 · 중복 키 칸, `ws_event` · `bridge_session`, 사유 코드 contract 이름, 보고 반영 규칙)
- 상태: 승인 (2026-11-19) · 구현 계획 `docs/superpowers/plans/2026-11-19-order-db.md` · 구현 `design/db/`
- 산출물: `design/db/order_db.dbml` (원본), 여기서 만드는 ERD PNG(제출용)와 PostgreSQL DDL
- 관련 문서: `docs/설계/통신_규칙.md` · `docs/설계/인터페이스_명세.md`(11-20. 결정 표 원문은 커밋 `869c359`의 `docs/통신_규칙_인터페이스_검토.md` 3장), `docs/fleet-v1_HELD_검토.md`(5장 논의 결과), `2026-11-19-close-db-discussion.md`(마감 토의 기록), `design/다이어그램_수정_요청.md`(R-15 ~ R-20)

## 1. 목적

주문 관리 웹 서비스(Docker 서버, SW 아키텍처의 "Central Order/Management Service")가 쓰는 **Order/Operations DB**의 구조를 정한다. 두 가지에 쓴다.

- 구현 기준: 웹 서비스가 이 DDL로 DB를 만든다.
- 제출: 같은 원본에서 ERD 그림을 내보내 제출한다.

프로젝트는 온라인 쇼핑몰의 상품 준비와 로봇 배송을 체험하는 사무실이다.

### 목표
- 기능 목록(기능 검토 시트 "기능 목록" 탭, 2026-11-19)에서 주문 관리(OMS-01 ~ 18) 중 구현 계획이 포함 · 일부 포함 · 새로 추가된 기능, 그리고 관리자 화면(SCR-01 · 07 · 08 · 10 · 11 · 12 · 16 · 18 · 19 · 20 · 21)이 읽고 쓰는 데이터가 모두 어느 테이블엔가 자리가 있다.
- 주문 상태 값과 전이 시점이 상태 · 시퀀스 다이어그램 v2.4(와 수정 요청 R-15 ~ R-18)와 같다.
- 그림과 DDL이 한 원본(DBML)에서 나와 서로 어긋나지 않는다.

### 하지 않는 것
- FMS Log의 State/Event/Robot Data DB. **InfluxDB(시계열 DB)로 따로 설계**한다. 로봇 상태 · 위치 · 관제 이벤트 이력은 거기서 조회한다.
- 서비스 브리지의 자체 저장소(outbox · 중복 요청 키 · 알림 수신함, `service_bridge_test`의 SQLite)
- 구현 계획 미포함 기능: 재고 예약(OMS-07), 취소 처리 중 · 취소 상품 처리 중(OMS-11 일부), 통계 집계(OMS-17 일부), 빈 바구니 부족(SCR-14), 새 Pode 대체 배정
- **창고와 작업대 선반 사이를 오가는 수량.** 재고는 창고 기준 한 숫자로만 관리한다 (2절).
- 시험 모드의 강제 입력(조건 · 상태 바꾸기) 명령과 로봇 모드 알림 · 모드 맞추기 인터페이스 (인터페이스 작업, 6절). DB는 모드마다 따로 두는 것만 정한다.
- 웹 서비스 코드, API 설계

### 실패로 보는 경우
- 다이어그램 v2.4의 DB 기록 시점(6-1-1 #2 · #14, 6-1-3 #33, 6-2 #4 · #11, 6-4 #8, 6-5 #10, 6-6 #10, 6-7 #5)에 쓸 자리가 없다.
- 관리자 입력 버튼(스토리보드)이 남긴 기록을 이력 화면에서 찾을 수 없다.
- 결품 사전 확인이 앞선 운반 건이 이미 담아 간 수량을 모른다.
- 재고가 기록(입고 · 반출 · 출고 차감 · 마감 조정 · 수시 조정) 없이 바뀐다.
- 마감 실사 중에 재고가 바뀌거나, 주문 하나가 마감 앞뒤로 갈라진다.

## 2. 정한 내용 (사용자 결정)

11-18:

- **DB 주인은 주문 관리 웹 서비스 (A안).** central(ROS 주문 처리)은 DB 없이 처리 순서를 관리하고 진행 상황을 보고한다. 결품 사전 확인처럼 central이 DB 값을 알아야 하면 서비스 브리지를 거쳐 웹 서비스에 묻는다 (기능 목록 BRG-01 노트와 같은 길). SW 아키텍처 · D-04 3차 판단과 맞다.
- **범위는 주문 관리 DB 하나.** FMS Log는 InfluxDB.
- **DBMS는 PostgreSQL.** 이유: `LISTEN/NOTIFY`로 상태 변경을 웹 서비스의 WS 전달(OMS-05)에 바로 잇고, 부분 인덱스 · JSONB · CHECK로 규칙을 DB에 걸 수 있다.
- **원본은 DBML.** `design/db/order_db.dbml` 하나에서 ERD(dbdiagram.io → PNG)와 DDL(`dbml2sql --postgres`)을 만든다.
- **이번 작업 범위는 DB만.** DBML · DDL(+ `order_db_extra.sql`) · ERD PNG와 design/README · CHANGELOG 기록, 그리고 `pc/central/README.md`의 문구 수정. 인터페이스 변경과 가짜 central 노드 수정은 관제 담당과 **별도 작업**으로 한다 (6절 목록을 넘겨줌).

11-19:

- **다이어그램 v2.4와 기능 목록이 `fleet-v1` 브랜치보다 우선한다.** 팀 논의에서 fleet-v1을 제안된 설계 방법(v2.4)에 맞추기로 했다 (`docs/fleet-v1_HELD_검토.md` 5장). 주문 처리 보류(HELD)는 Pode 장애(운반 중) · Phody 장애에만 쓴다.
- **마감 뒤 재고를 다시 확인해 거절한 대기 주문은 취소됨(CANCELLED), 사유 `STOCK_RECHECK`.** 상태 값을 새로 만들지 않는다.
- **마감의 기록과 현재 상태는 DB(`stock_close`)에 둔다.** central에는 브리지로 알린다.
- **바구니 용량을 넘는 주문은 나누어 처리한다.** 웹 서비스가 접수 때 주문을 **운반 건**(바구니 하나 분량)으로 나누고, 운반 건마다 Pode가 한 대씩 맡는다. 로봇 진행 상태 · 담은 수량 · 출고 차감은 운반 건에, 사용자가 주문한 내용은 주문에 둔다 (D-5 가). 다이어그램 수정 요청 R-15.
- **설정 두 가지(주문 한 건의 최대 상품 개수, 바구니 하나의 최대 상품 개수)는 DB에 둔다** (D-6). 관리자가 바꿀 수 있다.
- **재고의 기준은 창고다.** `product.stock_qty`는 창고(작업대 선반에 옮겨 둔 것 포함) 전체의 수량 하나다. 창고와 작업대 선반 사이를 오가는 수량은 관리하지 않는다. 결품은 두 가지다 (R-18).
  - **선반만 빔** (DB 재고는 있음, 담는 도중 인식 오류): 관리자가 창고에서 작업대에 갖다 놓는다. 입고가 아니므로 재고 기록을 남기지 않는다.
  - **DB 재고 부족** (작업대 도착 때 결품 사전 확인): 창고에도 없다는 뜻이다. 바깥에서 들여와 **추가 입고**를 등록하고 재개하거나, 보충 불가로 처리한다.
- **Phody 팔 고장은 복구 명령으로 처리한다.** Pode를 운행에서 빼지 않는다.
- 마감 토의(`2026-11-19-close-db-discussion.md`)의 합의 12건과 결정:
  - D-1 가: 입력 오류는 **원래 종류의 반대 기록**(예: 반출 −100, 사유 입력 오류, 원래 기록 #N)으로 바로잡는다. 따로 "바로잡음" 종류를 두지 않는다.
  - D-2 가: 마감 뒤 재확인은 **접수 순으로 수량을 쌓아** 비교한다. 넘는 주문은 건너뛰고 뒤의 작은 주문은 계속 본다.
  - D-3 가: 상품을 담은 채 운반 중 Pode가 고장 나면 **보류(Pode 장애)**. 보류를 끝내는 길에 **"선반에 되돌림"(보류 → 접수됨)**을 더한다 (R-16).
  - **마감 중 입고는 실사를 열기 전까지 받는다.** 반출 · 수시 조정은 마감 시작부터 막는다. 마감을 기다리는 동안 남은 운반 건에 DB 재고 부족 결품이 나면 추가 입고로 채울 수 있게 하려는 것이다. 장부 수량은 확정 때 기록하므로 실사 전 입고는 데이터를 틀어지게 하지 않는다 (합의 3을 고침).
  - D-4 가: **결품 중 수시 조정은 막는다.** 담기 일시 중지 때 담은 수를 알려 주는 인터페이스가 생기면 연다.
  - **이미 시작된 나뉜 주문은 마감 중에도 남은 운반 건까지 끝낸다.** 실사는 그 뒤에 연다. 마감 뒤 재확인은 아직 시작하지 않은 주문만, 주문 단위로 한다 (R-17).
- **서비스 전체를 시험 모드와 정상 운영으로 나눈다** (SCR-17 지원, 5절 "시험 모드"). 실제 장비의 장애는 언제 날지 모르므로, 기능 개발을 위해 상태를 일부러 만들어야 한다.
  - 모드는 서비스를 켤 때 정하고 도중에 바꾸지 않는다. **모드마다 DB를 따로** 둔다 (같은 구조, 다른 이름). 그래서 테이블에 시험 표시 칸이 없다.
  - 시험 모드의 주문은 **정해 둔 시나리오**와 **개발자 직접 주문** 두 가지로 만든다. 들어간 뒤로는 실제 주문과 같은 길로 간다.
  - **진입 · 해제 때 마감을 강제한다.** 진입: 정상 운영에서 마감을 확정해야 시험 모드로 바꿀 수 있고, 그 실사 수량으로 시험 DB 재고를 채운다. 해제: 정상 운영은 마감 중 상태로 켜져 실사를 확정해야 배정을 시작한다.
  - 시험 때문에 생긴 차이의 사유는 **"시험 영향"**(`TEST_RUN`).
  - 시험 모드 동안 사용자 화면은 **"점검 중"**으로 안내하고 주문을 받지 않는다.

## 3. 방식 (Approaches)

### DB 원본

| 방식 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **1. DBML 원본 (선택)** | DBML → ERD PNG · PostgreSQL DDL | 그림과 DDL이 한 원본, git diff, 기능 ID 노트 | DBML 문법 학습, 그림은 외부 사이트(dbdiagram.io)에서 |
| 2. DDL 원본 + draw.io ERD | SQL과 그림을 따로 손으로 | 기존 design/ 관리 방식 그대로 | 고칠 때마다 두 곳, 구현 중 그림이 낡음 |
| 3. DDL 원본 + 자동 생성 ERD | SchemaSpy · DBeaver | 실제 DB와 항상 같음 | 배치 · 노트를 다듬기 어려워 제출용으로 부족 |

선택 이유: ERD가 제출물이면서 구현 기준이라 둘이 어긋나지 않는 것이 가장 중요하다. Mermaid는 제약 · 자료형을 다 담지 못해 뺐다.

### 나눈 주문 (D-5)

| 방식 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **가. 운반 건 테이블 (선택)** | 주문은 사용자가 주문한 그대로, 운반 건이 Pode · 상태 · 담은 수량을 가짐. 주문 상태는 운반 건들의 요약 | 사용자가 보는 것과 로봇이 다루는 것이 갈림. 취소 · 이력의 뜻이 분명 | 주문 상태 요약 규칙이 필요 |
| 나. 자식 주문 | 주문을 여러 주문으로 쪼개고 부모로 묶음 | 지금 설계를 거의 그대로 씀 | 사용자 취소 · 화면은 부모, 이력 · 재고는 자식이라 "주문"의 뜻이 섞임 |

## 4. 테이블

`※` 표시는 설계자 판단(7절)이다. 자료형은 PostgreSQL 기준. 테이블은 16개다 (11-20에 `ws_event` · `bridge_session` 추가, 11-25에 `trip_dispatch` 추가. `trip_dispatch`의 DBML · DDL은 브리지 2단계에서 넣는다).

### 4.1 account — 계정 · 역할 (OMS-01, SCR-01)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| account_id | bigint identity | PK |
| login_id | text | UNIQUE, NOT NULL |
| password_hash | text | NOT NULL (평문 저장 금지) |
| role | text | NOT NULL, CHECK IN ('USER', 'ADMIN', 'DEV') — `DEV`는 개발자 (SCR-17). 정상 운영에서도 로그인할 수 있지만, 정상 운영에서는 관리자 화면과 시험 모드 진입(8번 탭 [시험 모드로])만 쓴다. 시험 메뉴 · 강제 입력 API는 시험 모드에만 있다 |
| name | text | 화면 표시 이름 |
| created_at | timestamptz | NOT NULL DEFAULT now() |

### 4.2 product — 상품 · 현재 재고 (OMS-02 · 03 · 06 · 08 · 09, SCR-02 · 18)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| product_id | bigint identity | PK |
| kind | text | UNIQUE, NOT NULL. ROS 인터페이스 `OrderItem.kind`와 비전이 쓰는 상품 코드 |
| name | text | NOT NULL, 화면 표시 이름 (검색 대상) |
| stock_qty | integer | NOT NULL, CHECK >= 0. 지금 재고. **창고 기준** (작업대 선반에 옮겨 둔 것 포함, 위치를 나누지 않음). **`stock_ledger`를 거쳐서만 바뀐다** (5절) |
| low_stock_threshold | integer | NOT NULL DEFAULT 0, CHECK >= 0. 재고 부족 알림 기준 (OMS-09). 화면의 "일괄 적용"은 모든 행을 같은 값으로 UPDATE ※ |
| updated_at | timestamptz | NOT NULL |

### 4.3 stock_ledger — 재고 변동 기록 (OMS-06 · 08 · 15 · 18, SCR-18 · 19)

재고가 바뀔 때마다 한 줄. 재고를 직접 고치는 화면은 없다 (SCR-18, OMS-08). 잘못 넣은 기록은 지우거나 고치지 않고 **원래 종류의 반대 기록**으로 바로잡는다 (D-1 가, 스토리보드 장면 4). 창고 → 작업대 선반 보충은 기록하지 않는다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| ledger_id | bigint identity | PK |
| product_id | bigint | FK → product, NOT NULL |
| entry_type | text | NOT NULL, CHECK IN ('INBOUND' 입고, 'OUTBOUND' 반출, 'SHIP' 출고 차감, 'CLOSE_ADJUST' 마감 조정, 'SPOT_ADJUST' 수시 조정) |
| change_qty | integer | NOT NULL, ≠ 0. 늘면 +, 줄면 − |
| qty_after | integer | NOT NULL, CHECK >= 0. 트리거가 채운다 |
| reason | text | 입고 · 반출 사유. 입고: 'REGULAR' 정기 입고, 'ADDITIONAL' 추가 입고 (비정기 입고), 'INPUT_ERROR' 입력 오류, 'OTHER' 기타. 반출: 'DAMAGED' 파손, 'EXPIRED' 유통기한, 'INPUT_ERROR', 'OTHER' |
| corrects_ledger_id | bigint | FK → stock_ledger, UNIQUE, NULL 허용. 사유가 `INPUT_ERROR`일 때만, 그리고 반드시 채움 (바로잡는 원래 기록) |
| trip_id | text | FK → trip, NULL 허용 (`SHIP`일 때 반드시) |
| count_id | bigint | FK → stock_count, NULL 허용 (`CLOSE_ADJUST` · `SPOT_ADJUST`일 때 반드시) |
| account_id | bigint | FK → account, NULL 허용 (관리자가 넣은 기록, 늦은 출고 보고를 관리자가 확인한 출고 차감) |
| note | text | 기타 사유 설명 등. 사유 `OTHER`면 필수 |
| created_at | timestamptz | NOT NULL DEFAULT now() |

CHECK:

```sql
CHECK (entry_type <> 'INBOUND'  OR (change_qty > 0 AND reason IN ('REGULAR','ADDITIONAL','INPUT_ERROR','OTHER'))),
CHECK (entry_type <> 'OUTBOUND' OR (change_qty < 0 AND reason IN ('DAMAGED','EXPIRED','INPUT_ERROR','OTHER'))),
CHECK (entry_type IN ('INBOUND','OUTBOUND') OR reason IS NULL),
CHECK ((entry_type = 'SHIP') = (trip_id IS NOT NULL) AND (entry_type <> 'SHIP' OR change_qty < 0)),
CHECK ((entry_type IN ('CLOSE_ADJUST','SPOT_ADJUST')) = (count_id IS NOT NULL)),
CHECK ((reason IS NOT DISTINCT FROM 'INPUT_ERROR') = (corrects_ledger_id IS NOT NULL)),
CHECK (reason IS DISTINCT FROM 'OTHER' OR note IS NOT NULL)
```

입력 오류 예 (사이다 입고를 10개 대신 100개로 넣음): "반출 −100, 사유 입력 오류, 원래 기록 #N" 후 "입고 +10, 정기 입고". 반출 합계를 낼 때는 사유 입력 오류를 뺀다.

### 4.4 orders — 주문 (OMS-04 · 05 · 10, SCR-04 · 07)

사용자가 주문한 단위. 로봇 · 상태 진행은 운반 건(4.6)에 있고, `status`는 운반 건들의 **요약**이다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| order_id | text | PK. 웹 서비스가 만든다 (예: `O-20261118-0001`) ※ |
| account_id | bigint | FK → account, NOT NULL (주문한 사용자) |
| status | text | NOT NULL, CHECK IN (아래 9개). 운반 건 상태가 바뀌는 트랜잭션에서 요약 규칙으로 다시 계산해 저장 ※ |
| created_at | timestamptz | NOT NULL DEFAULT now() (접수 시각, 마감 뒤 재확인 순서) |
| updated_at | timestamptz | NOT NULL |
| finished_at | timestamptz | 출고됨 · 일부 출고됨 · 취소됨이 된 시각 |
| idem_key | text | UNIQUE, NULL 허용. 화면 주문 요청의 `Idempotency-Key`. 같은 키로 다시 온 요청은 새로 접수하지 않고 처음 결과를 돌려준다 (통신 규칙 #34 · #37) |

상태 값 (다이어그램 v2.4 '0. 상태표' · '2. 주문', CEN-03). 주문과 운반 건이 같은 값을 쓴다.

| 값 | 한국어 | 비고 |
|---|---|---|
| RECEIVED | 접수됨 | 사용자 취소는 모든 운반 건이 여기 있을 때만 (OMS-10). 마감 중에는 배정되지 않고 머문다 (CEN-13). 시작된 나뉜 주문의 남은 운반 건은 예외 |
| ASSIGNED | 배정됨 | Pode 이동 · 도킹 · 담기 대기까지 모두. fleet-v1에서는 알 수 없어 건너뛸 수 있다 (4.7) |
| FILLING | 담는 중 | |
| FILLING_PAUSE | 담기 일시 중지 | 결품 · 집기 실패 · 인식 오류 |
| TRANSPORTING | 운반 중 | |
| SHIPPED | 출고됨 | 끝 |
| PARTIAL | 일부 출고됨 | 끝. 못 담은 상품 있음 |
| HELD | 주문 처리 보류 | Pode 장애(운반 중) · Phody 장애 · 관제 재시작(`FLEET_RESTART`, 통신 규칙 #37). 사유는 이력에 |
| CANCELLED | 취소됨 | 끝. 사용자 취소, 담을 상품 없음(`NOTHING_TO_FILL`, 0.6 `CANCELED`에 해당, 통신 규칙 #20), 마감 뒤 재고 다시 확인 거절. 철자는 `CANCELLED` (통신 규칙 #18) |

**주문 상태 요약 규칙** ※ (사용자 화면이 보는 값). 나뉜 주문도 고객에게는 **주문 하나로 보인다** (11-19 사용자 결정, 사용자 스토리보드 설계 8절):

1. 운반 건이 모두 끝났으면(출고됨 · 일부 출고됨 · 취소됨): 모두 취소됨 → 취소됨, 모두 출고됨 → 출고됨, 그 밖 → 일부 출고됨.
2. 모든 운반 건이 접수됨이면 → 접수됨. 사용자 취소는 이때만 된다.
3. 끝나지 않은 운반 건 중 보류가 하나라도 있으면 → 보류.
4. 그 밖에는 끝나지 않은 운반 건 중 가장 늦은 상태 (배정됨 < 담는 중 · 담기 일시 중지 < 운반 중). 하나라도 시작한 주문에 남은 **접수됨 운반 건은 배정됨으로 센다.** 같은 단계에 담는 중과 담기 일시 중지가 섞이면 담기 일시 중지.

그래서 고객 화면(사용자 스토리보드 상태 대응표)에서 나뉜 주문은 첫 운반 건이 시작되면 끝날 때까지 "준비 중"이고, 남은 운반 건이 모두 운반 중일 때만 "출고 중"이다. 일부 바구니가 먼저 출고되어도 "접수됨"으로 되돌아가 보이지 않는다. 몇 번째 바구니까지 출고됐는지(예: "1/2 출고")는 주문 상세에서 운반 건으로 보인다.

나뉘지 않은 주문(운반 건 하나)은 운반 건 상태가 그대로 주문 상태다.

관제 내부 값(`stage` FILLING · SWAPPING, `basket` EMPTY · FULL · NONE, `Assignment.task_type`)은 관제 안에서만 쓰고 DB에 넣지 않는다.

### 4.5 order_item — 주문 상품 (OMS-04 · 12)

사용자가 주문한 상품과 수량. 담은 수량은 운반 건 상품(4.6)에 있다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| order_id | text | FK → orders, PK 일부 |
| product_id | bigint | FK → product, PK 일부 |
| qty_ordered | integer | NOT NULL, CHECK > 0 |

- 주문 한 건의 `qty_ordered` 합 ≤ 설정 `max_order_items` (4.13). 여러 행에 걸친 규칙이라 웹 서비스가 주문 등록 때 확인한다 ※. 화면에서도 먼저 막는다.
- 상품마다 `qty_ordered` = 그 주문 운반 건들의 `trip_item.qty_ordered` 합. 접수 트랜잭션에서 함께 만든다.

### 4.6 trip · trip_item — 운반 건 · 운반 건 상품 (CEN-06 · 08, OMS-06 · 12, R-15)

바구니 하나 분량. 웹 서비스가 접수 때 주문을 `basket_capacity`(4.13) 이하씩 나눠 만든다. central · 관제에 넘기는 주문 번호는 **`trip_id`**다.

**trip**

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| trip_id | text | PK. `order_id` + 순번 (예: `O-20261118-0001-1`) ※ |
| order_id | text | FK → orders, NOT NULL |
| seq | integer | NOT NULL, CHECK > 0. UNIQUE `(order_id, seq)`. 배정 순서 |
| status | text | NOT NULL, CHECK IN (4.4의 9개) |
| robot_id | text | NULL 허용. 맡은 Pode (`pode1` ~ `pode3`, fleet-v1 contract 0.5 기준) ※ |
| placed_at | timestamptz | 관제가 담은 수량(`placed`)을 알려 온 시각. NULL이면 바구니에 몇 개 담겼는지 모름 (결품 사전 확인 · 수시 조정 막기에 씀) |
| report_epoch | integer | NULL 허용. 마지막으로 반영한 `ReportTrip`의 `report_epoch` (`bridge_session.epoch`). 반영 전 NULL (통신 규칙 #18 · #37) |
| report_n | integer | NULL 허용. 마지막으로 반영한 `ReportTrip`의 `report_n`. CHECK `(report_epoch IS NULL) = (report_n IS NULL)`. `seq`(주문 안 순번)와는 별개 |
| first_assigned_at | timestamptz | 처음 배정됨이 된 시각. **기록용**: 접수됨으로 돌아온 운반 건의 고객 취소 판단에 쓰지 않는다 (통신 규칙 #21) |
| created_at | timestamptz | NOT NULL DEFAULT now(). **처음 접수 시각 — 통신 규칙 #21 · #37의 `trip.submitted_at`이 이 칸이다** ※. 다시 배정 순서와 `ListOpenTrips` 답의 정렬 기준. 접수됨으로 되돌려도 새로 찍지 않는다 (마지막 변경 시각 `updated_at` 순 금지) |
| updated_at | timestamptz | NOT NULL |
| finished_at | timestamptz | 출고됨 · 일부 출고됨 · 취소됨이 된 시각 |

**trip_item**

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| trip_id | text | FK → trip, PK 일부 |
| product_id | bigint | FK → product, PK 일부 |
| qty_ordered | integer | NOT NULL, CHECK > 0. 이 바구니에 담을 수 |
| qty_placed | integer | NOT NULL DEFAULT 0. 바구니에 담은 수 (`ReportOrder.placed`) |
| qty_missing | integer | NOT NULL DEFAULT 0. 못 담음 처리한 수 (`missing`) |

- CHECK `qty_placed + qty_missing <= qty_ordered`
- 운반 건 하나의 `qty_ordered` 합 ≤ 나눌 때의 `basket_capacity`. 나눈 뒤 설정이 바뀌어도 이미 나뉜 운반 건은 그대로 둔다.
- 나누는 방법(상품을 어떤 순서로 바구니에 채울지)은 만드는 사람에게 맡긴다 (9절).

### 4.7 status_history — 상태 이력 (OMS-05 · 11 · 12 · 14 · 15, SCR-19)

운반 건 상태가 바뀔 때 한 줄, 그 결과 주문 요약 상태가 바뀌면 한 줄 더. 상태를 바꾸는 트랜잭션 안에서 함께 쓴다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| history_id | bigint identity | PK |
| order_id | text | FK → orders, NOT NULL |
| trip_id | text | FK → trip, NULL 허용. NULL이면 주문 요약 상태의 변화 |
| from_status | text | NULL 허용 (처음 접수) |
| to_status | text | NOT NULL |
| reason | text | NULL 허용. contract 사유 코드 표의 이름 (통신 규칙 #37). CHECK IN ('OUT_OF_STOCK' 결품, 'PICK_FAILED' 집기 실패, 'RECOGNITION_ERROR' 인식 오류, 'ROBOT_FAULT' Pode 장애, 'ARM_FAULT' Phody 장애, 'FLEET_RESTART' 관제 재시작 보류, 'USER_CANCEL', 'NOTHING_TO_FILL' D-14, 'STOCK_RECHECK' 마감 뒤 재고 다시 확인 거절, 'FORCED_SHIP' 강제 출고 처리, 'FORCED_PARTIAL' 일부 출고 강제 처리, 'REASSIGN' 배정됨 · 담는 중 → 접수됨 되돌림(CEN-08, 빈 바구니 Pode 장애, 담은 것 0개인 Pode · Phody 장애 — 통신 규칙 #21), 'RETURNED_TO_SHELF' 보류 → 접수됨 선반에 되돌림(D-3, R-16)) ※ |
| robot_id | text | 관련 로봇 |
| actor | text | NOT NULL, CHECK IN ('USER', 'ADMIN', 'SYSTEM') |
| account_id | bigint | FK → account, NULL 허용 (USER · ADMIN일 때) |
| detail | jsonb | 결품 상품 · 수량, 관제 보고 원문(status · detail), 되돌린 상품 · 수량 등 |
| created_at | timestamptz | NOT NULL DEFAULT now() |

다이어그램 범례 "저장 값은 상태마다 하나, 사유는 detail"을 따른다: 상태는 `HELD` 하나, Pode 장애(`ROBOT_FAULT`)인지 Phody 장애(`ARM_FAULT`)인지 관제 재시작(`FLEET_RESTART`)인지는 `reason`으로 가른다. 11-20에 사유 이름을 contract 이름으로 바꿨다: `VISION_MISS` → `RECOGNITION_ERROR`, `PODE_FAULT` → `ROBOT_FAULT`, `PHODY_FAULT` → `ARM_FAULT`.

- HELD는 Pode 장애 · Phody 장애 · 관제 재시작(`FLEET_RESTART`)에만 쓴다 (v2.4, CEN-06 비고, 통신 규칙 #14 · #37). 담기 도중 장애는 자동 출고하지 않고 보류 → 관리자 일부 출고 강제 처리로 끝낸다 (통신 규칙 #20). 담은 것이 0개면 보류 대신 접수됨으로 되돌려 다시 배정한다(`REASSIGN`, #21). 결품으로 하나도 못 담은 운반 건은 HELD가 아니라 CANCELLED (`NOTHING_TO_FILL`, D-14).
- **보류를 끝내는 길**: Pode 장애 → 강제 출고 처리(`FORCED_SHIP`) 또는 선반에 되돌림(`RETURNED_TO_SHELF`, 바구니 상품을 창고에 되돌리고 `qty_placed`를 0으로, 접수됨으로 돌아가 다시 배정). Phody 장애 → 일부 출고 강제 처리(`FORCED_PARTIAL`).
- fleet-v1은 v2.4에 맞추기로 했다 (검토 문서 5장). 바뀌기 전까지 웹 서비스는 검토 문서 4장 규칙으로 보고를 바꿔 읽고, 원래 보고 값은 `detail`에 남긴다.
- Phony 장애(6-7)는 상태를 바꾸지 않는다 (운반 중 유지). 장애는 `alert`에 남고, 관리자가 강제 출고 처리하면 `FORCED_SHIP`으로 출고됨 · 일부 출고됨이 된다. R-07이 결정되면 다시 본다.

**누가 언제 쓰나**: 접수됨 · 취소됨(사용자 · 마감 재확인) · 선반에 되돌림은 웹 서비스가 직접 쓴다. 나머지 전이는 central · 관제의 진행 상황을 받아 웹 서비스가 쓴다. 지금 인터페이스(fleet-v1 contract 0.5)에서 쓸 수 있는 신호는 다음과 같다.

| 상태 | 신호 |
|---|---|
| 배정됨 | **신호 없음.** fleet-v1은 Pode가 작업대에 도착해야 주문을 묶으므로 이동 중 `/fleet/assignments`의 주문 번호가 비어 있다. 그래서 접수됨 → 담는 중 전이를 받는다 |
| 담는 중 | `/fleet/assignments` `FILL` 또는 `/central/events` `ORDER_BOUND` |
| 운반 중 | `/fleet/assignments` `TO_OUTBOUND` |
| 출고됨 · 일부 출고됨 · 보류 | `ReportOrder` (SHIPPED · PARTIAL · HELD, `placed` · `missing` 포함). 받으면 `placed_at`을 채운다 |

담기 일시 중지(FILLING_PAUSE)와 담는 중 → 운반 중 때의 `placed`는 아직 신호가 없다 (6절).

**11-20 (통신 규칙 #18)**: 진행 보고는 fleet → 브리지 `ReportTrip` 하나로 바뀐다 (접수됨 ~ 취소됨 9개 상태, `reason`, 누적 `placed`, `missing`, 번호 `(report_epoch, report_n)`). 위 표는 인터페이스가 바뀌기 전의 신호다. 반영 규칙은 5절 "진행 보고 반영".

### 4.8 alert — 관리자 알림 (OMS-03 · 09 · 12 · 14 · 16, SCR-08 · 10)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| alert_id | bigint identity | PK |
| level | text | NOT NULL, CHECK IN ('INFO', 'WARN', 'ERROR') (`FleetEvent.level`) |
| code | text | NOT NULL. `FleetEvent.code` 또는 웹 서비스 코드 (`OUT_OF_STOCK`, `LOW_STOCK`, `NEGATIVE_STOCK`, `LATE_REPORT`, `LATE_COMMAND_RESULT` 등) |
| source | text | NOT NULL (`central`, `fleet`, `order_service` 등) |
| robot_id | text | NULL 허용 |
| order_id | text | FK → orders, NULL 허용 |
| trip_id | text | FK → trip, NULL 허용 |
| product_id | bigint | FK → product, NULL 허용 (재고 부족) |
| event_id | text | UNIQUE, NULL 허용. `ReportEvent`의 `event_id` (보낸 쪽이 만든 UUID). 같은 번호로 다시 오면 새 줄을 만들지 않는다 (통신 규칙 #28 · #37). 웹이 스스로 여는 알림은 NULL |
| message | text | NOT NULL, 화면 문구 |
| detail | jsonb | 원본 이벤트. `LOW_STOCK`이면 재고 초과로 거절한 횟수 · 마지막 거절 시각도 여기에 |
| status | text | NOT NULL DEFAULT 'OPEN', CHECK IN ('OPEN', 'RESOLVED') ※ |
| created_at | timestamptz | NOT NULL DEFAULT now() |
| resolved_at | timestamptz | |
| resolved_by | bigint | FK → account. 자동 해결이면 NULL |

- 부분 UNIQUE 인덱스: `(code, product_id) WHERE status = 'OPEN' AND code = 'LOW_STOCK'` — 같은 상품의 재고 부족 알림이 쌓이지 않게 한다.
- 재고 초과 주문 거절(OMS-03, R-01)과 마감 뒤 재확인 거절도 이 `LOW_STOCK` 알림 하나로 합친다. 열린 알림이 있으면 `detail`의 거절 횟수만 올리고, 없으면 새로 연다 ※.
- `LOW_STOCK`은 재고가 바뀐 모든 기록(입고 · 반출 · 출고 차감 · 조정) 뒤에 다시 판단한다: 기준 아래면 열고, 기준 이상으로 오르면 자동 해결 (관리자 스토리보드 6번 탭 장면 9).
- 시험 모드 관련 웹 서비스 코드: `MODE_MISMATCH` 모드가 다른 로봇(다시 알린 뒤에도 다름, 배정에서 뺌), `TEST_LEFTOVER_STOP` 시험 모드에서 멈춘 채 정상 운영으로 넘어온 로봇(운영 DB에 생김, 로봇이 보통 복구 절차로 돌아오면 자동 해결) (5절 "시험 모드").
- 어떤 관제 이벤트를 알림으로 올리는지는 D-06(보류)에 달렸다. 테이블은 모든 `FleetEvent`를 담을 수 있게 두고, 고르는 규칙은 웹 서비스 설정으로 둔다.

### 4.9 admin_action — 관리자 입력 기록 (SCR-08 · 11 · 12 · 16 · 18 · 20 · 21, COM-02, SCR-19)

스토리보드의 관리자 버튼 하나를 누를 때마다 한 줄. 이력 화면 · 감사용. 간편 처리 패널(SCR-21)에서 누른 것도 같은 값으로 남는다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| action_id | bigint identity | PK |
| account_id | bigint | FK → account, NOT NULL |
| action | text | NOT NULL, CHECK IN (아래 값) |
| order_id | text | FK → orders, NULL 허용 |
| trip_id | text | FK → trip, NULL 허용 (운반 건에 대한 조치) |
| robot_id | text | NULL 허용 |
| alert_id | bigint | FK → alert, NULL 허용 (알림 칸에서 누른 경우) |
| close_id | bigint | FK → stock_close, NULL 허용 (마감 입력일 때) |
| payload | jsonb | 못 담음 상품 · 수량, 다시 투입 때 바구니(EMPTY · NONE), 락 대상 팔, 바꾼 설정의 이전 · 새 값 등 입력 값 |
| request_id | text | UNIQUE, NULL 허용. 관리 명령의 `request_id` (UUID 문자열). 다시 보낼 때 같은 번호, `ReportCommand`를 이 번호로 찾는다. ROS로 보내지 않는 입력(`LOCAL`)은 NULL (통신 규칙 #25 · #34 · #37) |
| result | text | NOT NULL, CHECK IN ('SENT', 'ACCEPTED', 'REJECTED', 'LOCAL') — ROS로 보낸 뒤 **접수 응답**(fleet 자기 판단). ROS로 보내지 않는 입력은 `LOCAL` |
| result_detail | text | 거절 사유. contract 사유 코드 이름 (`NOT_STOPPED` · `LOCK_RELEASED` · `NOT_TEST_MODE` · `MODE_MISMATCH` · `BATTERY_LOW` · `UNKNOWN_TARGET` · `ORDER_STATE` · `ROBOT_TIMEOUT` · `ROBOT_REJECTED` · `HOLDING_ITEM` 등, 통신 규칙 #25) |
| final_result | text | NULL 허용 (결과 보고 전 · `LOCAL`). `ReportCommand`의 **최종 결과**. CHECK IN ('ACCEPTED', 'REJECTED', 'UNCONFIRMED', 'TIMEOUT') ※. `UNCONFIRMED` = `ROBOT_TIMEOUT`(결과 모름, 끝 상태 아님). 그 뒤 늦은 응답 · `/fleet/robots` 관측으로 끝 상태를 **한 번만** 정하고(늦은 결과는 `LATE_COMMAND_RESULT` 알림), fleet이 로봇에 처음 요청한 때부터 30초 안에 정해지지 않으면 `TIMEOUT`으로 확정하고 알림 (통신 규칙 #25 ⑥) |
| created_at | timestamptz | NOT NULL DEFAULT now() |

action 값:

| 묶음 | 값 |
|---|---|
| 주문 조치 | 'RESUME_FILL' 재개, 'MARK_MISSING' 보충 불가 · 못 담음, 'FORCE_SHIP' 강제 출고 처리, 'FORCE_PARTIAL' 일부 출고 강제 처리, 'RETURN_TO_SHELF' 선반에 되돌림 (D-3) |
| 로봇 조치 | 'CONFIRM_CLEAR' 운행 복귀 확인(치움), 'REINSTATE' 다시 투입 (`/fleet/reinstate`), 'FAULT_RESOLVED' 장애 처리 완료, 'RESUME_ARM' 팔 일시 정지 재개, 'ARM_RECOVER' 팔 고장 복구 ※, 'LOCK_RELEASE' 락 해제, 'LOCK_ENGAGE' 락 잠금 (PDE-08 · PHD-10) |
| 서비스 | 'SERVICE_PAUSE', 'SERVICE_RESUME', 'ESTOP', 'ESTOP_RELEASE', 'SETTING_CHANGE' 설정 변경 (4.13) |
| 마감 | 'CLOSE_START', 'CLOSE_CONFIRM', 'CLOSE_CANCEL' (OMS-18 · CEN-13) |
| 시험 (시험 DB에만 생김) | 'MOCK_SCENARIO' 시나리오 주문 실행, 'MOCK_ORDER' 개발자 직접 주문, 'FORCE_CONDITION' 조건 바꾸기, 'FORCE_STATE' 상태 바꾸기(멈춤 계열), 'FORCE_CLEAR' 강제 해제, 'CHARGE_END' 충전 끝내기 (SCR-17 · OMS-21) |

- 스토리보드의 "운행 복귀 확인"은 fleet-v1에서 `confirm_clear`(치움)와 `reinstate`(빈 바구니 유무를 실어 다시 넣음) 두 단계다. 두 줄로 남긴다.
- 작업 모드(RUN · PAUSE · STOP)와 락 상태의 **현재 값**은 DB에 두지 않는다. 작업 모드는 central이 `OrderBoard.run_mode`로, 락은 로봇 상태로 알린다. DB에는 바꾼 기록만 남는다.
- 입고 · 반출 · 수시 조정은 여기 남기지 않는다. `stock_ledger`에 관리자와 함께 남는다.
- 마감 시작 · 확정 · 취소는 여기(버튼과 central 전달 결과)와 `stock_close`(업무 원본)에 둘 다 남긴다. 역할이 다르다.

### 4.10 stock_close — 마감 (OMS-18, CEN-13, SCR-18)

마감 한 번에 한 줄. 마감의 **현재 상태**도 여기서 읽는다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| close_id | bigint identity | PK |
| status | text | NOT NULL DEFAULT 'IN_PROGRESS', CHECK IN ('IN_PROGRESS' 진행 중, 'CONFIRMED' 확정, 'CANCELLED' 취소) |
| started_at | timestamptz | NOT NULL DEFAULT now() |
| started_by | bigint | FK → account. 운영 재개 마감은 서버가 시작하므로 NULL |
| close_kind | text | NOT NULL DEFAULT 'MANUAL', CHECK IN ('MANUAL' 관리자가 시작, 'TEST_ENTRY' 시험 모드 진입, 'OPS_RESUME' 정상 운영 재개) |
| hold_acked_at | timestamptz | central이 배정 보류를 주문판에 반영했다고 답한 시각. 이 값이 있어야 실사를 연다 |
| counting_started_at | timestamptz | 실사를 연 시각 (화면의 "기다린 시간") |
| finished_at | timestamptz | 확정 · 취소 시각 |
| finished_by | bigint | FK → account |
| release_acked_at | timestamptz | 확정 · 취소 뒤 central이 배정 재개를 반영했다고 답한 시각. 비어 있으면 웹이 다시 보낸다 |
| note | text | 취소 사유 등 |

- 부분 UNIQUE 인덱스: `((true)) WHERE status = 'IN_PROGRESS'` — 진행 중인 마감은 하나뿐.
- `CHECK ((status = 'IN_PROGRESS') = (finished_at IS NULL))`
- `CHECK ((close_kind = 'OPS_RESUME') = (started_by IS NULL))`, `CHECK (close_kind <> 'OPS_RESUME' OR status <> 'CANCELLED')` — 운영 재개 마감은 취소할 수 없다 (실사를 건너뛰고 배정하지 못하게).
- 지금 마감 중인지 = `status = 'IN_PROGRESS'`인 행이 있는지.
- 마감 주기(하루 한 번 · 여러 번)는 정하지 않았다. 날짜 칸 없이 시각으로만 판단하므로 어느 쪽이든 담긴다.

### 4.11 stock_count — 실사 결과 (OMS-18, SCR-18)

마감 때 상품마다 한 줄, 수시 조정 때 그 상품 한 줄. 창고(작업대 선반 포함) 전체를 센다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| count_id | bigint identity | PK |
| close_id | bigint | FK → stock_close, NULL 허용. NULL이면 수시 조정 |
| product_id | bigint | FK → product, NOT NULL |
| counted_qty | integer | CHECK >= 0. 실제로 센 수량. 마감 중 임시 저장 때 NULL 허용 (화면은 빈칸으로 시작) |
| book_qty | integer | CHECK >= 0. 장부 수량. 마감은 **확정 순간**의 `stock_qty`, 수시 조정은 저장 순간의 `stock_qty` |
| in_transit_qty | integer | NOT NULL DEFAULT 0, CHECK >= 0. 수시 조정 때 진행 중 운반 건 바구니에 있던 수. 마감은 0 (진행 중 0건일 때만 세므로) |
| diff_qty | integer | GENERATED ALWAYS AS (counted_qty − (book_qty − in_transit_qty)) STORED |
| reason | text | 차이 사유, 고르는 목록. CHECK IN ('COUNT_DIFF' 재고 조사 차이, 'RECOGNITION_ERROR' 인식 오류 (11-20 이름 바꿈, 통신 규칙 #37), 'DAMAGE_FOUND' 파손 발견, 'LOST' 분실, 'INPUT_ERROR' 입력 오류, 'TEST_RUN' 시험 영향, 'OTHER' 기타) — `TEST_RUN`은 운영 재개 마감에서만. 나머지 값은 팀 확인 필요 |
| account_id | bigint | FK → account, NOT NULL. 마지막으로 입력한 관리자 (두 명이 나눠 셀 때) |
| note | text | 사유 `OTHER`면 필수 |
| created_at | timestamptz | NOT NULL DEFAULT now() |
| updated_at | timestamptz | NOT NULL |

- UNIQUE `(close_id, product_id)` — 한 마감에 상품마다 한 번.
- 차이가 0이 아니면 `stock_ledger`에 `CLOSE_ADJUST`(마감 확정 때) 또는 `SPOT_ADJUST`(수시 조정 저장 때) 한 줄을 `count_id`와 함께 쓴다.
- 마감을 취소해도 행은 감사용으로 남긴다. 원장이 취소된 마감의 행을 가리키지 못하게 막는다 ※.

### 4.12 v_in_flight — 진행 중 운반 건 (뷰)

결품 사전 확인, 실사 여는 조건, 수시 조정 막기가 같이 쓴다.

- 운반 건 상태가 ASSIGNED · FILLING · FILLING_PAUSE · TRANSPORTING · HELD
- **또는** 운반 건은 RECEIVED이지만 같은 주문의 다른 운반 건이 이미 시작됨(접수됨 · 취소됨이 아님) — 시작된 나뉜 주문의 남은 운반 건

### 4.13 app_setting — 운영 설정 (D-6, R-15)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| key | text | PK, CHECK IN ('max_order_items' 주문 한 건의 최대 상품 개수, 'basket_capacity' 바구니 하나의 최대 상품 개수) |
| value | integer | NOT NULL, CHECK > 0 |
| updated_at | timestamptz | NOT NULL |
| updated_by | bigint | FK → account |

- `basket_capacity`의 처음 값은 4 (fleet-v1 `basket_capacity`와 같게). `max_order_items`의 처음 값은 8 (바구니 2개, 11-19 사용자 결정).
- 바꾸면 `admin_action` `SETTING_CHANGE`에 이전 · 새 값을 남긴다. 마감 중에도 바꿀 수 있다 (재고와 관계없음).
- **원본은 이 DB 하나다 (2026-11-25 사용자 결정).** 관리자가 정하는 중요 설정 값은 한 곳에서 관리한다. 관제(fleet-v1)의 `fleet_params.yaml` `basket_capacity`(지금 4, 주석 "추정")는 따로 두지 않고, DB 값을 관제에 전달한다.
  - 이유: 관제는 품목 합이 자기 값보다 큰 운반 건을 배정하지 않고 `ReportOrder`도 보내지 않는다(경고 `ORDER_OVER_CAPACITY` 한 번, `pc/fleet_core/.../orders/pseudocode.md`). 두 값이 어긋나 웹 값이 크면 운반 건이 "접수됨"으로 멈춘다.
  - **전달 방식 (2026-11-25 사용자 결정): 켜질 때는 `Hello`, 바뀔 때는 서비스.**
    - 관제가 켜질 때: 관제가 부르는 `Hello`의 응답에 설정 값을 싣는다. 관제는 `Hello` 응답을 받아야 보고를 시작하므로 그 자체가 확인이다.
    - 관리자가 바꿀 때: 브리지 → 관제 서비스(이름 예: `/fleet/set_settings`)로 목표 값(IR-21)과 설정 번호를 보내고, 관제는 적용한 번호로 응답한다. 응답이 없으면 같은 번호로 다시 보내고, 관리자 화면은 "관제 적용 대기"였다가 응답을 받으면 "적용됨"으로 바꾼다.
    - 토픽이 아닌 이유: 중요 설정은 관제가 적용했다는 확인이 필요하다. 액션이 아닌 이유: 곧바로 끝나는 요청이라 IR-10 표의 "한 번 보내는 요청 = 서비스"다.
  - 시기: 인터페이스 변경(`Hello` 응답 칸, 새 서비스)이라 IR-90 절차로 **다음 주**에 넣는다(contract 0.8 → 0.9). **이번 주는 DB · 관제 모두 4로 둔다.**
  - 함께 정할 것 (다음 주): 값을 줄였을 때 이미 더 큰 값으로 나뉜 운반 건을 관제가 막지 않는 규칙(DB는 "이미 나뉜 운반 건은 그대로"), 설정 번호를 둘 곳, `max_order_items`도 같은 길로 보낼지.

### 4.14 ws_event — 화면 WS 사건 (OMS-05, 통신 규칙 #32 · #37)

화면 WS의 "사건"(주문 · 운반 건 상태 변화, 알림 발생 · 해결, 관리 명령 결과, 고객 위쪽 알림)에 DB 순번을 붙여 남긴다. 다시 연결한 화면은 `since_seq` 뒤의 줄만 받는다. "상태"(로봇 · 작업 상태 · 재고 · 마감 진행)는 순번 없이 최신 값만 보내므로 여기 넣지 않는다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| seq | bigint identity | PK. 사건 순번 |
| type | text | NOT NULL. 사건 종류. 상태 변화 트리거는 테이블 이름(`orders` · `trip` · `alert`)을 넣는다 |
| data | jsonb | NOT NULL. 사건 내용. 상태 변화 트리거가 화면 WS `data` 칸(명세 3.1, 2026-11-23)을 그 시점 값으로 채운다: `orders` → `{order_id, status}`, `trip` → `{trip_id, order_id, status, robot_id, items}`(`items`는 `trip_item`에서). 고객 말(`status_text`)로 바꾸기는 웹 서비스가 보낼 때 한다 ※ |
| created_at | timestamptz | NOT NULL DEFAULT now() |

- **같은 트랜잭션에서 INSERT 후 NOTIFY**: `ws_event` AFTER INSERT 트리거가 `NOTIFY`한다. 그래서 변경이 롤백되면 사건도 알림도 남지 않는다. 주문 · 운반 건 · 알림의 상태 변화는 그 테이블 트리거가 `ws_event`에 넣고, 관리 명령 결과처럼 테이블 상태 변화가 아닌 사건은 웹 서비스가 같은 트랜잭션에서 직접 넣는다.
- 다시 받을 수 있는 기간(최근 24시간 또는 1,000건 중 많은 쪽)보다 오래된 줄을 지우는 일은 웹 서비스가 한다 (통신 규칙 #32).

### 4.15 bridge_session — 노드별 epoch (통신 규칙 #18 · #37)

보고를 보내는 노드가 켜질 때 브리지 `Hello`를 부르면 DB가 그 노드의 `epoch`를 1 늘려 돌려준다. 노드는 `epoch`를 받기 전에는 `Report*`를 보내지 않는다.

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| node | text | PK. 보내는 노드. **지금은 `ReportTrip`을 보내는 fleet만 쓴다** (central의 재시작 구분은 통신 규칙 다음 주 논의 2) |
| epoch | integer | NOT NULL, CHECK > 0. `Hello` 때마다 1씩 늘림 |
| boot_id | text | NOT NULL. 노드가 켜질 때 만든 UUID (기록용) |
| hello_at | timestamptz | NOT NULL DEFAULT now(). 마지막 `Hello` 시각 |

- `ReportCommand` · `ReportEvent`는 `request_id` · `event_id`로 중복을 가리므로 `epoch`를 쓰지 않는다.

### 4.16 trip_dispatch — 운반 건 넘기기 기록 (SB-04, 2026-11-25 확정)

웹이 접수한 운반 건을 브리지가 central에 넘겼는지(`SubmitTrip`) 기록한다. 운반 건 한 건에 한 줄. 브리지는 `central_acked_at`이 빈 줄을 찾아 보낸다. 그래서 브리지가 다시 켜져도 아직 넘기지 못한 건을 찾을 수 있다(서비스 브리지 조사 D-2).

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| trip_id | text | PK, FK → trip |
| attempts | integer | NOT NULL DEFAULT 0, CHECK >= 0. central에 보낸 횟수 |
| next_attempt_at | timestamptz | 다음에 다시 보낼 시각. 이번 주는 비워 둠 |
| unknown_since | timestamptz | 결과 모름이 시작된 시각(화면 "확인 중"). 이번 주는 비워 둠 |
| last_result | text | 마지막 응답(`NOT_READY` 등). 이번 주는 비워 둠 |
| central_acked_at | timestamptz | central이 접수 · 거절로 확정 응답한 시각. NULL이면 아직 넘기지 않은 건 |

- 주문 등록 트랜잭션에서 `orders` · `trip`과 함께 운반 건마다 한 줄을 쓴다. 접수는 됐는데 넘길 기록이 없는 경우가 생기지 않는다.
- `trip`에 칸을 더하지 않는 이유: `trip`의 `updated_at` · `ws_event` 트리거가 보낼 때마다 돌아 화면 사건이 쌓인다.
- 보내는 순서는 처음 접수 시각 순(`trip.created_at`, 통신 규칙의 `trip.submitted_at` · Q-16)이다. central 주문판이 처음 접수 시각 순이기 때문이다.
- 부분 인덱스: `(trip_id) WHERE central_acked_at IS NULL` — 브리지가 1초마다 찾는 줄.
- 칸 여섯 개를 지금 모두 만들고, 이번 주에는 `trip_id` · `attempts` · `central_acked_at`만 채운다. central이 거절했을 때의 처리는 다음 주에 정한다(지금은 `last_result`만 남긴다).

### 관계 요약

```
account 1 ─< orders 1 ─< order_item >─ 1 product 1 ─< stock_ledger >─ stock_count >─ stock_close
                │  1 ─< trip 1 ─< trip_item >─ product         │
                │          └──< stock_ledger (SHIP)             └─ trip
                │  1 ─< status_history >─ trip
                │  1 ─< alert >─ trip / product
account 1 ─< admin_action >─ orders / trip / alert / stock_close
app_setting · ws_event · bridge_session (독립)
trip 1 ─ 1 trip_dispatch
```

## 5. 동작 규칙 (DB에 걸린 쪽)

### 주문 · 운반 건

- **주문 등록 (OMS-03 · 04)**: 같은 `Idempotency-Key`(`orders.idem_key`)로 다시 온 요청은 새로 접수하지 않는다 (통신 규칙 #34). 품목 합 > `max_order_items`이거나 상품마다 주문 수량 > `stock_qty`면 거절 (주문을 기록하지 않음). 재고 초과 거절은 `LOW_STOCK` 알림에 합친다. 통과하면 같은 트랜잭션에서 `orders` · `order_item`과 `basket_capacity` 이하로 나눈 `trip` · `trip_item`을 만든다. 예약하지 않는다(OMS-07 미포함). 마감 중에도 접수는 받는다.
  - 접수 트랜잭션은 첫 문장에서 7001 공유를 잡고(아래 "재고 기록"의 잠금 7001), `orders` · `trip`과 함께 운반 건마다 `trip_dispatch` 한 줄을 쓴다 (4.16, 2026-11-25 확정).
- **결품 사전 확인 (6-2 #4, OMS-02)**: 상품마다 `가용 = stock_qty − (v_in_flight 운반 건의 qty_placed 합)`. 가용 < 남은 수량이면 결품. 결품이면 담기 일시 중지 → 관리자가 추가 입고를 등록하고 재개하거나 보충 불가(R-18). 재고는 창고 기준이므로 여기서 결품이면 창고에도 없다. **전제**: 출고 전에 `qty_placed`가 채워져 있어야 한다. fleet-v1은 출고 · 보류 때만 `placed`를 보내므로 이 신호가 없으면 앞선 운반 건 수량을 모른다 (6절).
- **사용자 취소 (OMS-10, 통신 규칙 #20 · #21 · #22)**: 그 주문의 모든 운반 건이 `RECEIVED`일 때만 (담기를 시작한 운반 건은 사용자 취소됨으로 끝나지 않음, 접수됨으로 돌아온 운반 건은 취소 허용). 웹이 central에 `CancelOrder`를 보내고, **받아들인다는 접수 응답을 받은 트랜잭션 안에서 웹이 직접** 운반 건 · 주문을 취소됨(`USER_CANCEL`, actor USER)으로 쓴다. 거절(`ALREADY_ASSIGNED` · `NOT_FOUND`)이면 쓰지 않는다. 그 뒤 fleet이 같은 취소됨을 보고하면 무시한다 (상태가 같으므로 번호만 갱신하고 ACK). 판단 주인은 통신 규칙 다음 주 논의 1.
- **재고 차감 (6-1-3 #33, OMS-06)**: 운반 건이 출고됨 · 일부 출고됨이 될 때 상품마다 `qty_placed`만큼 `SHIP` 한 줄 (`trip_id`). 강제 출고 처리 · 일부 출고 강제 처리도 같다. 취소됨 · 선반에 되돌림은 차감 없음.
- **출고 차감이 재고 0 아래로 가면**: 차감만 따로 시도(SAVEPOINT)하고, 실패하면 출고됨 기록은 남기고 `NEGATIVE_STOCK` 알림을 연다. 관리자가 수시 조정 등으로 맞춘 뒤 차감한다.
- **진행 보고 반영 (통신 규칙 #18 · #37)**: 브리지가 받은 `ReportTrip` · `ReportCommand` · `ReportEvent`를 DB에 쓰는 곳은 **웹 서비스의 함수 하나(`apply_report`)**다. 브리지는 이 함수가 커밋한 뒤 응답하고, 응답이 없으면 보낸 쪽이 같은 번호로 다시 보낸다. 한 트랜잭션에서:
  - `ReportTrip`: 운반 건마다 `(report_epoch, report_n)`을 `trip`의 값과 비교한다. **더 클 때만 반영**하고(상태 · `placed` · `missing` · `placed_at`, 이력, 주문 요약, 재고 차감, `trip.report_epoch` · `report_n` 갱신), **같거나 작으면 아무것도 바꾸지 않고 `DUPLICATE`로 ACK만** 한다 (재전송 · 재시작 이전의 늦은 보고, IR-24). 접수됨 복귀가 정상 전이이므로 상태 순서가 아니라 번호로 가린다. 번호는 더 크지만 **허용되지 않는 전이**(아래 "허용 전이" 표. 예: 이미 끝난 운반 건에 다른 결과, 웹이 취소됨으로 쓴 뒤의 배정됨)는 덮어쓰지 않고 `LATE_REPORT` 알림을 연다 → 관리자 확인 뒤 출고 차감 (`account_id` 채움). 처음 배정됨이 되면 `first_assigned_at`을 한 번만 채운다.
  - `ReportCommand`: `admin_action.request_id`로 줄을 찾아 `final_result`를 쓴다 (4.9).
  - `ReportEvent`: `alert.event_id`가 이미 있으면 ACK만 한다.
  - 반영한 상태 변화는 같은 트랜잭션에서 `ws_event`에 남고 NOTIFY된다 (4.14).
- **`Hello` (통신 규칙 #18, 9장 2절 5)**: `bridge_session`의 그 노드 `epoch`를 1 늘려 돌려준다. fleet이 새 `epoch`로 `Hello`를 부르면 직전 `epoch`에서 배정됨 ~ 운반 중이던 운반 건을 보류(`FLEET_RESTART`)로 쓴다. 다시 배정할 운반 건 목록(`ListOpenTrips`)은 `trip.created_at`(= `submitted_at`) 순으로 답한다 (#21).
- **허용 전이 (SB-03, 2026-11-24 확정)**: 번호가 더 큰 `ReportTrip`의 상태가 지금 `trip.status`에서 아래로 가면 반영(`APPLIED`), 아니면 `LATE_REPORT`. fleet은 기다리는 보고를 최신 상태로 바꿔 끼우므로(IR-23) 중간 단계가 빠진 채 올 수 있어, **앞으로 건너뛰는 전이는 허용**한다.

  | 지금 상태 | 허용 (번호가 더 크면 반영) | `LATE_REPORT` |
  |---|---|---|
  | RECEIVED | ASSIGNED · FILLING · FILLING_PAUSE · TRANSPORTING · SHIPPED · PARTIAL · HELD · CANCELLED(D-14) | – |
  | ASSIGNED | FILLING · FILLING_PAUSE · TRANSPORTING · SHIPPED · PARTIAL · HELD · CANCELLED(D-14) · RECEIVED(되돌림 `REASSIGN`) | – |
  | FILLING · FILLING_PAUSE | 서로, TRANSPORTING · SHIPPED · PARTIAL · HELD · CANCELLED(D-14) · RECEIVED(`REASSIGN`) | ASSIGNED |
  | TRANSPORTING | SHIPPED · PARTIAL · HELD | RECEIVED · ASSIGNED · FILLING · FILLING_PAUSE · CANCELLED |
  | HELD | SHIPPED · PARTIAL(강제 출고 처리) · RECEIVED(선반에 되돌림 · `REASSIGN`) · ASSIGNED · FILLING · TRANSPORTING(이어서 진행) | CANCELLED |
  | SHIPPED · PARTIAL · CANCELLED (끝) | 같은 상태(번호만 갱신, 예: 웹이 취소됨으로 쓴 뒤 fleet의 같은 취소됨) | 다른 모든 상태 |

  - 지금과 같은 상태가 오면 칸(`placed` · `missing`)과 번호만 갱신하고 이력은 남기지 않는다.
  - 이 표는 상태만 본다. 마감 중 배정 · 사용자 취소처럼 다른 규칙이 막는 경우는 그 규칙을 따른다.
- **잠금 순서 (SB-05, 2026-11-24 확정)**: 재고 원장에 쓸 수 있는 트랜잭션(`apply_report`의 출고 반영, 강제 출고 처리, 마감, 입고 · 반출 등)은 **7001 공유(마감은 배타) → orders(`order_id` 순) → trip(`trip_id` 순) → 원장 행** 순서로 잠근다. 예외 없음. `apply_report`는 `trip`에서 `order_id`만 잠금 없이 읽은 뒤 첫 줄에서 `SELECT pg_advisory_xact_lock_shared(7001)`을 부르고 orders부터 잠근다(원장 트리거가 같은 트랜잭션에서 다시 잡아도 문제없음). 주문 접수도 7001 공유를 잡는다(2026-11-25, V2 접수 부분). 원장 행이 없으므로 7001 → orders → trip 순서다. 모두 같은 순서를 따르므로 교착이 생기지 않는다. 잠금 7001은 아래 "재고 기록"의 설명을 본다.
- **주문 요약 상태**: 운반 건 상태를 바꾸는 트랜잭션에서 4.4 규칙으로 `orders.status`를 다시 계산하고, 바뀌면 이력에 `trip_id` 없는 한 줄.

### 재고 기록

- **재고는 원장 트리거 한 길로만 바뀐다 (OMS-08)**: `stock_ledger` BEFORE INSERT 트리거 하나가 차례로:
  1. 공유 잠금(`pg_advisory_xact_lock_shared`)을 잡는다.
  2. 진행 중 마감이 있으면 `OUTBOUND` · `SPOT_ADJUST`를 거절한다. `INBOUND`는 실사를 연 뒤(`counting_started_at` 있음)부터 거절한다. `SHIP` · `CLOSE_ADJUST`는 받는다. 입력 오류 바로잡기도 그 종류(입고 · 반출)의 규칙을 따른다.
  3. 입력 오류 바로잡기면: 원래 기록이 같은 상품, 입고 또는 반출, 부호 반대, 같은 수량인지 확인한다. 원래 기록이 마지막 확정 마감의 `finished_at`보다 앞서면 거절한다 ("마감 이전 기록은 고칠 수 없음", 날짜가 아니라 시각).
  4. `product.stock_qty`를 바꾸고 `qty_after`를 채운다.
- 원장은 UPDATE · DELETE 금지 트리거로 막는다.
- **잠금 7001이란**: 표의 행이 아니라 **번호로 정한 잠금 하나**다(PostgreSQL advisory lock). 같은 번호를 잡으려는 트랜잭션끼리만 서로 기다리고, `pg_advisory_xact_lock…`이라 **트랜잭션이 끝나면(커밋 · 롤백) 저절로 풀린다.** 7001은 이 용도로 정한 번호일 뿐 다른 뜻은 없다. 목적은 마감이 "어느 순간의 재고"를 깨끗하게 잡게 하는 것이다(확정 도중 출고 차감 같은 원장 기록이 끼면 장부 수량과 실사 차이가 어긋남).

  | 잠그는 쪽 | 방식 | 동시에 잡을 수 있는 수 | 막는 것 |
  |---|---|---|---|
  | 원장 기록(입고 · 반출 · 출고 차감 · 조정) — 원장 INSERT 트리거 1번 | **공유** `pg_advisory_xact_lock_shared(7001)` | 상한 없음 (실제로는 DB 연결 수 · 연결 풀 크기만큼) | 배타만 |
  | 주문 접수(OMS-03 · 04) — 접수 트랜잭션 첫 문장 (V2 접수 부분, 2026-11-25 확정) | **공유** `pg_advisory_xact_lock_shared(7001)` | 상한 없음 | 배타만 |
  | 주문 번호 매기기 — 접수 트랜잭션, `orders` 넣기 직전 (2026-11-26) | **배타** `pg_advisory_xact_lock(7002)` | 1 | 접수끼리만. 커밋까지 쥔다. 순서는 7001 공유 → 7002 → orders |
  | 마감 시작 · 확정 — 마감 트리거 | **배타** `pg_advisory_xact_lock(7001)` | 1 | 공유 · 배타 모두 |

  - 공유끼리는 막지 않아 원장 기록 여러 개가 동시에 진행된다. 배타는 진행 중인 공유가 모두 끝날 때까지 기다리고, 잡고 있는 동안 새 공유 요청을 모두 기다리게 한다(마감 확정 중에는 출고 차감이 잠깐 멈춤).
  - PostgreSQL은 기다리는 순서를 지킨다. 마감이 배타를 **기다리는 중**이면 그 뒤에 온 공유 요청도 마감 뒤에 줄을 선다. 그래서 브리지의 `apply_report`는 `lock_timeout`(조사 문서 제안 500ms)을 두고, 넘기면 `accepted=false`로 답해 fleet이 같은 번호로 다시 보내게 한다.
  - 주문 접수도 7001 공유를 잡는다(2026-11-25 확정, 마감 보완 토의 B4 · U4, 종합 검토 V2). 접수는 `stock_qty`로 재고 초과를 판단하므로, 마감 확정 도중 조정 전 재고로 통과한 접수가 재확인 뒤에 커밋되는 빈틈을 막는다. 접수는 원장 행을 잠그지 않으므로 순서는 7001 → orders → trip이다. 마감 확정 동안에는 접수가 잠깐 기다리고, `lock_timeout`을 넘기면 503 + 같은 `Idempotency-Key`로 다시 보내게 한다(웹 쪽 값은 종합 검토 V6과 함께 정함).
  - 7001을 잡지 않는 일(조회 등)은 영향을 받지 않는다.
- **재고 부족 알림 (OMS-09)**: 원장 한 줄마다 `LOW_STOCK`을 다시 판단해 열거나 자동 해결한다 (4.8).
- **마감 직후 입고**: 마감 전에 이미 창고에 넣은 물건을 마감 뒤 입고로 다시 넣으면 두 번 세게 된다. 화면 확인 창으로 막는다 (DB 변경 없음).

### 마감 (OMS-18 · CEN-13, R-08 · R-17, 관리자 스토리보드 7번 탭)

1. **시작**: 배타 잠금(`pg_advisory_xact_lock`) → `stock_close` IN_PROGRESS → `admin_action` CLOSE_START → central에 "보류 + 주문판 목록" 전송. 주문판에는 **시작된 나뉜 주문의 남은 운반 건만** 남긴다. 답이 오면 `hold_acked_at`. 이때부터 반출 · 수시 조정은 DB가 막는다. 입고는 실사를 열 때까지 받는다.
2. **기다림**: `v_in_flight`(시작된 나뉜 주문의 남은 운반 건 포함)와 열린 알림을 화면에 보여 준다 (예: "남은 운반 건 2건 처리 중 (주문 #1027)"). 남은 운반 건에서 선반만 비었으면 창고에서 갖다 놓는다 (기록 없음, 막히지 않음). DB 재고 부족 결품이면 바깥에서 들여와 추가 입고를 등록하고 재개하거나, 보충 불가로 처리한다. 보류 반영 전에 묶인 운반 건의 접수됨 → 담는 중은 받는다. 시간 제한은 없다. 끝내려면 관리자가 마감 취소.
3. **실사**: `hold_acked_at` 있음 + `v_in_flight` 0건이면 `counting_started_at`을 적고 입력을 연다. 입력은 그때그때 저장한다 (`counted_qty` 빈칸 허용).
4. **확정** (한 트랜잭션): 배타 잠금 → 검사 (모든 상품에 행이 있고 `counted_qty`가 채워졌는지, 차이가 있는 행에 사유가 있는지, `v_in_flight` 0건인지. 하나라도 어기면 확정을 막음) → `book_qty := stock_qty` → 차이 행만 `CLOSE_ADJUST` → 대기 주문 재확인 → 커밋 → central에 재개 전송 → 답이 오면 `release_acked_at`.
   - **대기 주문 재확인 (D-2 가)**: 모든 운반 건이 접수됨인 주문만, 접수 순(`orders.created_at`)으로 상품별 주문 수량을 쌓아 재고와 비교한다. 넘는 주문은 **주문 전체**를 취소됨(`STOCK_RECHECK`, actor SYSTEM)으로 바꾸고 건너뛴다. 뒤의 작은 주문은 계속 본다. 거절은 `LOW_STOCK` 알림에 합친다.
5. **취소**: `CANCELLED` + 사유(`note`), 재고는 그대로, 실사 행은 남김, central에 재개 전송. 운영 재개 마감(`OPS_RESUME`)은 취소할 수 없다.

### 시험 모드 (SCR-17 · COM-06 · OMS-21, R-20)

모드는 서비스를 켤 때 정한다. DB는 모드마다 따로 있고(예: `ifp` · `ifp_test`), 서버는 켤 때 DB 이름이 모드와 맞는지 확인한다.

| | 정상 운영 | 시험 모드 |
|---|---|---|
| DB | 운영 DB | 시험 DB |
| 주문 | 사용자 화면 | 시나리오 주문 · 개발자 직접 주문 (시험 계정으로). 사용자 화면은 "점검 중" |
| 강제 입력 (조건 · 상태 바꾸기) | 없음 (메뉴 · API 없음, 로봇도 무시) | 개발자(`DEV`)만 |
| 마감 | 관리자가 원할 때 (`MANUAL`) | 시험 DB에서는 하지 않음 |

1. **진입** (정상 운영 → 시험 모드): 관리자 화면 8번 탭 [시험 모드로](개발자)에서 모달로 진행한다. 정상 운영에서 `TEST_ENTRY` 마감을 시작해 확정한다. 남은 운반 건 0까지 기다리는 것이 그대로 안전한 종료 조건이다. 보통 마감과 달리 확정 뒤 대기 주문 재확인 · 배정 재개를 하지 않고 서비스를 내린다. 접수됨 주문은 운영 DB에 그대로 남는다. 확정 전에는 모달의 [진입 취소]로 취소할 수 있다 (`CANCELLED`, 배정 다시 시작, 정상 운영 유지).
2. **시험 DB 준비**: 시험 DB를 새로 만들고, 상품, 관리자 · 개발자 계정(운영 DB에서 복사), 시험 계정을 넣은 뒤 마지막 `TEST_ENTRY` 마감의 실사 수량을 입고(사유 기타, 설명 "시험 시작 재고")로 넣는다. 그래서 시험 중 결품 사전 확인이 실제 선반과 맞는다.
3. **시험**: 시나리오 · 직접 주문과 강제 입력. 서비스가 켜질 때, 로봇이 다시 연결될 때, 로봇이 보고한 모드가 다를 때 로봇 노드에 모드를 알리고, 로봇은 시험 모드일 때만 강제 입력을 받는다. 조건 바꾸기(배터리 · 장애물 · 집기 실패 등 판단 값)는 로봇이 원래 규칙대로 상태를 바꾸고 실제로 동작하게 하고, 상태 바꾸기는 멈춤 계열(CAUTION · ERROR · PAUSED)만 받아 실제로 멈추고 보고한다 (PC가 상태만 덮어쓰면 현장과 화면이 달라짐). 실제 값과 강제 값 중 나쁜 쪽을 쓴다. 충전 중인 Pode의 [충전 끝내기]는 실제 배터리가 이동 기준(35%) 이상일 때만 받아 남은 충전을 건너뛴다 (기다리는 시간만 줄임). 입력은 시험 DB의 `admin_action`에 남는다 (관리자 스토리보드 설계 v1.4).
4. **해제** (시험 모드 → 정상 운영): 8번 탭 [정상 운영으로]. 시험 DB의 진행 중 운반 건이 0이어야 끈다 (시험 DB 실사는 하지 않음). 다시 켤 때 로봇에 정상 운영을 알려 강제 값을 모두 지운다. 정상 운영은 `OPS_RESUME` 마감이 진행 중인 상태로 켜진다 (배정 보류). 실사로 시험 동안 현장에서 움직인 상품을 맞추고, 시험 때문에 생긴 차이는 사유 "시험 영향"으로 남긴다. 확정하면 접수됨 주문을 재확인하고 배정을 시작한다.
   - 강제로 멈춘 상태(CAUTION · ERROR · PAUSED)는 강제 값을 지워도 남는다. 해제를 막지 않고, 운영 DB에 `TEST_LEFTOVER_STOP` 알림을 열고 운영 재개 마감 모달에 목록으로 보인다. 로봇 화면의 보통 복구 절차로 되돌린다. 판단 규칙: 서버는 그 멈춤이 강제 입력 때문인지 실제 장애인지 구분할 수 없으므로, 운영 재개 마감(`OPS_RESUME`) 중에 멈춤 계열 상태로 보고된 로봇은 원인과 상관없이 모두 `TEST_LEFTOVER_STOP`으로 열고, 알림에 로봇이 보고한 원인을 함께 적는다.
- 시험 DB는 다음 진입 때 지우고 새로 만든다. 시험 기록을 남길지는 만드는 사람에게 맡긴다 (9절).

### 수시 조정 (OMS-18, 관리자 스토리보드 6번 탭 장면 7)

- `close_id` 없는 `stock_count` 한 줄과 `SPOT_ADJUST`. 창고(작업대 선반 포함) 전체를 센다.
- **막는 경우 (D-4 가)**: 진행 중 마감이 있을 때, 그리고 그 상품을 담는 `v_in_flight` 운반 건 중 `placed_at`이 NULL인 것이 있을 때 (바구니에 몇 개 있는지 모름). 담기 일시 중지 때 담은 수를 알려 주는 인터페이스가 생기면 이 막음을 푼다.
- 받는 경우: `book_qty` = 그 순간의 `stock_qty`, `in_transit_qty` = 그 상품을 담은 진행 중 운반 건의 `qty_placed` 합. 화면은 "재고 2 (바구니 1) · 창고에 있어야 할 수 1".

### 화면 전달 (OMS-05)

주문 · 운반 건 상태, 알림, 마감 상태가 바뀌면 `NOTIFY`로 웹 서비스에 알리고 웹 서비스가 WS로 보낸다. 11-20부터 주문 · 운반 건 · 알림의 변화는 사건으로 `ws_event`에 한 줄 넣고 같은 트랜잭션에서 NOTIFY한다(내용에 `seq`). 마감은 상태라 순번 없이 NOTIFY만 한다 (4.14, 통신 규칙 #32 · #37).

## 6. 미결 · 다른 문서에 영향

- **인터페이스 작업에 넘길 것** (별도 작업):
  - 웹 → central 주문 전달(BRG-01): central에 넘기는 주문 번호는 `trip_id`. contract 0.5에 정의가 없으니 "번호는 웹 서비스가 만들어 넘긴다"를 전제로 넘긴다.
  - 진행 상황 보고: 배정됨 신호, 담기 일시 중지 상태와 그때까지 담은 수, 담는 중 → 운반 중 때의 `placed` · `missing`. 결품 사전 확인(5절)과 수시 조정 막음 풀기(D-4)가 여기에 달려 있다. 다이어그램 R-14와 같은 내용.
  - 결품 보고에 취소(담을 상품 없음, D-14)를 더함 (검토 문서 ②).
  - 마감: central ↔ 웹 "보류 여부 + 주문판 목록" 보내기와 반영 답(hold · release). 주문판에 무엇을 실을지 웹이 정한다. central 재시작 때 웹에 먼저 묻기. 나중에: central이 보는 진행 중 운반 건 수 · 목록 (자동 대조).
  - 락 해제 · 잠금(PDE-08 · PHD-10)과 팔 고장 복구 명령의 이름 · 응답.
  - 명령 경로 (11-19 사용자 결정): 업무 명령(담기 지시 · 정지 · 이어서 지시)은 central → 로봇, 관리 명령(락 해제 · 잠금, 운행 복귀 확인 · 다시 투입, 강제 입력, 운영 모드 알림)은 service_bridge → fleet → 로봇. 관리 명령은 fleet이 먼저 판단하고 로봇 노드가 한 번 더 거절할 수 있다. 거절은 `admin_action.result = REJECTED`와 `result_detail`로 남는다. central은 락 상태를 `/fleet/robots`로 안다. 요청은 ROS 서비스(요청 · 응답), 상태 알림은 토픽(발행 · 구독)으로 한다. 예: Phody 장애 처리 완료는 브리지 → fleet → Phody 서비스이고, 팔 복귀는 `/{arm}/state` → `/fleet/robots` 토픽으로 central이 구독해 재시작을 받을 수 있는지 안다.
  - `basket_capacity` 설정을 웹과 관제가 같게 쓰는 방법.
  - 시험 모드: 구성 요소(웹 · 브리지 · central · fleet · 로봇 노드)가 켤 때 모드를 받고, 브리지가 연결할 때 서로 모드를 비교해 다르면 거부. 로봇 노드에 모드 알림(서비스가 켜질 때 · 로봇이 다시 연결될 때 · 보고한 모드가 다를 때)과 상태 보고의 모드 · 강제 중 항목(`forced`), 다시 알려도 모드가 다른 로봇은 배정에서 뺌. 로봇 노드의 강제 입력 명령(조건 바꾸기 · 상태 바꾸기 · 강제 해제 · 충전 끝내기; 정상 운영이면 무시, 받으면 실제로 동작하고 보고, 충전 끝내기는 실제 배터리가 기준 미만이면 거절).
- **다이어그램 수정 요청**: R-15(주문 나눔) · R-16(선반에 되돌림) · R-17(마감 중 나뉜 주문 · 재확인 · 수시 조정) · R-18(결품 두 가지: 선반만 빔은 창고에서 갖다 놓음, DB 재고 부족은 추가 입고) · R-19(마감 뒤 거절은 취소됨) · R-20(시험 모드). `design/다이어그램_수정_요청.md`에 적었다.
- **fleet-v1 맞춤 전 임시 규칙**: 검토 문서 4장. 관제가 고쳐지면 지운다.
- **차이 사유 목록 값** (4.11): 팀 확인 필요.
- 입고 사유 "보충"은 "추가 입고"(`ADDITIONAL`, 비정기 입고)로 바꿨다 (11-19 사용자 결정). "보충"은 결품 때 창고에서 작업대로 갖다 놓는 일(기록 없음)에만 쓴다. 관리자 스토리보드 6번 탭 장면 2(입고 창) · 2번 탭 장면 3(추가 입고 등록 후 담기 재개)에 반영했다.
- **D-06 (중요 알림 상태 머신, 보류)**: alert.status를 OPEN · RESOLVED 두 값으로 시작한다. 확인(ACK)이 생기면 CHECK에 값을 더한다.
- **D-14 (결품으로 담을 상품 없음 → 취소, 논의 중)**: reason `NOTHING_TO_FILL`로 자리만 둔다.
- **R-07 (Phony 장애 때 보류)**: 결정되면 4.7을 다시 본다.
- **마감 주기 · 운영 종료와 엮을지 (OMS-18 비고, 미결)**: 시각으로만 판단하므로 하루 한 번도, 여러 번도 담긴다.
- **pc/central/README.md**: "주문·재고·화면 백엔드" 문구를 A안에 맞게 고쳐야 한다 (DB 없음, 주문 처리).
- 해결된 것 (11-20, 통신 규칙 검토 3장): 진행 보고 번호 · 재시작 구분(#18, `trip.report_epoch` · `report_n`, `bridge_session`), 관리 명령 결과(#25, `admin_action.request_id` · `final_result`), 놓치면 안 되는 알림(`alert.event_id`), 화면 주문 중복(#34, `orders.idem_key`), 화면 사건 순번(`ws_event`), 사용자 취소를 쓰는 곳(#22), 사유 코드 이름(#37). D-14는 `NOTHING_TO_FILL`로 정해짐(#20).
- 해결된 것 (11-19): `SubmitOrder` · `set_run_mode`가 관제 계약에서 빠짐(관제는 central의 주문 번호를 그대로 씀), `docs/관제_동작.md`는 fleet-v1에서 지워져 고치지 않음, HELD의 뜻(검토 문서 5장), 마감 결정 D-1 ~ D-4, 주문 나눔 D-5 · D-6.

## 7. 설계자 판단

- `kind`를 PK로 쓰지 않고 `product_id`를 따로 둔다. 상품 코드가 바뀌어도 이력 FK가 깨지지 않게.
- `order_id` · `trip_id`는 text. ROS 메시지가 문자열이고, 로그 · 화면 · 관제 이벤트 detail에서 같은 값으로 찾을 수 있게. `trip_id`는 주문 번호에 순번을 붙여 사람이 봐도 어느 주문인지 안다.
- `orders.status`를 계산 뷰로 두지 않고 저장한다. 사용자 화면 조회 · `NOTIFY` · 조건부 UPDATE가 단순해진다. 운반 건 상태를 바꾸는 같은 트랜잭션에서 다시 계산하므로 어긋나지 않는다.
- 주문 요약 규칙에서 보류를 가장 앞에 둔다. 사용자에게 "준비가 멈췄다"를 먼저 알리는 것이 맞고, 관리자 조치가 필요한 상태이기 때문이다.
- 이력 테이블은 하나로 두고 `trip_id`가 NULL이면 주문 요약 변화로 읽는다. 사용자 화면은 NULL 줄만, 관리자 화면은 모두 본다.
- 로봇 테이블을 두지 않는다. 로봇 목록의 원본은 `config/robots.yaml`이고, 상태는 InfluxDB · `/fleet/robots`에 있다. `robot_id`는 문자열로만 적는다.
- 팔 고장 복구는 `ARM_RECOVER`로 따로 둔다. fleet-v1의 명령 이름이 정해지면 맞춘다.
- 재고 부족 기준은 상품별 칼럼으로 두고 "일괄 적용"은 전체 UPDATE로 한다.
- 상태 · 사유 · 동작 값은 PostgreSQL ENUM 대신 `text + CHECK`. 값을 더할 때 마이그레이션이 쉽다.
- 장애 기록(OMS-14)은 따로 테이블을 두지 않고 `status_history`(주문 쪽)와 `alert`(로봇 쪽)로 남긴다. 장애의 시계열 상세는 InfluxDB.
- 재고 초과 거절은 거절된 주문을 `orders`에 넣지 않고 알림 횟수로만 남긴다.
- 마감을 취소해도 실사 행을 남긴다 (토의자 A는 버리자고 함). 원장이 취소된 마감의 행을 가리킬 수 없으므로 재고에 섞일 길이 없다.
- (11-20) 통신 규칙 #21 · #37의 `trip.submitted_at`은 새 칸을 만들지 않고 `trip.created_at`(운반 건을 만든 접수 시각, 되돌려도 바뀌지 않음)으로 쓴다. contract에서는 `submitted_at`이라는 이름으로 내보낸다.
- (11-20) `admin_action.final_result` 값은 결정에 있는 `UNCONFIRMED` · `TIMEOUT`에 접수 응답과 같은 말 `ACCEPTED` · `REJECTED`를 더한 4개로 둔다. contract 표가 정해지면 맞춘다.
- (11-20) `VISION_MISS` → `RECOGNITION_ERROR`는 이력 사유와 실사 차이 사유(4.11) 둘 다 바꾼다. 거절 사유(`admin_action.result_detail`)는 CHECK 없이 contract 이름을 그대로 적는다.
- 마감 중 입고를 실사 전까지 받기로 하면서 반출은 마감 시작부터 그대로 막는다. 반출은 기다리는 동안 해야 할 이유가 없고, 막는 범위를 줄이는 것만 사용자가 정했다.

## 8. 파일 · 버전

- `design/db/order_db.dbml`: 원본. 테이블 · 칼럼 노트에 기능 ID를 적는다.
- `design/db/order_db.sql`: `dbml2sql --postgres`로 만든 DDL. 손으로 고치지 않는다.
- `design/db/order_db_extra.sql`: DBML로 표현하지 못하는 것만 손으로 쓴다 — 부분 UNIQUE 인덱스(`WHERE`), 여러 칼럼 CHECK(4.3 CHECK, `qty_placed + qty_missing <= qty_ordered`, `stock_close` 상태 · 시각 등), 생성 칼럼(`diff_qty`), 원장 트리거(잠금 · 마감 중 막기 · 바로잡기 확인 · 재고 반영) · UPDATE/DELETE 금지 트리거, 마감 확정 검사 트리거, `v_in_flight` 뷰, `ws_event` 넣기 · `NOTIFY` 트리거. 적용 순서는 `order_db.sql` → `order_db_extra.sql`. 같은 규칙을 DBML 테이블 노트에도 적어 ERD 그림에 보이게 한다 ※
- 검증: 빈 PostgreSQL에 두 파일을 차례로 적용해 오류가 없는지 확인하고, 마감 중 입고 거절 · 바로잡기 · 확정 검사 · 재확인을 SQL로 시험한다.
- 제출 PNG: `design/exports/`에 제출 이름 규칙으로.
- 버전 v1.0부터 `design/README.md` 현재 문서 표 · CHANGELOG에 함께 기록 (기존 관리 규칙). DBML 맨 위 `Project` 노트에도 버전을 적는다.

## 9. 만드는 사람에게 맡기는 것

- 인덱스(FK · 조회용) 세부, 칼럼 순서, ERD 배치 · 색
- `updated_at` 갱신을 트리거로 할지 앱에서 할지
- `order_id` · `trip_id` 번호 규칙의 세부 형식
- 주문을 운반 건으로 나누는 순서 (상품을 어떤 순서로 바구니에 채울지)
- 주문 요약 상태 계산을 트리거로 할지 앱 함수로 할지 (같은 트랜잭션이면 어느 쪽이든)
- DBML 노트 문구
- 시험 DB 만들기 · 지우기 스크립트, 시나리오 주문의 모양(파일 형식), 시험 DB 기록을 지우기 전에 보관할지
