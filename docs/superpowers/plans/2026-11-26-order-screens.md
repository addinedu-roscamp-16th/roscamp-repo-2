# 주문 화면 구성 구현 계획 (고객 주문 화면 · 관리자 상태 표시)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객 주문 화면(`/customer/`)과 관리자 상태 표시(`/admin/`)를 정적 HTML + 순수 JS로 만든다. 먼저 화면 안 가짜 데이터로 검토하고, 백엔드가 끝나면 같은 화면을 실제 API · WS에 붙인다.

**Architecture:**
- 그리는 코드는 메시지 출처(`source.js`)가 주는 `{type, seq?, ts, data}` 메시지와 `placeOrder` 하나만 안다.
- 출처는 주소에 `?demo=<장면>`이 있으면 가짜(`demo.js`), 없으면 실제(`live.js`)다.
- 화면 상태 계산(`*/state.js`)은 DOM을 모르는 순수 함수로 두고, 브라우저 시험 페이지(`test/`)로 확인한다. DOM 그리기는 `*/app.js`에 둔다.

**Tech Stack:** HTML · CSS · JavaScript ES 모듈(빌드 도구 · npm 없음), 시험 서버 `python3 -m http.server`, 확인은 앱 안 브라우저 창(`mcp__Claude_Browser__*`)

**작업 위치:** worktree `/home/ane/dev_ws/roscamp-repo-2/.claude/worktrees/order-screens`, 브랜치 `feat/order-screens`(`dev` `52b6da4`에서). 아래 경로는 모두 이 worktree 기준이다.

**참고 문서 위치:** 2단계 설계 · 계획, 백엔드 검토 목록 v0.10, 백엔드 코드(`core/intake.py` · `core/snapshot.py` · `web/`)는 아직 `dev`에 없다. 원래 폴더 `/home/ane/dev_ws/roscamp-repo-2`(브랜치 `docs/backend-review-list`, 백엔드 작업 진행 중)에서 **읽기만** 한다. 그 폴더의 파일은 고치지 않는다.

**Spec:** `docs/superpowers/specs/2026-11-26-order-screens-design.md` (2026-11-26 승인). 각 작업은 설계의 절 번호를 단다. 실행하는 사람은 설계와 이 계획을 함께 읽는다. 메시지 모양의 원본은 2단계 설계(`docs/superpowers/specs/2026-11-25-order-intake-api-ws-design.md`) 5.2 · 5.5 · 7장과 백엔드 검토 목록 8.2다.

## Global Constraints

- **언어:** 화면 문구 · 주석 · 문서는 한국어다. 주석 머리는 저장소 관례대로 `// ---- … ----` 모양이다.
- **위치:** 모든 화면 파일은 `pc/service_bridge/service_bridge/web/static/` 아래에만 만든다. 그 밖(`web/*.py`, `setup.py`, `core/`)은 고치지 않는다. 같은 폴더에서 백엔드 작업이 진행 중이다.
- **경로:** HTML · JS · CSS 안의 경로는 모두 상대 경로다(`../common/words.js`). 절대 경로(`/api/…`, `/ws/…`)는 `common/live.js` 한 곳에만 있다.
- **도구:** 빌드 도구 · npm · 외부 CDN을 쓰지 않는다. 글꼴은 시스템 글꼴(`"Noto Sans KR", "Malgun Gothic", sans-serif`)이다.
- **주소 인자:** `?account=<account_id 숫자>`, `?demo=<장면 이름>`. 고객 화면 머리에는 "계정 <번호>"를 보인다.
- **고객 상태 말:** 화면은 서버의 `status_text`를 그대로 보인다. 진행 칸: 접수됨 1 · 준비 중 2 · 출고 중 3 · 출고 완료 4. 일부 출고 · 취소됨은 막대 없이 말만.
- **고객 화면에 나오면 안 되는 말:** 장애 · 보류 · 일시 정지 · 일시 중지 · 마감 · 결품 · 배정 · 운반 · `RECEIVED` 같은 내부 상태 값.
- **한 주문 최대 수량:** 스냅샷 `limits.max_order_items`, 없으면 8(콘솔 경고).
- **주문 보내기:** `Idempotency-Key`는 `crypto.randomUUID()`. 고른 상품 · 수량이 그대로인 동안 같은 키를 다시 쓴다. 202를 받거나 고른 것이 바뀌면 새 키. 503 `BUSY`는 `Retry-After`초(없으면 1초) 뒤 같은 키로 최대 3번. 망 오류는 같은 키로 1번 더, 그래도 실패하면 스냅샷 다시 읽기.
- **WS 다시 연결:** 1 → 2 → 5초(최대 5초). 닫힘 코드 4404는 다시 붙지 않는다. 스냅샷을 받으면 화면 전체를 다시 그리고 `seq` 기억을 지운다. 같은 `seq` 두 번째는 버린다.
- **모르는 주문 번호:** 고객 `order_changed`가 모르는 `order_id`면 2초 기다렸다가 그래도 모르면 스냅샷을 다시 읽는다.
- **색 (3안 시안에서 뽑은 값, CSS 변수로):**
  - 고객(밝은 톤): 바탕 `#ffffff`, 카드 `#f7f8fa`, 테두리 `#e6e8ee`, 초록 포인트 · 버튼 `#22c55e`, 진행 막대 `#7c3aed`, 빈 막대 `#e2e5ec`, 이미지 자리 `#f0fdf4`, 글자 `#17203f`, 보조 글자 `#5a6389`, 품절 · 오류 `#dc2626`
  - 관리자(다크 톤): 바탕 `#111418`, 머리 `#171b21`, 카드 `#1a1e24`, 알림 카드 `#1f242b`, 초록 `#4ade80`, 빨강 `#f87171`, 파랑 `#6e8bff`, 노랑 `#fbbf24`, 보라 `#a78bfa`, 비상정지 `#dc2626`, 칸 바탕(조치 필요 `#2a1a1c` · 일시 중지 `#2a2414` · 접수됨 `#1a2240`)
  - 간격 · 글자 크기 · 둥글기는 시안 PNG(`design/exports/스토리보드_1st/2팀_GUIDesign-사용자_스타일(밝은 톤).png` · `…관리자_스타일(다크).png`, 1440 × 900 기준)를 보고 맞춘다.
