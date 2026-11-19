# 주문 관리 DB 설계 (ERD)

- 작성일: 2026-11-18 · 고침: 2026-11-19 (기능 목록 11-19 변경 · fleet-v1 검토 · 마감 토의 결정 · 주문 나눔 · 시험 모드 반영)
- 상태: 검토 중
- 산출물: `design/db/order_db.dbml` (원본), 여기서 만드는 ERD PNG(제출용)와 PostgreSQL DDL
- 관련 문서: `docs/fleet-v1_HELD_검토.md`(5장 논의 결과), `2026-11-19-close-db-discussion.md`(마감 토의 기록), `design/다이어그램_수정_요청.md`(R-15 ~ R-20)

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
- 시험 모드의 가상 장애 · 상태 바꾸기 명령과 모드 맞추기 인터페이스 (인터페이스 작업, 6절). DB는 모드마다 따로 두는 것만 정한다.
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

`※` 표시는 설계자 판단(7절)이다. 자료형은 PostgreSQL 기준. 테이블은 13개다.

### 4.1 account — 계정 · 역할 (OMS-01, SCR-01)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| account_id | bigint identity | PK |
| login_id | text | UNIQUE, NOT NULL |
| password_hash | text | NOT NULL (평문 저장 금지) |
| role | text | NOT NULL, CHECK IN ('USER', 'ADMIN', 'DEV') — `DEV`는 개발자 (SCR-17). 정상 운영 서버는 `DEV` 로그인을 거절한다 |
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
| HELD | 주문 처리 보류 | Pode 장애(운반 중) · Phody 장애. 사유는 이력에 |
| CANCELLED | 취소됨 | 끝. 사용자 취소, 담을 상품 없음(D-14), 마감 뒤 재고 다시 확인 거절 |

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
| created_at | timestamptz | NOT NULL DEFAULT now() |
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
| reason | text | NULL 허용. CHECK IN ('OUT_OF_STOCK' 결품, 'PICK_FAILED' 집기 실패, 'VISION_MISS' 인식 오류, 'PODE_FAULT', 'PHODY_FAULT', 'USER_CANCEL', 'NOTHING_TO_FILL' D-14, 'STOCK_RECHECK' 마감 뒤 재고 다시 확인 거절, 'FORCED_SHIP' 강제 출고 처리, 'FORCED_PARTIAL' 일부 출고 강제 처리, 'REASSIGN' 배정됨 → 접수됨 되돌림(CEN-08, 빈 바구니 Pode 장애), 'RETURNED_TO_SHELF' 보류 → 접수됨 선반에 되돌림(D-3, R-16)) ※ |
| robot_id | text | 관련 로봇 |
| actor | text | NOT NULL, CHECK IN ('USER', 'ADMIN', 'SYSTEM') |
| account_id | bigint | FK → account, NULL 허용 (USER · ADMIN일 때) |
| detail | jsonb | 결품 상품 · 수량, 관제 보고 원문(status · detail), 되돌린 상품 · 수량 등 |
| created_at | timestamptz | NOT NULL DEFAULT now() |

다이어그램 범례 "저장 값은 상태마다 하나, 사유는 detail"을 따른다: 상태는 `HELD` 하나, Pode 장애인지 Phody 장애인지는 `reason`으로 가른다.

- HELD는 Pode 장애(운반 중) · Phody 장애에만 쓴다 (v2.4, CEN-06 비고). 결품으로 하나도 못 담은 운반 건은 HELD가 아니라 CANCELLED (`NOTHING_TO_FILL`, D-14).
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

### 4.8 alert — 관리자 알림 (OMS-03 · 09 · 12 · 14 · 16, SCR-08 · 10)

