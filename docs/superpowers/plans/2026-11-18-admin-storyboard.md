# 관리자 페이지 스토리보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 웹 페이지의 화면 구성과 시나리오별 동작 흐름을 draw.io 와이어프레임 스토리보드(탭 9장)로 만든다.

**Architecture:** Python 생성 스크립트가 장면 명세(데이터)를 읽어 `.drawio` XML을 만든다. 공통 부품(위쪽 고정 줄, 현황 · 주문 관리 · 비상조치 화면 틀, 장면 칸, 갈래 화살표)은 함수 하나씩으로 두고, 탭마다 장면 목록만 다르게 준다. 생성된 `.drawio`가 원본이며, 이후 팀은 draw.io에서 직접 고친다(스크립트는 첫 판을 만드는 도구).

**Tech Stack:** Python 3 표준 라이브러리(`xml.etree.ElementTree`), draw.io(mxGraph XML), 확인용 PIL 렌더러(`scratchpad/render.py`, 근사 렌더).

**Spec:** `docs/superpowers/specs/2026-11-18-admin-storyboard-design.md`

## Global Constraints

- 산출물: `design/diagrams/관리자_스토리보드.drawio`, 파일 첫 줄 `<mxfile host="app.diagrams.net" pages="9">`, XML 선언 없음 (기존 drawio와 같은 형식).
- 탭 이름(정확히): `0. 화면 구성`, `1. 평상시 현황`, `2. 결품 · 담기 일시 중지`, `3. Pode 장애`, `4. Phody 장애`, `5. Phony 장애`, `6. 서비스 일시 정지 · 재시작`, `7. 재고 · 이력`, `8. 비상정지`.
- 각 탭 왼쪽 위 버전 라벨 셀 id `version-label`, 값 `v1.0 · 2026-11-18` (기존 다이어그램과 같은 스타일 `text;html=1;fontSize=12;fontColor=#666666;align=left;verticalAlign=middle;`, 위치 x=40, y=-26).
- 용어: 서비스 일시 정지(SCR-16) · 팔 일시 정지(SCR-12) · 담기 일시 중지(주문 FILLING_PAUSE). "일시 정지" 단독, "담기 보류", "취소하고 멈춤", "정지(STOP)"는 쓰지 않는다.
- 위쪽 고정 줄: `IFP 관리자` · 메뉴 `현황 | 주문 관리 | 재고 | 이력` · 작업 상태(`실행 중` 또는 `서비스 일시 정지`) · 작업 모드 버튼(`[서비스 일시 정지]` 또는 `[재시작]`) · 오른쪽 끝 크게 키운 빨간 `비상정지` 버튼(다른 버튼보다 너비 · 높이 1.5배 이상, fillColor=#e51400, fontColor=#ffffff).
- 장면 칸 = 제목(`장면 N · 제목`) + 화면 와이어프레임 + 설명 3줄(`관리자: …` / `시스템: …` / `근거: …`). 눌러야 할 곳에 ①② 번호.
- 갈래 화살표 라벨은 대괄호 조건(예: `[보충 가능]`), 시퀀스 alt 조건과 같은 말.
- 주문 조치 줄 버튼은 `[재개]` `[보충 불가 · 못 담음]` / `[강제 출고 처리]` / `[일부 출고 강제 처리]`. 로봇 조치(알림 칸) 버튼은 `[운행 복귀 확인]` `[장애 처리 완료]` `[재개]`(거리 정지). 재시작 · 재개(작업 모드)는 위쪽 고정 줄에만.
- 미결 표시 노트: 지도 "일정에 따라 (SCR-06)", 비상정지 탭 "제안 페이지 기준, 채택 전", D-06 · D-14 해당 장면. 노트 스타일은 기존 주황 점선 노트(`dashed=1;fillColor=#ffe6cc;strokeColor=#d79b00`).
- 커밋은 사용자가 요청할 때만 한다(저장소 규칙). 커밋 단계는 "사용자 승인 후"로 표시.

## Review Focus

1. **버튼-메시지 1:1 대응**: 관리자 입력 버튼마다 근거 줄에 시퀀스 메시지 번호가 있고, 같은 메시지를 두 곳에 그리지 않는다 → Task 1 검증기의 `check_button_refs`.
2. **용어 혼입**: 대상 없는 "일시 정지", "담기 보류", "취소하고 멈춤"이 섞이면 v2.4 결정과 어긋난다 → 검증기 `check_terms`.
3. **기능 누락**: 스펙 목표의 기능 ID(SCR-01 · 06 · 07 · 08 · 10 · 11 · 12 · 16 · 18 · 19, OMS-08 · 09 · 16, COM-02)가 어느 탭에도 없으면 실패 → 검증기 `check_coverage`.
4. **흐름 순서 오류**: 비상정지 해제 직후 바로 실행 중이 되는 장면, 배정됨 이후 사용자 취소 장면 → Task 4 검증 단계에서 탭 8 장면 순서(해제 → 서비스 일시 정지 유지 → 재시작)를 검사하고, 마지막 UML · 흐름 검수자가 시퀀스 v2.4와 대조.
5. **글자 넘침**: 장면 설명이 칸을 넘으면 PNG에서 잘린다(이번 v2.4에서 실제로 발생) → 검증기 `check_text_fit`(줄 수 × 15px + 여백 ≤ 칸 높이, 한 줄 최대 글자 수 기준).

---

### Task 1: 생성기 골격 · 검증기 · 탭 0 (화면 구성)

**Files:**
- Create: `scratchpad/sb/build.py` (생성기: 공통 부품 + 탭 조립 + 파일 쓰기)
- Create: `scratchpad/sb/scenes.py` (탭별 장면 명세 데이터)
- Create: `scratchpad/sb/check.py` (검증기)
- Create: `design/diagrams/관리자_스토리보드.drawio` (생성 결과)

(`scratchpad` = `/tmp/claude-1000/-home-ane-dev-ws/419f5941-999b-488e-8fc1-2d4e9adc4449/scratchpad`. 생성 도구는 저장소에 넣지 않는다.)

**Interfaces:**
- Produces (build.py):
  - `topbar(root, x, y, w, mode: str) -> None` — mode는 `"실행 중"` 또는 `"서비스 일시 정지"`. 모드 버튼은 실행 중이면 `[서비스 일시 정지]`, 아니면 `[재시작]`.
  - `screen(root, x, y, kind: str, mode: str, body: list[tuple]) -> (w, h)` — kind ∈ `{"login","dashboard","orders","stock","history","alerts","estop"}`. body는 `(종류, 텍스트, 강조)` 목록(종류: `"alert"`, `"order_row"`, `"button"`, `"text"`, `"robot_row"`, `"check"`).
  - `scene(root, n: int, title: str, x: int, y: int, screen_args: dict, admin: str, system: str, basis: str) -> str` — 장면 칸을 그리고 칸 셀 id(`sc{n}`) 반환.
  - `arrow(root, src_id: str, dst_id: str, label: str = "") -> None`
  - `note(root, x, y, w, text: str, pending: bool) -> None` — pending=True면 주황 점선.
  - `build_tab(tab_id: str, name: str, title: str, spec: dict) -> Element`, `write(path: str, tabs: list[Element]) -> None`
- Produces (scenes.py): `TABS: list[dict]` — 각 dict `{id, name, title, scenes: [{n, title, screen:{kind, mode, body}, admin, system, basis, next: [(n, label)]}], notes: [(text, pending)]}`.
- Produces (check.py): `main(path) -> int` (실패 수 반환, 0이면 통과) 와 검사 함수 `check_structure`, `check_terms`, `check_coverage`, `check_button_refs`, `check_text_fit`.

- [ ] **Step 1: 검증기 작성 (먼저 실패해야 함)**

`check.py`의 검사 항목(각각 실패 시 메시지 출력):
- `check_structure`: 탭 9개, 탭 이름이 Global Constraints 목록과 정확히 같음, 탭마다 `version-label` 값 `v1.0 · 2026-11-18`, id 중복 없음, edge의 source/target이 모두 존재.
- `check_terms`: 모든 셀 값(태그 제거)에서 정규식 `(?<!서비스 )(?<!팔 )일시 정지` 0건, `담기 보류` · `취소하고 멈춤` 0건, 정규식 `(?<!E)STOP` 0건 (비상정지 상태 `ESTOP`은 허용).
- `check_coverage`: 전체 텍스트에 `SCR-01, SCR-06, SCR-07, SCR-08, SCR-10, SCR-11, SCR-12, SCR-16, SCR-18, SCR-19, OMS-08, OMS-09, OMS-16, COM-02` 각각 1회 이상.
- `check_button_refs`: 장면 설명의 `관리자:` 줄에 `[…]` 버튼이 있으면 같은 칸의 `근거:` 줄에 `SCR-` 또는 `P-` ID가 있음. 버튼 이름 → 근거 메시지 번호(`6-x #n`) 쌍을 모아 같은 메시지 번호가 서로 다른 버튼 이름에 쓰이면 실패.
- `check_text_fit`: 설명 셀마다 줄 수(`<br>` 기준, 한 줄 38자 넘으면 줄바꿈으로 셈) × 15 + 10 ≤ 셀 높이.

- [ ] **Step 2: 검증기를 빈 파일 대상으로 실행 → 실패 확인**

Run: `python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: 파일 없음 또는 `check_structure` 실패, 종료 코드 ≠ 0

- [ ] **Step 3: build.py 공통 부품과 탭 0 명세 작성**

탭 0 `0. 화면 구성` 내용:
- 위쪽 고정 줄 확대 그림 1개 (각 요소에 설명 꼬리표: 메뉴, 작업 상태 · 모드 버튼 SCR-16, 비상정지 COM-02 · 제안 P-01).
- 화면 목록 6칸(작은 와이어프레임): 로그인(SCR-01) · 현황(SCR-06 · 07 · 10, 지도 자리에 "일정에 따라" 노트) · 주문 관리(SCR-07 · 08 · 11 · 12, 조치 필요 / 진행 중 / 완료) · 재고(SCR-18, OMS-08 · 09) · 이력(SCR-19, OMS-16 알림 목록) · 비상조치(COM-02, 제안 5-5).
- 알림 나누기 규칙 표 노트: 주문 조치 → [주문 관리로], 로봇 조치 → 알림 칸에서 바로, 작업 모드 → 위쪽 고정 줄.
- "조치 필요 주문" 기준표 노트(스펙 2절 표 그대로).
- 용어 노트(스펙 용어표 그대로).
- 범례 노트: 장면 칸 구성, ①② 표시, `[조건]` 갈래, 주황 점선 = 미결.
- 탭 1 ~ 8은 이 단계에서 빈 탭(제목 · 버전 라벨만)으로 둔다.

- [ ] **Step 4: 생성 → 검증**

Run: `python3 scratchpad/sb/build.py && python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: `check_structure` · `check_terms` · `check_text_fit` 통과. `check_coverage`는 탭 0에 모든 ID가 나오므로 통과. 종료 코드 0.

- [ ] **Step 5: 렌더 확인**

Run: `python3 scratchpad/render.py design/diagrams/관리자_스토리보드.drawio "0. 화면 구성" scratchpad/sb/t0.png` 후 이미지 보기.
Expected: 겹침 없음, 비상정지 버튼이 다른 버튼보다 큼, 글자가 칸 안에 있음.

### Task 2: 탭 1 ~ 3 (평상시 현황 · 결품 · Pode 장애)

**Files:**
- Modify: `scratchpad/sb/scenes.py` (TABS[1..3] 장면 추가)
- Regenerate: `design/diagrams/관리자_스토리보드.drawio`

**Interfaces:**
- Consumes: Task 1의 `scene`, `screen`, `arrow`, `note`, `TABS` 형식.

- [ ] **Step 1: 탭 1 `1. 평상시 현황` 장면**
  1. 로그인(SCR-01) → 2. 현황: 로봇 6대 상태 · 주문 진행 요약, 지도 자리 "일정에 따라"(SCR-06 · 07, 6-1-1 loop) → 3. 현황: 주문 #1024 배정됨(ASSIGNED) → 담는 중(FILLING) (6-1-1 #15, 6-1-2 #20a) → 4. 운반 중(TRANSPORTING) → 출고됨(SHIPPED)(6-1-3 #34) → 5. 주문 관리: 진행 중 · 완료 보기(SCR-07).
- [ ] **Step 2: 탭 2 `2. 결품 · 담기 일시 중지` 장면**
  1. 현황 알림 칸: 결품 알림 + [주문 관리로] (6-2 #12, D-06 노트) → 2. 주문 관리 조치 필요 줄 "#1024 담기 일시 중지(FILLING_PAUSE) · 결품" [재개] [보충 불가 · 못 담음] → 갈래 `[보충 가능]` 3. 관리자 보충 후 ①[재개] → 담는 중 (SCR-08 · 6-2 #14) / `[보충 불가]` 4. ①[보충 불가 · 못 담음] (SCR-08 · 6-2 #18) → 갈래 `[담을 상품 남음]` 5. 남은 상품 담기 · 사용자 결품 안내(6-2 #22) / `[담을 상품 없음]` 6. 취소됨(CANCELLED) · 사용자 취소 안내 · Pode 복귀 (6-2 #24 ~ #27, D-14 노트).
  - 아래 줄 별도 흐름: 7. 담는 도중 집기 실패 · 인식 오류 → 팔 일시 정지 + 주문 담기 일시 중지 → 같은 조치 줄([재개] / [보충 불가 · 못 담음], 근거 `SCR-08 · 2. 주문 담기 일시 중지 전이`) / 8. 거리 정지 → 알림 칸 로봇 조치 ①[재개] (SCR-12, 팔 일시 정지 해제, 주문은 담는 중 유지).
- [ ] **Step 3: 탭 3 `3. Pode 장애` 장면**
  1. 알림 칸: Pode 장애 알림(6-5 #11) → 갈래 `[빈 바구니 (배정됨)]` 2. 자동 재배정, 알림만(조치 없음, 6-5 #3 ~ #6) / `[상품 바구니 (운반 중)]` 3. 주문 관리 조치 필요 "#1021 주문 처리 보류(HELD) · Pode 장애" — 현장에서 바구니를 출고 위치로 옮긴 뒤 ①[강제 출고 처리] (SCR-11 · 6-5 #14) → 4. 출고됨 · 일부 출고됨(6-5 #17) → 5. 수리 후 알림 칸 ①[운행 복귀 확인] (SCR-11 · 6-5 #20).
  - 노트: "담는 중 · 담기 일시 중지에는 Pode 장애로 주문을 판단하지 않음 (운반 중부터)".
- [ ] **Step 4: 생성 → 검증 → 렌더 확인**

Run: `python3 scratchpad/sb/build.py && python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: 종료 코드 0. 탭 1 ~ 3 렌더(`render.py … "1." / "2." / "3."`)에서 겹침 · 넘침 없음.

### Task 3: 탭 4 ~ 6 (Phody 장애 · Phony 장애 · 서비스 일시 정지)

**Files:**
- Modify: `scratchpad/sb/scenes.py` (TABS[4..6])
- Regenerate: `design/diagrams/관리자_스토리보드.drawio`

- [ ] **Step 1: 탭 4 `4. Phody 장애`**
  1. 알림 칸: Phody 장애 알림 + 위쪽 작업 상태 "서비스 일시 정지"(6-6 #3 · #11) → 2. 주문 관리 "#1024 주문 처리 보류(HELD) · Phody 장애" ①[일부 출고 강제 처리] (SCR-12 · 6-6 #13) → 3. 운반 중 · 담은 상품만(6-6 #16) → 4. 알림 칸 ①[장애 처리 완료] (SCR-12 · 6-6 #18), 알림에 "위쪽에서 재시작" 안내 → 5. 위쪽 ①[재시작] (SCR-16 · 6-6 #22) → 작업 상태 "실행 중", Pode 출고 구역으로(6-6 #27).
- [ ] **Step 2: 탭 5 `5. Phony 장애`**
  1. 알림 칸: Phony 장애 알림(6-7 #6), 출고 구역 Pode 출고 대기 유지 → 2. 현장에서 손으로 바구니 교환 → 주문 관리 "#1025 운반 중 · Phony 장애" ①[강제 출고 처리] (SCR-11 · 6-7 #9) → 3. 출고됨 · 일부 출고됨(6-7 #12) → 4. 알림 칸 ①[운행 복귀 확인] (SCR-11 · 6-7 #14) → Pode 복귀(6-7 #19).
- [ ] **Step 3: 탭 6 `6. 서비스 일시 정지 · 재시작`**
  1. 위쪽 ①[서비스 일시 정지] (SCR-16) → 2. 확인 창 "로봇은 하던 동작을 마치고 멈춥니다" [확인] [닫기] → 3. 작업 상태 "서비스 일시 정지", 로봇 상태 카드 정지 표시(CEN-10, 운행 모드 PAUSE) → 4. 위쪽 ①[재시작] (SCR-16) → 작업 상태 "실행 중".
  - 노트: "취소 후 멈춤 · 정지 표시는 별도 논의(스토리보드 제외)" — 단, 금지어 `취소하고 멈춤`과 운행 모드 `STOP`은 쓰지 않는다.
- [ ] **Step 4: 생성 → 검증 → 렌더 확인**

Run: `python3 scratchpad/sb/build.py && python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: 종료 코드 0. `check_button_refs`에서 `[재시작]`이 6-6 #22 하나에만 대응.

### Task 4: 탭 7 ~ 8 (재고 · 이력, 비상정지)

**Files:**
- Modify: `scratchpad/sb/scenes.py` (TABS[7..8])
- Regenerate: `design/diagrams/관리자_스토리보드.drawio`

- [ ] **Step 1: 탭 7 `7. 재고 · 이력`**
  1. 재고 화면: 상품 · 수량 · 기준 수량 목록, ①[입고 · 수정] (SCR-18 · OMS-08) → 2. 기준 수량 설정 ①[저장] (OMS-09) → 3. 재고 부족 알림이 알림 칸에 뜸(OMS-09) → 4. 이력 화면: 주문 · 재고 · 오류 이력 조회(SCR-19, 통계 없음) → 5. 알림 목록 화면: 발생 · 확인 · 해결 상태(OMS-16).
- [ ] **Step 2: 탭 8 `8. 비상정지`** (근거는 제안 ID)
  1. 위쪽 ①[비상정지] (COM-02, P-01) → 2. 비상조치 화면으로 고정: "비상정지 중 · 누른 곳 · 시각", 로봇별 상태 ESTOP · 락 · 조치(P-06 · P-08), 응답 없는 로봇 경고(P-14) → 3. 로봇별 ①[락 해제] → 현장 정리 → ②[락 잠금] (P-08, 팔은 "받친 뒤 해제" 안내) → 4. 출고 대기 주문 ①[수동 출고 완료] (P-12 · P-13) → 5. 체크리스트 6항목 체크 → ①[해제] 켜짐 (P-09, Pode 위치 경고) → 6. 해제 뒤 작업 상태 "서비스 일시 정지" 유지(P-11) → 7. 위쪽 ①[재시작] (SCR-16, P-11).
  - 노트(주황 점선): "비상정지 시나리오 (제안) v3 기준, 팀 채택 전".
- [ ] **Step 3: 순서 검사 추가 후 실행**

`check.py`에 `check_estop_order` 추가: 탭 8에서 `[해제]` 장면 번호 < `서비스 일시 정지` 유지 장면 번호 < `[재시작]` 장면 번호, 그리고 `[해제]` 장면의 `시스템:` 줄에 `실행 중`이 없음.
Run: `python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: 종료 코드 0.

- [ ] **Step 4: 전 탭 렌더 확인**

Run: 탭 0 ~ 8을 `render.py`로 렌더해 이미지 확인.
Expected: 겹침 · 넘침 없음, 갈래 라벨이 화살표 옆에 보임.

### Task 5: 검수 · 문서 · 마무리

**Files:**
- Modify: `design/README.md` (현재 문서 표에 `관리자 스토리보드 | diagrams/관리자_스토리보드.drawio | v1.0 | 2026-11-18 | -` 추가, 디렉터리 설명 줄에 "관리자 스토리보드")
- Modify: `design/CHANGELOG.md` (`## 2026-11-18` 맨 위에 `- 관리자 스토리보드 v1.0: 처음 작성 (탭 9장, B안 관제실 한 화면형, 설계 docs/superpowers/specs/2026-11-18-admin-storyboard-design.md)`)

- [ ] **Step 1: UML · 흐름 검수 서브에이전트 (사용자 요청 사항)**

새 서브에이전트에 스토리보드 파일과 `IFP_다이어그램.drawio`(v2.4)를 주고, 읽기만 하며 다음을 보고하게 한다: 장면 흐름이 시퀀스 6-1 ~ 6-7 · 상태 다이어그램 2 · 3 · 4와 어긋나는 곳, 갈래 조건이 alt 조건과 다르거나 빠진 경우, 버튼-메시지 1:1 위반, 용어 위반. 규칙 위반 / 권장으로 나눠 보고.

- [ ] **Step 2: 검수 결과 반영 → 재생성 → 검증**

Run: `python3 scratchpad/sb/build.py && python3 scratchpad/sb/check.py design/diagrams/관리자_스토리보드.drawio`
Expected: 종료 코드 0.

- [ ] **Step 3: README · CHANGELOG 수정**

- [ ] **Step 4: 커밋 (사용자 승인 후)**

```bash
git add design/diagrams/관리자_스토리보드.drawio design/README.md design/CHANGELOG.md docs/superpowers/specs/2026-11-18-admin-storyboard-design.md docs/superpowers/plans/2026-11-18-admin-storyboard.md
git commit -m "docs(design): 관리자 스토리보드 v1.0 — 화면 구성과 시나리오 8종"
```