- **시험:** 자동 브라우저 도구가 없다. 순수 함수는 `static/test/index.html` 시험 페이지로 확인하고, 화면은 앱 안 브라우저로 눌러 보고 캡처한다.
  - 시험 서버: `.claude/launch.json`의 `screens-static`(Task 1에서 만듦) → `preview_start {name: "screens-static"}`
  - 시험 페이지 통과 기준: 페이지 글에 `결과: 통과 N · 실패 0`
- **브랜치 · 커밋**
  - 이 worktree의 `feat/order-screens`에서만 일한다. 브랜치를 바꾸지 않는다.
  - 설계 문서와 이 계획은 이 브랜치의 첫 커밋으로 들어간다. 백엔드 검토 목록 v0.10은 원래 폴더(`docs/backend-review-list`)에 남는다.
  - 커밋은 사용자가 이 계획의 실행을 맡긴 범위에서만 한다. 경로를 하나씩 적어 스테이징한다(`git add -A` 금지).
  - `git -c user.name=ane -c user.email=brightbig01@gmail.com commit`, 끝 줄은 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 푸시하지 않는다.

## Review Focus

1. **[주문하기]를 빠르게 두 번 누름:** 첫 요청이 끝나기 전 두 번째 누름은 무시된다(버튼 막힘). 주문은 하나 → Task 4 `customer: 보내는 중에는 두 번째 보내기를 막음`.
2. **주문 중에 다른 탭 · 다른 주문으로 재고가 줄어 고른 수량이 재고보다 커짐:** `stock` 메시지를 받으면 고른 수량을 새 재고 안으로 낮추고 키를 새로 만든다 → Task 4 `customer: stock이 줄면 고른 수량을 낮춤`.
3. **스냅샷을 다시 받았는데 이 브라우저가 넣은 주문의 품목이 스냅샷에 없음(N-06 전):** 보낸 본문으로 채운 품목을 잃지 않는다 → Task 4 `customer: 다시 받은 스냅샷에 items가 없으면 기억한 품목을 유지`.
4. **검색어와 일치하는 상품이 없음 / 상품 목록이 비어 있음:** 빈 안내 문구를 보이고 [주문하기]는 막힌다 → Task 4 `customer: 고른 것이 없으면 주문할 수 없음`, Task 5 화면 확인.
5. **관리자: 운반 건이 없는 주문, 모르는 상태 값, 로봇이 0대:** 칸 나누기가 멈추지 않는다. 모르는 상태 값은 원래 값을 보인다 → Task 6 `admin: 모르는 상태 값은 진행 중 칸에 원래 값으로`, `admin: robots 0대면 빈 목록`.

---

### Task 1: 뼈대 · 시험 페이지 · 공용 말 표 (설계 4.1 · 3.1 · 3.2 · 3.3)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/index.html` (주소 `?…`를 붙여 `customer/`로 넘김, `location.replace`)
- Create: `pc/service_bridge/service_bridge/web/static/common/base.css` (초기화 · 글꼴)
- Create: `pc/service_bridge/service_bridge/web/static/common/words.js`
- Create: `pc/service_bridge/service_bridge/web/static/test/index.html`, `test/harness.js`, `test/words.test.js`
- Create (커밋하지 않음, `.claude/`는 `.gitignore` 대상): `.claude/launch.json` (없으면 만들고, 있으면 항목만 더함): `{"name": "screens-static", "runtimeExecutable": "python3", "runtimeArgs": ["-m", "http.server", "8765", "-d", "pc/service_bridge/service_bridge/web/static"], "port": 8765}`

**Interfaces:**
- Produces (`test/harness.js`):
  - `test(name: string, fn: () => void | Promise<void>)`, `eq(actual, expected, msg?)`(JSON 비교), `ok(cond, msg?)`
  - `run()`: 등록된 시험을 차례로 돌리고 페이지에 `결과: 통과 N · 실패 M`과 실패 이름 · 이유를 쓴다. `test/index.html`은 `*.test.js`를 모두 import한 뒤 `run()`.