| 칼럼 | 자료형 | 제약 · 설명 |
|---|---|---|
| alert_id | bigint identity | PK |
| level | text | NOT NULL, CHECK IN ('INFO', 'WARN', 'ERROR') (`FleetEvent.level`) |
| code | text | NOT NULL. `FleetEvent.code` 또는 웹 서비스 코드 (`OUT_OF_STOCK`, `LOW_STOCK`, `NEGATIVE_STOCK`, `LATE_REPORT` 등) |
| source | text | NOT NULL (`central`, `fleet`, `order_service` 등) |
| robot_id | text | NULL 허용 |
| order_id | text | FK → orders, NULL 허용 |
| trip_id | text | FK → trip, NULL 허용 |
| product_id | bigint | FK → product, NULL 허용 (재고 부족) |
| message | text | NOT NULL, 화면 문구 |
| detail | jsonb | 원본 이벤트. `LOW_STOCK`이면 재고 초과로 거절한 횟수 · 마지막 거절 시각도 여기에 |
| status | text | NOT NULL DEFAULT 'OPEN', CHECK IN ('OPEN', 'RESOLVED') ※ |
| created_at | timestamptz | NOT NULL DEFAULT now() |
| resolved_at | timestamptz | |
| resolved_by | bigint | FK → account. 자동 해결이면 NULL |

- 부분 UNIQUE 인덱스: `(code, product_id) WHERE status = 'OPEN' AND code = 'LOW_STOCK'` — 같은 상품의 재고 부족 알림이 쌓이지 않게 한다.
- 재고 초과 주문 거절(OMS-03, R-01)과 마감 뒤 재확인 거절도 이 `LOW_STOCK` 알림 하나로 합친다. 열린 알림이 있으면 `detail`의 거절 횟수만 올리고, 없으면 새로 연다 ※.
- `LOW_STOCK`은 재고가 바뀐 모든 기록(입고 · 반출 · 출고 차감 · 조정) 뒤에 다시 판단한다: 기준 아래면 열고, 기준 이상으로 오르면 자동 해결 (관리자 스토리보드 6번 탭 장면 9).
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
| result | text | NOT NULL, CHECK IN ('SENT', 'ACCEPTED', 'REJECTED', 'LOCAL') — ROS로 보낸 뒤 응답. ROS로 보내지 않는 입력은 `LOCAL` |
| result_detail | text | 거절 사유 |
| created_at | timestamptz | NOT NULL DEFAULT now() |

action 값:

| 묶음 | 값 |
|---|---|
| 주문 조치 | 'RESUME_FILL' 재개, 'MARK_MISSING' 보충 불가 · 못 담음, 'FORCE_SHIP' 강제 출고 처리, 'FORCE_PARTIAL' 일부 출고 강제 처리, 'RETURN_TO_SHELF' 선반에 되돌림 (D-3) |
| 로봇 조치 | 'CONFIRM_CLEAR' 운행 복귀 확인(치움), 'REINSTATE' 다시 투입 (`/fleet/reinstate`), 'FAULT_RESOLVED' 장애 처리 완료, 'RESUME_ARM' 팔 일시 정지 재개, 'ARM_RECOVER' 팔 고장 복구 ※, 'LOCK_RELEASE' 락 해제, 'LOCK_ENGAGE' 락 잠금 (PDE-08 · PHD-10) |
| 서비스 | 'SERVICE_PAUSE', 'SERVICE_RESUME', 'ESTOP', 'ESTOP_RELEASE', 'SETTING_CHANGE' 설정 변경 (4.13) |
| 마감 | 'CLOSE_START', 'CLOSE_CONFIRM', 'CLOSE_CANCEL' (OMS-18 · CEN-13) |
| 시험 (시험 DB에만 생김) | 'MOCK_SCENARIO' 시나리오 주문 실행, 'MOCK_ORDER' 개발자 직접 주문, 'FORCE_STATE' 상태 바꾸기, 'FAULT_INJECT' 가상 장애, 'FAULT_CLEAR' 가상 해제 (SCR-17 · OMS-21) |

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
| reason | text | 차이 사유, 고르는 목록. CHECK IN ('COUNT_DIFF' 재고 조사 차이, 'VISION_MISS' 인식 오류, 'DAMAGE_FOUND' 파손 발견, 'LOST' 분실, 'INPUT_ERROR' 입력 오류, 'TEST_RUN' 시험 영향, 'OTHER' 기타) — `TEST_RUN`은 운영 재개 마감에서만. 나머지 값은 팀 확인 필요 |
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

