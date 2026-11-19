# 사용자(고객) 주문 페이지 스토리보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 스토리보드 생성 스크립트를 저장소로 옮겨 공통 부분을 나누고, 같은 도구로 고객 주문 페이지 스토리보드(탭 6장)를 만든다.

**Architecture:** `design/storyboard_tools/`에 공통 모듈(`sb_core.py`: 스타일 · 장면 칸 · 탭 배치 · draw.io 쓰기)과 화면별 모듈(관리자 `admin_*`, 고객 `user_*`)을 둔다. 각 스토리보드는 "화면 그리기 함수 + 장면 명세 + 검사기" 세 파일이다. 생성된 `.drawio`가 원본이고 검사기가 규칙을 지킨다.

**Tech Stack:** Python 3 표준 라이브러리(`xml.etree.ElementTree`, `unittest`), 확인용 PIL 근사 렌더러.

**Spec:** `docs/superpowers/specs/2026-11-19-user-storyboard-design.md`

## Global Constraints

- 산출물: `design/diagrams/사용자_스토리보드.drawio`, `<mxfile host="app.diagrams.net" pages="6">`, XML 선언 없음.
- 탭 이름(정확히): `0. 화면 구성`, `1. 평상시 주문`, `2. 재고 초과 거절`, `3. 주문 취소`, `4. 결품`, `5. 마감 뒤 거절`.
- 버전 라벨 셀 id `version-label`, 값 `v1.0 · 2026-11-19`.
- 고객 화면에 쓰는 상태 말(이것만): `접수됨` `준비 중` `출고 중` `출고 완료` `일부 출고` `취소됨` `거절됨`.
- [취소]는 `접수됨` 줄에만.
- 고객 화면(와이어프레임 안) 금지어: `장애` `보류` `일시 정지` `일시 중지` `마감` `결품` `배정` `담는 중` `운반 중` `Pode` `Phody` `Phony` 와 영문 상태 이름(`RECEIVED` `ASSIGNED` `FILLING` `HELD` `SHIPPED` `PARTIAL` `CANCELLED`). 설명 줄 · 근거 줄 · 노트는 예외.
- 위쪽 줄: `IFP 주문` · 오른쪽 `고객1 [로그아웃]`. 알림이 있으면 그 아래 노란 한 줄 + `[확인]`.
- 장면 설명 세 줄: `고객: …` / `시스템: …` / `근거: …`.
- 관리자 스토리보드 출력은 옮기기 전과 바이트 단위로 같아야 한다.
- 커밋은 사용자가 요청할 때만.

## Review Focus

1. **관리자 스토리보드 회귀**: 공통 모듈로 나누다 관리자 출력이 바뀌면 이미 제출한 v1.2와 어긋난다 → Task 1 동일성 비교 + `admin_check.py` 통과.
2. **[취소]가 접수됨 아닌 줄에 붙음**: 화면 함수가 상태와 무관하게 버튼을 그리면 OMS-10과 어긋난다 → Task 2 `test_cancel_only_on_received`.
3. **내부 말 노출**: 알림 문구나 주문 줄에 "결품" "마감" 등이 새어 나옴 → Task 2 `test_forbidden_word_in_screen`.
4. **대응표에 없는 상태 말**: "배송 중"처럼 새 말이 장면에 생김 → Task 2 `test_unknown_status`.
5. **알림 줄이 화면을 밀어 겹침**: 알림 줄이 있는 장면에서 주문 목록이 화면 밖으로 넘침 → Task 2 `test_cells_inside_screen`.

---

### Task 1: 생성 스크립트 옮기기 · 공통 부분 나누기

**Files:**
- Create: `design/storyboard_tools/sb_core.py`, `admin_screen.py`, `admin_scenes.py`, `admin_build.py`, `admin_check.py`, `render.py`, `README.md`
- Source: 세션 임시 폴더 `scratchpad/sb/{build,scenes,check}.py`, `scratchpad/render.py`

**Interfaces:**
- Produces (`sb_core.py`):
  - 스타일 상수 `S_*`, 크기 `CARD_W SCR_W SCR_H BAR_H GAP_X GAP_Y`
  - `text_len(s) -> float`, `lines_needed(text, width, px=11.5) -> int`
  - `class Tab` (`v`, `e`, `nid`, `finish`), `buttons(t, x, y, labels, right)` (지금 `_buttons`)
  - `login_screen(t, x, y, body)`
  - `build_tab(spec, ver, screen_fn) -> Element`: `screen_fn(t, x, y, screen_dict)`로 화면을 그림
  - `write(path, tabs)`
- Produces (`admin_screen.py`): `screen(t, x, y, s: dict)` — 지금 `topbar` + `screen` 본문
- `admin_build.py`: `python3 admin_build.py [출력 경로]`, 기본 `design/diagrams/관리자_스토리보드.drawio`(저장소 루트 기준 상대 경로)

- [ ] **Step 1: 기준 출력 만들기** — 옮기기 전 스크립트로 `scratchpad/ref_admin.drawio` 생성
- [ ] **Step 2: 옮기고 나누기** — 동작은 바꾸지 않는다. 절대 경로는 `__file__` 기준 상대 경로로
- [ ] **Step 3: 동일성 확인**