- Produces (`common/words.js`):
  - `PROGRESS_STEPS: string[]` = `["접수됨", "준비 중", "출고 중", "출고 완료"]`
  - `progressIndex(statusText: string): number | null` (1 ~ 4, 표에 없으면 `null`)
  - `ADMIN_STATUS: Record<string, string>` = RECEIVED 접수됨 · ASSIGNED 배정됨 · FILLING 담는 중 · FILLING_PAUSE 담기 일시 중지 · HELD 주문 처리 보류 · TRANSPORTING 운반 중 · SHIPPED 출고됨 · PARTIAL 일부 출고됨 · CANCELLED 취소됨
  - `adminStatusText(status: string): string` (표에 없으면 원래 값)
  - `adminColumn(status: string): "action" | "progress" | "received" | "done"` (FILLING_PAUSE · HELD → action, RECEIVED → received, SHIPPED · PARTIAL · CANCELLED → done, 그 밖 → progress)
  - `MODE_TEXT` = NORMAL 실행 중 · TEST 시험 모드, `modeText(mode: string): string`
  - `robotWord(robot: {fsm_state: string, fault: string}): {text: string, tone: "ok" | "busy" | "bad"}` (`fault`가 `NONE`이 아니면 `{text: "오류", tone: "bad"}`, 아니면 `FSM_TEXT` 표, 표에 없으면 `{text: fsm_state, tone: "busy"}`). `FSM_TEXT` 값은 `pc/fleet_core`의 관제 FSM 상태 값을 찾아 채운다(설계 8장).
  - `productName(p: {product_id: number, name?: string}): string` (`name` 없으면 `상품 ${product_id}`)
  - `rejectMessage(status: number, body: object | null, names: Record<number, string>): {text: string, action: "lower" | "reload" | "maintenance" | null}` (설계 3.3 문구 그대로)

- [ ] **Step 1: 시험 쓰기 (`test/words.test.js`)**

```js
test("words: 진행 칸", () => { eq(progressIndex("준비 중"), 2); eq(progressIndex("일부 출고"), null); });
test("words: 관리자 칸 나누기", () => {
  eq(["FILLING_PAUSE","HELD","RECEIVED","ASSIGNED","FILLING","TRANSPORTING","SHIPPED","PARTIAL","CANCELLED"].map(adminColumn),
     ["action","action","received","progress","progress","progress","done","done","done"]);
});
test("words: 모르는 관리자 상태는 원래 값", () => eq(adminStatusText("NEW_STATE"), "NEW_STATE"));
test("words: 오류 로봇", () => eq(robotWord({fsm_state: "IDLE", fault: "ERROR"}), {text: "오류", tone: "bad"}));
test("words: 이름 없는 상품", () => eq(productName({product_id: 3}), "상품 3"));
test("words: 재고 초과 문구", () => {
  const r = rejectMessage(422, {code: "OUT_OF_STOCK", detail: {/* web/app.py의 실제 모양 */}}, {1: "미니멘토스 레인보우"});
  eq(r.action, "lower"); ok(r.text.includes("수량을 줄여 다시 주문해 주세요"));
});
test("words: 점검 중", () => eq(rejectMessage(503, {code: "MAINTENANCE"}, {}).action, "maintenance"));
test("words: 고객 말에 내부 말 없음", () => {
  for (const w of PROGRESS_STEPS) ok(!/장애|보류|일시|마감|결품|배정|운반/.test(w), w);
});
```

`OUT_OF_STOCK`의 `detail` 모양은 `pc/service_bridge/service_bridge/core/intake.py`(또는 백엔드 `web/app.py`)에서 확인해 그대로 쓴다. 아직 없으면 2단계 설계 5.2 "상품별 주문 수 · 재고"를 `{items: [{product_id, qty, stock_qty}]}`로 두고 `TODO(백엔드 확인)`을 단다.

- [ ] **Step 2: 시험 서버를 켜고 시험 페이지가 실패하는지 확인**

`preview_start {name: "screens-static"}` → `http://localhost:8765/test/` → `get_page_text`
Expected: import 오류 또는 `실패` > 0

- [ ] **Step 3: `harness.js` · `words.js` · `base.css` · `index.html` 구현**

- [ ] **Step 4: 시험 페이지 통과 확인** — `결과: 통과 8 · 실패 0`. `http://localhost:8765/?demo=normal`이 `/customer/?demo=normal`로 넘어가는지(404여도 주소만 확인).

- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/index.html pc/service_bridge/service_bridge/web/static/common/base.css pc/service_bridge/service_bridge/web/static/common/words.js pc/service_bridge/service_bridge/web/static/test
git commit -m "feat(screens): 화면 뼈대, 브라우저 시험 페이지, 상태 말 표"
```

---

### Task 2: 메시지 출처와 실제 연결 (설계 4.2 · 4.4)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/common/source.js`
- Create: `pc/service_bridge/service_bridge/web/static/common/live.js`
- Create: `pc/service_bridge/service_bridge/web/static/test/source.test.js` (`test/index.html`에 import 추가)