- `basket_capacity`의 처음 값은 4 (fleet-v1 `basket_capacity`와 같게).
- 바꾸면 `admin_action` `SETTING_CHANGE`에 이전 · 새 값을 남긴다. 마감 중에도 바꿀 수 있다 (재고와 관계없음).
- 관제(fleet-v1)도 `basket_capacity`를 갖고 있으므로 두 값을 맞추는 방법은 인터페이스 작업으로 넘긴다 (6절).

### 관계 요약

```
account 1 ─< orders 1 ─< order_item >─ 1 product 1 ─< stock_ledger >─ stock_count >─ stock_close
                │  1 ─< trip 1 ─< trip_item >─ product         │
                │          └──< stock_ledger (SHIP)             └─ trip
                │  1 ─< status_history >─ trip
                │  1 ─< alert >─ trip / product
account 1 ─< admin_action >─ orders / trip / alert / stock_close
app_setting (독립)
```

## 5. 동작 규칙 (DB에 걸린 쪽)

### 주문 · 운반 건

- **주문 등록 (OMS-03 · 04)**: 품목 합 > `max_order_items`이거나 상품마다 주문 수량 > `stock_qty`면 거절 (주문을 기록하지 않음). 재고 초과 거절은 `LOW_STOCK` 알림에 합친다. 통과하면 같은 트랜잭션에서 `orders` · `order_item`과 `basket_capacity` 이하로 나눈 `trip` · `trip_item`을 만든다. 예약하지 않는다(OMS-07 미포함). 마감 중에도 접수는 받는다.
- **결품 사전 확인 (6-2 #4, OMS-02)**: 상품마다 `가용 = stock_qty − (v_in_flight 운반 건의 qty_placed 합)`. 가용 < 남은 수량이면 결품. 결품이면 담기 일시 중지 → 관리자가 추가 입고를 등록하고 재개하거나 보충 불가(R-18). 재고는 창고 기준이므로 여기서 결품이면 창고에도 없다. **전제**: 출고 전에 `qty_placed`가 채워져 있어야 한다. fleet-v1은 출고 · 보류 때만 `placed`를 보내므로 이 신호가 없으면 앞선 운반 건 수량을 모른다 (6절).
- **사용자 취소 (OMS-10)**: 그 주문의 모든 운반 건이 `RECEIVED`일 때만. 조건부 UPDATE로 central 배정과 경합해도 하나만 이긴다.
- **재고 차감 (6-1-3 #33, OMS-06)**: 운반 건이 출고됨 · 일부 출고됨이 될 때 상품마다 `qty_placed`만큼 `SHIP` 한 줄 (`trip_id`). 강제 출고 처리 · 일부 출고 강제 처리도 같다. 취소됨 · 선반에 되돌림은 차감 없음.
- **출고 차감이 재고 0 아래로 가면**: 차감만 따로 시도(SAVEPOINT)하고, 실패하면 출고됨 기록은 남기고 `NEGATIVE_STOCK` 알림을 연다. 관리자가 수시 조정 등으로 맞춘 뒤 차감한다.
- **늦게 온 출고 보고**: 이미 끝난 운반 건에 같은 결과가 다시 오면(관제 재전송) 무시한다. 결과가 다르면 덮어쓰지 않고 `LATE_REPORT` 알림 → 관리자 확인 뒤 출고 차감 (`account_id` 채움).
- **주문 요약 상태**: 운반 건 상태를 바꾸는 트랜잭션에서 4.4 규칙으로 `orders.status`를 다시 계산하고, 바뀌면 이력에 `trip_id` 없는 한 줄.

### 재고 기록

- **재고는 원장 트리거 한 길로만 바뀐다 (OMS-08)**: `stock_ledger` BEFORE INSERT 트리거 하나가 차례로:
  1. 공유 잠금(`pg_advisory_xact_lock_shared`)을 잡는다.
  2. 진행 중 마감이 있으면 `OUTBOUND` · `SPOT_ADJUST`를 거절한다. `INBOUND`는 실사를 연 뒤(`counting_started_at` 있음)부터 거절한다. `SHIP` · `CLOSE_ADJUST`는 받는다. 입력 오류 바로잡기도 그 종류(입고 · 반출)의 규칙을 따른다.
  3. 입력 오류 바로잡기면: 원래 기록이 같은 상품, 입고 또는 반출, 부호 반대, 같은 수량인지 확인한다. 원래 기록이 마지막 확정 마감의 `finished_at`보다 앞서면 거절한다 ("마감 이전 기록은 고칠 수 없음", 날짜가 아니라 시각).
  4. `product.stock_qty`를 바꾸고 `qty_after`를 채운다.
- 원장은 UPDATE · DELETE 금지 트리거로 막는다.
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
| 상태 바꾸기 · 가상 장애 | 없음 (메뉴 · API 없음) | 개발자(`DEV`)만 |
| 마감 | 관리자가 원할 때 (`MANUAL`) | 시험 DB에서는 하지 않음 |

1. **진입** (정상 운영 → 시험 모드): 정상 운영에서 `TEST_ENTRY` 마감을 시작해 확정한다. 남은 운반 건 0까지 기다리는 것이 그대로 안전한 종료 조건이다. 보통 마감과 달리 확정 뒤 대기 주문 재확인 · 배정 재개를 하지 않고 서비스를 내린다. 접수됨 주문은 운영 DB에 그대로 남는다.
2. **시험 DB 준비**: 시험 DB를 새로 만들고, 상품과 시험 계정을 넣은 뒤 마지막 `TEST_ENTRY` 마감의 실사 수량을 입고(사유 기타, 설명 "시험 시작 재고")로 넣는다. 그래서 시험 중 결품 사전 확인이 실제 선반과 맞는다.
3. **시험**: 시나리오 · 직접 주문, 상태 바꾸기 · 가상 장애. 가상 장애는 로봇 노드가 명령을 받아 실제로 멈추고 장애를 보고하는 방식이어야 한다 (PC가 상태만 덮어쓰면 현장과 화면이 달라짐). 입력은 시험 DB의 `admin_action`에 남는다.
4. **해제** (시험 모드 → 정상 운영): 시험 DB의 진행 중 운반 건이 0이어야 끈다 (시험 DB 실사는 하지 않음). 정상 운영은 `OPS_RESUME` 마감이 진행 중인 상태로 켜진다 (배정 보류). 실사로 시험 동안 현장에서 움직인 상품을 맞추고, 시험 때문에 생긴 차이는 사유 "시험 영향"으로 남긴다. 확정하면 접수됨 주문을 재확인하고 배정을 시작한다.
- 시험 DB는 다음 진입 때 지우고 새로 만든다. 시험 기록을 남길지는 만드는 사람에게 맡긴다 (9절).

### 수시 조정 (OMS-18, 관리자 스토리보드 6번 탭 장면 7)

- `close_id` 없는 `stock_count` 한 줄과 `SPOT_ADJUST`. 창고(작업대 선반 포함) 전체를 센다.
- **막는 경우 (D-4 가)**: 진행 중 마감이 있을 때, 그리고 그 상품을 담는 `v_in_flight` 운반 건 중 `placed_at`이 NULL인 것이 있을 때 (바구니에 몇 개 있는지 모름). 담기 일시 중지 때 담은 수를 알려 주는 인터페이스가 생기면 이 막음을 푼다.
- 받는 경우: `book_qty` = 그 순간의 `stock_qty`, `in_transit_qty` = 그 상품을 담은 진행 중 운반 건의 `qty_placed` 합. 화면은 "재고 2 (바구니 1) · 창고에 있어야 할 수 1".

### 화면 전달 (OMS-05)

주문 · 운반 건 상태, 알림, 마감 상태가 바뀌면 `NOTIFY`로 웹 서비스에 알리고 웹 서비스가 WS로 보낸다.

## 6. 미결 · 다른 문서에 영향

- **인터페이스 작업에 넘길 것** (별도 작업):
  - 웹 → central 주문 전달(BRG-01): central에 넘기는 주문 번호는 `trip_id`. contract 0.5에 정의가 없으니 "번호는 웹 서비스가 만들어 넘긴다"를 전제로 넘긴다.
  - 진행 상황 보고: 배정됨 신호, 담기 일시 중지 상태와 그때까지 담은 수, 담는 중 → 운반 중 때의 `placed` · `missing`. 결품 사전 확인(5절)과 수시 조정 막음 풀기(D-4)가 여기에 달려 있다. 다이어그램 R-14와 같은 내용.
  - 결품 보고에 취소(담을 상품 없음, D-14)를 더함 (검토 문서 ②).
  - 마감: central ↔ 웹 "보류 여부 + 주문판 목록" 보내기와 반영 답(hold · release). 주문판에 무엇을 실을지 웹이 정한다. central 재시작 때 웹에 먼저 묻기. 나중에: central이 보는 진행 중 운반 건 수 · 목록 (자동 대조).
  - 락 해제 · 잠금(PDE-08 · PHD-10)과 팔 고장 복구 명령의 이름 · 응답.
  - `basket_capacity` 설정을 웹과 관제가 같게 쓰는 방법.
  - 시험 모드: 구성 요소(웹 · 브리지 · central · fleet · 로봇 노드)가 켤 때 모드를 받고, 브리지가 연결할 때 서로 모드를 비교해 다르면 거부. 로봇 노드의 가상 장애 · 상태 바꾸기 명령 (정상 운영이면 거절, 받으면 실제로 멈추고 보고).
- **다이어그램 수정 요청**: R-15(주문 나눔) · R-16(선반에 되돌림) · R-17(마감 중 나뉜 주문 · 재확인 · 수시 조정) · R-18(결품 두 가지: 선반만 빔은 창고에서 갖다 놓음, DB 재고 부족은 추가 입고) · R-19(마감 뒤 거절은 취소됨) · R-20(시험 모드). `design/다이어그램_수정_요청.md`에 적었다.
- **fleet-v1 맞춤 전 임시 규칙**: 검토 문서 4장. 관제가 고쳐지면 지운다.
- **차이 사유 목록 값** (4.11): 팀 확인 필요.
- 입고 사유 "보충"은 "추가 입고"(`ADDITIONAL`, 비정기 입고)로 바꿨다 (11-19 사용자 결정). "보충"은 결품 때 창고에서 작업대로 갖다 놓는 일(기록 없음)에만 쓴다. 관리자 스토리보드 6번 탭 장면 2(입고 창) · 2번 탭 장면 3(추가 입고 등록 후 담기 재개)에 반영했다.
- **D-06 (중요 알림 상태 머신, 보류)**: alert.status를 OPEN · RESOLVED 두 값으로 시작한다. 확인(ACK)이 생기면 CHECK에 값을 더한다.
- **D-14 (결품으로 담을 상품 없음 → 취소, 논의 중)**: reason `NOTHING_TO_FILL`로 자리만 둔다.
- **R-07 (Phony 장애 때 보류)**: 결정되면 4.7을 다시 본다.
- **마감 주기 · 운영 종료와 엮을지 (OMS-18 비고, 미결)**: 시각으로만 판단하므로 하루 한 번도, 여러 번도 담긴다.
- **pc/central/README.md**: "주문·재고·화면 백엔드" 문구를 A안에 맞게 고쳐야 한다 (DB 없음, 주문 처리).
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
- 마감 중 입고를 실사 전까지 받기로 하면서 반출은 마감 시작부터 그대로 막는다. 반출은 기다리는 동안 해야 할 이유가 없고, 막는 범위를 줄이는 것만 사용자가 정했다.

## 8. 파일 · 버전

- `design/db/order_db.dbml`: 원본. 테이블 · 칼럼 노트에 기능 ID를 적는다.
- `design/db/order_db.sql`: `dbml2sql --postgres`로 만든 DDL. 손으로 고치지 않는다.
- `design/db/order_db_extra.sql`: DBML로 표현하지 못하는 것만 손으로 쓴다 — 부분 UNIQUE 인덱스(`WHERE`), 여러 칼럼 CHECK(4.3 CHECK, `qty_placed + qty_missing <= qty_ordered`, `stock_close` 상태 · 시각 등), 생성 칼럼(`diff_qty`), 원장 트리거(잠금 · 마감 중 막기 · 바로잡기 확인 · 재고 반영) · UPDATE/DELETE 금지 트리거, 마감 확정 검사 트리거, `v_in_flight` 뷰, `NOTIFY` 트리거. 적용 순서는 `order_db.sql` → `order_db_extra.sql`. 같은 규칙을 DBML 테이블 노트에도 적어 ERD 그림에 보이게 한다 ※
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