Run: `python3 design/storyboard_tools/admin_build.py /tmp/…/new_admin.drawio && cmp scratchpad/ref_admin.drawio /tmp/…/new_admin.drawio && python3 design/storyboard_tools/admin_check.py /tmp/…/new_admin.drawio`
Expected: cmp 출력 없음, 검사기 `OK`

- [ ] **Step 4: README** — 파일 역할, 만들기 · 검사 · 미리보기 명령, "draw.io에서 손으로 고친 뒤 다시 만들면 덮어씀" 경고

### Task 2: 고객 화면 그리기 · 검사기

**Files:**
- Create: `design/storyboard_tools/user_screen.py`, `user_check.py`, `tests/test_user_check.py`

**Interfaces:**
- Consumes: Task 1 `sb_core`
- Produces (`user_screen.py`): `screen(t, x, y, s: dict)`. `s['kind']`는 `login` | `shop`. `shop`의 `s`:
  - `notice`: 위쪽 알림 줄 글 또는 없음, `mark`: `'notice'`면 [확인]에 ①
  - `body` 항목: `('search', 글)`, `('product', 이름, 재고, 수량, 표시)` (표시 `''` | `'①'` | `'soldout'` — 품절이면 수량 버튼 꺼짐), `('order_btn', 표시)`, `('inline', 글)`, `('order', 번호, 상태, 표시)` (상태가 `접수됨`일 때만 [취소]를 그림, 표시 `'①'`이면 [취소]에 ①), `('dialog', 글, 버튼들)`
  - 주문 줄 셀 id는 `…_ord…`, [취소] 셀 id는 `…_cancel…`
- `user_check.py`: `main(path) -> int`(실패 수), 실패마다 한 줄 출력, 0이면 `OK`

- [ ] **Step 1: 검사기 시험 먼저** — `tests/test_user_check.py`에 작은 탭 하나를 `sb_core`로 직접 만들어 검사하는 시험:
  - `test_cancel_only_on_received`: `준비 중` 줄 옆에 `_cancel` 셀 → 실패 1 이상
  - `test_forbidden_word_in_screen`: 화면 안 셀에 `결품` → 실패
  - `test_unknown_status`: 주문 줄 상태 `배송 중` → 실패
  - `test_cells_inside_screen`: 화면 밖으로 나간 셀 → 실패
  - `test_clean_tab_passes`: 규칙을 지킨 탭 → 위 네 규칙 실패 0
- [ ] **Step 2: 실패 확인** — Run: `python3 -m unittest discover -s design/storyboard_tools/tests` / Expected: import 실패 또는 FAIL
- [ ] **Step 3: `user_check.py` 구현** — 위 규칙 + 탭 이름 · 버전 라벨 · 설명 세 줄 머리말 · 기능 ID(SCR-01 · 02 · 03 · 04 · 05 · 09, OMS-03 · 10 · 13, CEN-13)가 어느 근거 줄엔가 있음 · 탭별 필수 글(3번 탭 `이미 준비가 시작되어 취소할 수 없습니다`, 0번 탭 상태 말 7개 모두)
- [ ] **Step 4: `user_screen.py` 구현** — 화면 520×250 안에 왼쪽 상품(폭 250) · 오른쪽 내 주문, 알림 줄이 있으면 본문을 20px 내림
- [ ] **Step 5: 시험 통과** — Expected: 5개 OK

### Task 3: 고객 장면 · 생성

**Files:**
- Create: `design/storyboard_tools/user_scenes.py`, `user_build.py`, `design/diagrams/사용자_스토리보드.drawio`

**Interfaces:**
- Consumes: Task 1 `build_tab`, Task 2 `screen`
- Produces: `TABS`(dict 6개, 관리자 장면 명세와 같은 형식: `id name title subtitle scenes notes`, 장면 `n title pos screen lines next`)

- [ ] **Step 1: 장면 쓰기** — 설계 문서 4절 탭 구성대로. 0번 탭은 화면 배치 장면 + 상태 대응표 노트 + 범례 + 로그인 장면. 5번 탭에 R-08 미결 노트(주황 점선)
- [ ] **Step 2: 만들고 검사** — Run: `python3 design/storyboard_tools/user_build.py && python3 design/storyboard_tools/user_check.py design/diagrams/사용자_스토리보드.drawio` / Expected: `OK`
- [ ] **Step 3: 미리보기로 겹침 확인** — `render.py`로 탭별 PNG(임시 폴더)를 만들어 글자 겹침 · 화살표 라벨 겹침을 눈으로 확인하고 고친다

### Task 4: 검수 · 기록

- [ ] **Step 1: 검수자 1명** (고객 사용성 + 다이어그램 v2.4 · 기능 목록 정합). Critical · Important는 검사기 규칙을 먼저 더해(RED) 고친다(GREEN)
- [ ] **Step 2: 기록** — `design/README.md` 문서 표에 `사용자 주문 페이지 스토리보드 | v1.0 | 2026-11-19`, 디렉터리 줄에 `storyboard_tools/`; `design/CHANGELOG.md` 2026-11-19에 항목; 설계 문서 상태 "승인됨"
- [ ] **Step 3: 마지막 확인** — 두 검사기 · 단위 시험 · 관리자 동일성 다시 실행
- [ ] **Step 4: 커밋 (사용자 요청 시)**