**Interfaces:**
- Produces (`source.js`):
  - `readParams(search: string): {demo: string | null, account: number | null}` (`account`는 양의 정수만, 아니면 `null`)
  - `type Source = { onMessage(h: (msg) => void): void, onState(h: (s: "connecting" | "open" | "reconnecting" | "unknown-account") => void): void, placeOrder(body: {account_id, items: [{product_id, qty}]}, key: string): Promise<{status: number, body: object | null, headers: {get(name: string): string | null}}>, reloadSnapshot(): Promise<void>, close(): void }`
  - `openSource({view: "customer" | "admin", accountId: number | null, demo: string | null}): Promise<Source>` — `demo`가 있으면 `import("./demo.js")`의 `createDemo`, 없으면 `createLive`. 가짜일 때 `document.body`에 "가짜 데이터: <장면>" 띠를 붙인다.
  - `makeSeqFilter(): {accept(msg): boolean, reset(): void}` — `seq` 없는 메시지는 늘 통과, 마지막 `seq` 이하는 버림, `type === "snapshot"`이면 `reset` 뒤 통과.
  - `backoffMs(attempt: number): number` — 0 → 1000, 1 → 2000, 2 이상 → 5000
- Produces (`live.js`):
  - `apiUrl(path: string): string`, `wsUrl(loc: {protocol, host}, view, accountId): string` (`https:` → `wss:`, 고객은 `/ws/customer?account_id=N`, 관리자 `/ws/admin`)
  - `createLive({view, accountId}): Source` — 받은 메시지는 `makeSeqFilter`를 거쳐 `onMessage`로 보낸다.  `placeOrder`는 `fetch(POST /api/orders, headers {"Content-Type": "application/json", "Idempotency-Key": key})`, 망 오류면 `{status: 0, body: null, headers}`를 돌려준다(던지지 않음). `reloadSnapshot`은 `GET /api/snapshot?view=…[&account_id=N]`을 읽어 `{type: "snapshot", ts, data}`로 `onMessage`에 넣는다.

- [ ] **Step 1: 시험 쓰기**

```js
test("source: 주소 인자", () => {
  eq(readParams("?account=3&demo=busy"), {demo: "busy", account: 3});
  eq(readParams("?account=abc"), {demo: null, account: null});
});
test("source: seq 거르기", () => {
  const f = makeSeqFilter();
  ok(f.accept({type: "order_changed", seq: 5})); ok(!f.accept({type: "order_changed", seq: 5}));
  ok(f.accept({type: "stock"})); ok(f.accept({type: "snapshot"})); ok(f.accept({type: "order_changed", seq: 1}));
});
test("source: 다시 연결 간격", () => eq([0, 1, 2, 7].map(backoffMs), [1000, 2000, 5000, 5000]));
test("live: WS 주소", () => {
  eq(wsUrl({protocol: "http:", host: "h:8000"}, "customer", 1), "ws://h:8000/ws/customer?account_id=1");
  eq(wsUrl({protocol: "https:", host: "h"}, "admin", null), "wss://h/ws/admin");
});
```

- [ ] **Step 2: 실패 확인** (시험 페이지 새로고침, `실패` > 0)
- [ ] **Step 3: `source.js` · `live.js` 구현.** WS `close` 코드 4404면 `onState("unknown-account")` 후 다시 붙지 않는다. 그 밖 닫힘은 `onState("reconnecting")` 후 `backoffMs` 간격. 연결 성공이면 시도 횟수를 0으로.
- [ ] **Step 4: 통과 확인** — `결과: 통과 12 · 실패 0`
- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/common/source.js pc/service_bridge/service_bridge/web/static/common/live.js pc/service_bridge/service_bridge/web/static/test/source.test.js pc/service_bridge/service_bridge/web/static/test/index.html
git commit -m "feat(screens): 메시지 출처 고르기와 실제 API · WS 연결"
```

---

### Task 3: 가짜 데이터 출처와 장면 (설계 4.3)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/common/demo.js`
- Create: `pc/service_bridge/service_bridge/web/static/common/demo_scenes.js`
- Create: `pc/service_bridge/service_bridge/web/static/test/demo.test.js`

**Interfaces:**
- Consumes: Task 2 `Source` 모양
- Produces:
  - `createDemo({view, scene: string}, clock = {setTimeout, now: Date.now}): Source` — 모르는 장면 이름이면 `normal`.
  - `SCENES: Record<string, {views: ("customer" | "admin")[], …}>` — 이름 `normal` · `stock` · `busy` · `maintenance` · `reconnect` · `race` · `many`. 화면마다 따로 돈다(탭 사이 공유 없음).
  - 가짜 메시지 모양은 N-05 ~ N-08 반영 모양이다.
    - 고객 스냅샷 `data`: `{orders: [{order_id, status_text, created_at, items: [{product_id, qty}]}], stock: {items: [{product_id, kind, name, stock_qty}]}, notice: null | {kind: "MAINTENANCE", message: "점검 중"}, limits: {max_order_items: 8}}`
    - 관리자 스냅샷 `data`: `{orders: [{order_id, status, created_at, items}], trips: [{trip_id, order_id, status, robot_id, items: [{product_id, qty_ordered, qty_placed, qty_missing}]}], robots: [{robot_id, fsm_state, fault, battery, x, y, trip_id}], work_state: {system_mode}, stock: {...}}`
    - 사건: `order_changed`(고객 `{order_id, status_text}`, 관리자 `{order_id, status}`), `trip_changed`, `robots` `{robots: [...]}`, `stock`, `customer_notice`. 사건에는 1부터 오르는 `seq`.
  - 상품 셋: `{1, "A", "미니멘토스 레인보우", 12}`, `{2, "B", "과즙팡 비타민 마이구미", 5}`, `{3, "C", "자유시간 Mini", 0}`. 주문 번호 `O-20261126-0001`부터.
  - 장면 동작
    - `normal` 고객: 지난 주문 하나(출고 완료). 주문 → 202 → 1.5초 간격 `order_changed` 접수됨 → 준비 중 → 출고 중 → 출고 완료, 202 직후 `stock`.
    - `normal` 관리자: 3초 뒤 주문 하나가 들어와 운반 건 · Pode(`pode1`) 카드가 RECEIVED → ASSIGNED → FILLING → TRANSPORTING → SHIPPED로 1.5초 간격, `robots`가 같이 바뀜. 끝나면 5초 뒤 다음 주문으로 되풀이.
    - `stock`: 첫 주문을 422 `OUT_OF_STOCK`(상품 1 재고 2)으로 거절, 그 뒤 `stock` 1 재고 2.
    - `busy`: 같은 키 요청 두 번은 503 `BUSY` + `Retry-After: 1`, 세 번째 202. 받은 키를 `console.info`로 찍음.
    - `maintenance`: 스냅샷 `notice` MAINTENANCE 다음 `customer_notice`, 주문은 503 `MAINTENANCE`. 관리자 `work_state.system_mode = "TEST"`.
    - `reconnect`: `normal`처럼 가다가 두 번째 사건 뒤 `onState("reconnecting")`, 3초 뒤 `onState("open")` + 그때 상태의 스냅샷.
    - `race`: 주문하면 `order_changed`(접수됨)를 먼저 보내고 0.5초 뒤 202.
    - `many`: 고객 주문 다섯 개(상태 말 다섯 가지 섞임, 일부 출고 · 취소됨 포함), 관리자 주문 네 개 · 운반 건 다섯 개(8개 주문 → 운반 건 2개 포함, FILLING_PAUSE · HELD 하나씩), Pode 셋 중 하나 `fault: "ERROR"`.

- [ ] **Step 1: 시험 쓰기** (가짜 `clock`: `setTimeout`을 모아 두고 `advance(ms)`로 돌리는 작은 함수를 시험 안에 둔다)

```js
test("demo: 첫 메시지는 snapshot", async () => { /* createDemo({view:"customer", scene:"normal"}) → 받은 첫 msg.type === "snapshot", data.limits.max_order_items === 8 */ });
test("demo: normal 주문 흐름", async () => {
  // placeOrder → status 202, body.order_id; advance 6000 → 고객 order_changed status_text 순서
  // eq(texts, ["접수됨", "준비 중", "출고 중", "출고 완료"]); seq가 1씩 오름
});
test("demo: busy는 같은 키로 세 번째에 202", async () => { /* 503, 503, 202, headers.get("Retry-After") === "1" */ });
test("demo: maintenance", async () => { /* 스냅샷 notice.kind === "MAINTENANCE", 다음 msg customer_notice, placeOrder → 503 code MAINTENANCE */ });
test("demo: 고객 메시지에 내부 상태 값 없음", async () => { /* many 스냅샷 + normal 흐름 전체 JSON에 "RECEIVED" · "FILLING" 등 orders 상태 값이 없음 */ });
test("demo: 모르는 장면은 normal", async () => { /* scene "xxx" → normal 스냅샷과 같음 */ });
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: `demo.js` · `demo_scenes.js` 구현**
- [ ] **Step 4: 통과 확인** — `결과: 통과 18 · 실패 0`
- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/common/demo.js pc/service_bridge/service_bridge/web/static/common/demo_scenes.js pc/service_bridge/service_bridge/web/static/test/demo.test.js pc/service_bridge/service_bridge/web/static/test/index.html
git commit -m "feat(screens): 가짜 데이터 출처와 검토 장면 일곱 개"
```

---

### Task 4: 고객 화면 상태 계산 (설계 3.1 · 3.3 · 4.4 대체 표시)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/customer/state.js`
- Create: `pc/service_bridge/service_bridge/web/static/test/customer.test.js`

**Interfaces:**
- Consumes: Task 1 `words.js`
- Produces (모두 순수 함수, 상태 객체를 고쳐 쓰지 않고 새 객체를 돌려줌):
  - `initialState(accountId: number): CState` — `{accountId, products: [], orders: [], cart: {}, key: null, sending: false, online: false, notice: null, reject: null, maxItems: 8, query: "", knownItems: {}, pendingChanges: {}, attempts: 0}`
  - `applyMessage(s, msg): {state: CState, effects: Effect[]}` — `Effect`는 `{kind: "reload-after", ms: 2000, orderId}`뿐.
    - `snapshot`: 상품 · 주문 · `notice` · `maxItems`(`limits` 없으면 8 + `console.warn`)를 바꿈. 주문 `items`가 없으면 `knownItems[order_id]`로 채움.
    - `order_changed`: 아는 주문이면 `status_text`만 바꿈. 모르면 `pendingChanges[order_id]`에 담고 `reload-after` 효과.
    - `stock`: 재고를 바꾸고, 고른 수량이 재고를 넘으면 재고까지 낮추고 `key = null`.
    - `customer_notice`: `notice`를 바꿈.
  - `setQty(s, productId, qty): CState` — 0 ≤ qty ≤ min(재고, maxItems − 다른 상품 합). 바뀌면 `key = null`.
  - `setOnline(s, online: boolean): CState` — `app.js`가 `onState`의 `open`이면 `true`, 그 밖이면 `false`로 부른다(초기값 `false`).
  - `cartTotal(s): number`, `canOrder(s): boolean` (합 > 0, `!sending`, `notice` 없음, `online`)
  - `beginSend(s): {state, key, body}` — `key`가 없으면 `crypto.randomUUID()`로 만들고, 있으면 그대로. `sending = true`.
  - `applyResponse(s, resp, sentBody): {state, retryAfterMs: number | null}`
    - 202: 주문을 맨 앞에 넣음(`status_text`는 `pendingChanges`에 있으면 그 값, 없으면 "접수됨", `items`는 보낸 본문, `created_at`은 지금). `knownItems` 기록, `cart = {}`, `key = null`, `sending = false`.
    - 503 `BUSY`: 시도 3번 미만이면 `retryAfterMs = Retry-After × 1000`(없으면 1000), `sending` 유지. 넘으면 거절 문구.
    - 망 오류(`status 0`): 첫 번째면 `retryAfterMs = 0`. 두 번째면 "주문이 접수됐는지 확인 중입니다" + `sending = false` (키 유지).
    - 그 밖: `rejectMessage`로 `reject`를 채움. `action "lower"`면 `detail` 재고로 수량을 낮춤(키 새로). 409면 `key = null`.
  - `orderView(s): {orderId, statusText, step: number | null, itemsText: string}[]` — `created_at` 역순(없으면 받은 순서 거꾸로), `itemsText`는 "이름 수량 · 이름 수량".
  - `productView(s): {productId, name, kind, stock, soldOut, qty, canInc, canDec}[]` — `query`가 이름에 들어간 것만.

- [ ] **Step 1: 시험 쓰기**

```js
test("customer: 수량은 재고와 최대 수량 안에서", () => { /* 상품1 재고 12 · 상품2 재고 5, max 8: setQty(1, 7) 뒤 setQty(2, 5) → 상품2는 1 */ });
test("customer: 품절은 0에서 못 올림", () => { /* stock 0 → productView soldOut, canInc false */ });
test("customer: 고른 것이 바뀌지 않으면 같은 키", () => { /* beginSend → 망 오류 두 번 → beginSend 다시 → 같은 key */ });
test("customer: 고른 것이 바뀌면 새 키", () => { /* beginSend → 실패 → setQty 바꿈 → beginSend → 다른 key */ });
test("customer: BUSY는 세 번까지 다시", () => { /* retryAfterMs 1000, 1000, 1000, 네 번째는 reject 문구 */ });
test("customer: 202 뒤 줄 하나, 수량 0", () => { /* orders 길이 1, statusText "접수됨", cart {} */ });
test("customer: race — order_changed가 202보다 먼저", () => { /* 모르는 order_changed(준비 중) → effect reload-after 2000 → 202 → 줄 하나, statusText "준비 중" */ });
test("customer: 보내는 중에는 두 번째 보내기를 막음", () => { /* beginSend 뒤 canOrder false */ });            // Review Focus 1
test("customer: stock이 줄면 고른 수량을 낮춤", () => { /* qty 4 → stock 2 → qty 2, key null */ });          // Review Focus 2
test("customer: 다시 받은 스냅샷에 items가 없으면 기억한 품목을 유지", () => { /* 202 뒤 items 없는 snapshot → itemsText 유지 */ }); // Review Focus 3
test("customer: 고른 것이 없으면 주문할 수 없음", () => { /* cartTotal 0 → canOrder false; query가 어떤 이름과도 안 맞으면 productView [] */ }); // Review Focus 4
test("customer: 재고 초과 거절은 수량을 재고까지 낮춤", () => { /* 422 OUT_OF_STOCK → qty = detail 재고 */ });
test("customer: 이름 없는 상품 · limits 없는 스냅샷", () => { /* name 없음 → "상품 3", maxItems 8 */ });
test("customer: 점검 중 · 연결 끊김이면 주문 불가", () => { /* customer_notice → canOrder false; setOnline(false) → canOrder false */ });
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: `customer/state.js` 구현**
- [ ] **Step 4: 통과 확인** — `결과: 통과 32 · 실패 0`
- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/customer/state.js pc/service_bridge/service_bridge/web/static/test/customer.test.js pc/service_bridge/service_bridge/web/static/test/index.html
git commit -m "feat(screens): 고객 화면 상태 계산 — 수량 · 같은 키 · 거절 · 모르는 주문"
```

---

### Task 5: 고객 화면 그리기 (설계 3.1 · 3.3, 밝은 톤)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/customer/index.html`
- Create: `pc/service_bridge/service_bridge/web/static/customer/app.js`
- Create: `pc/service_bridge/service_bridge/web/static/customer/style.css`
- Create: `pc/service_bridge/service_bridge/web/static/img/.gitkeep` (이미지 파일이 없으면 자리 상자)

**Interfaces:**
- Consumes: Task 2 `readParams` · `openSource`, Task 4 `customer/state.js` 전부
- Produces: 화면만. `app.js`는 상태를 하나 들고 메시지 · 누름마다 `state.js` 함수를 부른 뒤 다시 그린다. 효과 `reload-after`는 `setTimeout` 뒤 아직 모르면 `source.reloadSnapshot()`. `retryAfterMs`가 있으면 그만큼 뒤 같은 `key` · 같은 본문으로 `placeOrder`.

- [ ] **Step 1: 화면 구현** — 배치 · 문구는 설계 3.1 그림과 시안 PNG. 상품 이미지는 `../img/<kind>.png`, 불러오기 실패면 "상품 이미지" 자리 상자. `?account`가 없으면 "주소에 ?account=번호를 붙여 주세요"만 보이고 연결하지 않음(가짜 데이터일 때는 계정 1로 봄). 연결 표시: 연결됨 · 다시 연결 중 · 모르는 계정.
- [ ] **Step 2: 장면별 확인 (앱 안 브라우저, 1440 × 900)** — `http://localhost:8765/customer/?demo=<장면>`을 `normal` · `stock` · `busy` · `maintenance` · `reconnect` · `race` · `many` 차례로 열어 눌러 본다.
  - `normal`: 주문 → 줄 하나, 막대가 1 → 4칸, 재고 숫자 그대로(재고는 출고 때 줆, 이번 가짜 데이터는 바꾸지 않음)
  - `stock`: 안내 문구 · 수량 낮춤. `busy`: "잠시 기다려 주세요" 뒤 접수, 콘솔에 같은 키 세 번. `maintenance`: 위쪽 점검 줄, [주문하기] 막힘. `reconnect`: 연결 표시가 다시 연결 중 → 연결됨, 줄이 이어짐. `race`: 줄 하나뿐. `many`: 일부 출고 · 취소됨은 막대 없이 말만, 최근 주문이 위.
  - 검색어 "없는이름" → 빈 안내, [주문하기] 막힘.
  - `read_console_messages {onlyErrors: true}` → 오류 없음
  - `get_page_text`로 고객 화면 글에 장애 · 보류 · 일시 · 마감 · 결품 · 배정 · 운반 · `[취소]`가 없음을 확인
  - 시안 PNG와 나란히 캡처해 색 · 간격 비교
- [ ] **Step 3: 시험 페이지 다시 확인** — `결과: 통과 32 · 실패 0`
- [ ] **Step 4: 캡처 `normal`(주문 진행 중) · `many`를 사용자에게 보임**
- [ ] **Step 5: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/customer/index.html pc/service_bridge/service_bridge/web/static/customer/app.js pc/service_bridge/service_bridge/web/static/customer/style.css pc/service_bridge/service_bridge/web/static/img/.gitkeep
git commit -m "feat(screens): 고객 주문 화면 — 상품 고르기 · 주문 · 내 주문 진행"
```

---

### Task 6: 관리자 상태 계산과 화면 (설계 3.2, 다크 톤)

**Files:**
- Create: `pc/service_bridge/service_bridge/web/static/admin/state.js`
- Create: `pc/service_bridge/service_bridge/web/static/admin/index.html`, `admin/app.js`, `admin/style.css`
- Create: `pc/service_bridge/service_bridge/web/static/test/admin.test.js`

**Interfaces:**
- Consumes: Task 1 `words.js`, Task 2 `openSource`
- Produces (`admin/state.js`, 순수 함수):
  - `initialState(): AState` — `{orders: {}, trips: {}, robots: [], mode: null, products: []}`
  - `applyMessage(s, msg): AState` — `snapshot`(전체 바꿈), `order_changed`, `trip_changed`(`trip_id`로 넣거나 바꿈), `robots`, `stock`
  - `columns(s): {action: Card[], progress: Card[], received: Card[], done: Card[]}` — `Card = {tripId, orderId, statusText, robotId, itemsText}`. 운반 건 기준, `created_at`(주문) · `trip_id` 순. 운반 건이 없는 주문은 주문 상태로 카드 하나(`tripId: null`).
  - `robotCards(s): {robotId, text, tone, sub}[]` — `sub`는 `trip_id`가 있으면 그 번호, 없으면 "배터리 n%"
  - `stockLine(s): string` — "이름 재고 · 이름 재고"

- [ ] **Step 1: 시험 쓰기**

```js
test("admin: 칸 나누기", () => { /* many 스냅샷 → action에 FILLING_PAUSE · HELD 카드, done에 SHIPPED */ });
test("admin: trip_changed로 칸 옮김", () => { /* RECEIVED 카드 → trip_changed ASSIGNED → progress 칸, robotId */ });
test("admin: 운반 건 없는 주문도 카드", () => {});
test("admin: 모르는 상태 값은 진행 중 칸에 원래 값으로", () => { /* status "NEW" → progress, statusText "NEW" */ });   // Review Focus 5
test("admin: robots 0대면 빈 목록", () => eq(robotCards(initialState()), []));                                        // Review Focus 5
test("admin: 오류 로봇 카드", () => { /* fault ERROR → tone bad */ });
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: `admin/state.js` 구현, 통과 확인** — `결과: 통과 38 · 실패 0`
- [ ] **Step 4: 화면 구현** — 설계 3.2 그림 · 관리자 시안. 머리의 메뉴(주문 관리 · 로봇 · 재고 · 이력)는 흐리게 표시만 하고, [서비스 일시 정지] · [비상정지]는 보이되 `disabled`. 작업 상태는 `modeText`. 알림 칸은 "이번 주 없음".
- [ ] **Step 5: 장면별 확인** — `/admin/?demo=normal` · `maintenance` · `reconnect` · `many`. 카드가 칸을 옮겨 가는지, 시험 모드 표시, 다시 연결, 오류 로봇 빨강. 콘솔 오류 없음. 시안과 나란히 캡처.
- [ ] **Step 6: 캡처 `normal` · `many`를 사용자에게 보임**
- [ ] **Step 7: 커밋**

```bash
git add pc/service_bridge/service_bridge/web/static/admin pc/service_bridge/service_bridge/web/static/test/admin.test.js pc/service_bridge/service_bridge/web/static/test/index.html
git commit -m "feat(screens): 관리자 상태 표시 — 로봇 · 주문 진행 · 재고"
```

---

### Task 7: 문서 맞춤 (설계 4.1 · 5)

**Files:**
- Modify: `pc/service_bridge/README.md` — "화면" 절 한 개: 파일 위치, 가짜 데이터로 띄우는 명령(`python3 -m http.server 8765 -d pc/service_bridge/service_bridge/web/static`), 주소(`/customer/?demo=normal`, `/admin/?demo=normal`, `/test/`), 장면 목록, 실제 서버 주소(`http://localhost:8000/customer/?account=N`, N-07 마운트 뒤). 백엔드 담당이 같은 파일을 고치는 중이면 이 절만 따로 커밋할 수 있게 다른 절은 건드리지 않는다.
- Modify: `design/README.md` "현재 문서" 표 아래 또는 디렉터리 설명에 화면 위치 한 줄(`pc/service_bridge/service_bridge/web/static/`, 설계 문서 링크)

- [ ] **Step 1: 두 문서 고치기**
- [ ] **Step 2: README의 명령을 그대로 실행해 `/customer/?demo=normal`이 열리는지 확인**
- [ ] **Step 3: 커밋**

```bash
git add pc/service_bridge/README.md design/README.md
git commit -m "docs(screens): 화면 파일 위치와 가짜 데이터로 띄우는 법"
```

---

### Task 8: 실제 서버에 붙이기 (설계 5 "실제 서버 단계") — 조건이 갖춰진 뒤

**시작 조건:** 2단계 계획 Task 7이 `dev`에 들어감, 백엔드 검토 목록 N-07(마운트)이 들어감. N-05 · N-06 · N-08은 들어갔는지 확인만 하고, 없으면 대체 표시로 진행한다. 조건이 안 되면 이 작업은 하지 않고 사용자에게 알린다.

**Files:**
- Modify: 확인 중 어긋난 곳만 (`common/live.js`, `common/words.js`의 `rejectMessage` `detail` 모양, `customer/state.js`)

- [ ] **Step 1: 별도 시험 계획 확인** — 설계 1장 "화면이 나오면 별도 시험 계획으로 확인"(2단계 설계 1장). 사용자에게 시험 계획을 따로 쓸지 이 Task로 갈음할지 묻는다.
- [ ] **Step 2: 한 바퀴** — 시험 DB · seed, 브리지 `mode:=NORMAL` · `central` · `fake_fleet`(명령은 `pc/service_bridge/README.md`). `http://localhost:8000/customer/?account=<tester의 account_id>`와 `/admin/`을 나란히 열고 주문 → 출고 완료. 고객 막대 1 → 4칸, 관리자 카드 접수됨 → 출고됨.
- [ ] **Step 3: 거절 · 점검 · 다시 연결** — 재고보다 많이 주문(문구 · 수량 낮춤), `mode:=TEST`로 다시 켜서 점검 중, 브리지를 껐다 켜서 다시 연결 · 스냅샷, `?account=99999` → 모르는 계정 안내.
- [ ] **Step 4: 어긋난 곳 고치기와 시험 페이지 다시 통과**
- [ ] **Step 5: 캡처를 사용자에게 보이고 커밋** (고친 파일만 경로로)

```bash
git commit -m "fix(screens): 실제 API · WS에 맞춤"
```
