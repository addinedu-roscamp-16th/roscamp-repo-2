// ---- customer/state.js 시험 ----
import { test, eq, ok } from "./harness.js";
import {
  initialState, applyMessage, setQty, setOnline, cartTotal, canOrder,
  beginSend, applyResponse, orderView, productView, makeUuid,
} from "../customer/state.js";
import { rejectMessage, noticeText, MAINTENANCE_TEXT } from "../common/words.js";

const P = [
  { product_id: 1, kind: "snack", name: "미니멘토스", stock_qty: 12 },
  { product_id: 2, kind: "snack", name: "과즙팡", stock_qty: 5 },
  { product_id: 3, kind: "snack", stock_qty: 0 },
];
const snap = (extra = {}) => ({
  type: "snapshot",
  data: { orders: [], stock: { items: JSON.parse(JSON.stringify(P)) }, notice: null, limits: { max_order_items: 8 }, ...extra },
});
const ready = (extra) => setOnline(applyMessage(initialState(1), snap(extra)).state, true);
const qtyOf = (s, id) => s.cart[id] || 0;
const res = (status, body, headers = {}) => ({ status, body, headers: { get: n => (n in headers ? headers[n] : null) } });
const ok202 = (order_id = "O-1") => res(202, { order_id, status: "RECEIVED", trip_ids: ["t1"] });
const busy = () => res(503, { code: "BUSY", message: "", detail: null }, { "Retry-After": "1" });
const net = () => res(0, null);

test("customer: 수량은 재고와 최대 수량 안에서", () => {
  let s = ready();
  s = setQty(s, 1, 7);
  s = setQty(s, 2, 5);
  eq(qtyOf(s, 1), 7); eq(qtyOf(s, 2), 1);
  eq(cartTotal(s), 8);
  s = setQty(s, 1, 99);                       // 상품2 1개 → 상품1은 최대 7
  eq(qtyOf(s, 1), 7);
});

test("customer: 품절은 0에서 못 올림", () => {
  const s = setQty(ready(), 3, 1);
  const v = productView(s).find(p => p.productId === 3);
  ok(v.soldOut); ok(!v.canInc); eq(v.qty, 0);
});

test("customer: 고른 것이 바뀌지 않으면 같은 키", () => {
  let s = setQty(ready(), 1, 2);
  const a = beginSend(s); s = a.state;
  let r = applyResponse(s, net(), a.body); s = r.state; eq(r.retryAfterMs, 0);
  const b = beginSend(s); s = b.state;
  r = applyResponse(s, net(), b.body); s = r.state; eq(r.retryAfterMs, null);
  ok(!s.sending); ok(s.reject.text.includes("접수됐는지 확인 중"));
  const c = beginSend(s);
  eq(c.key, a.key); eq(b.key, a.key);
});

test("customer: 고른 것이 바뀌면 새 키", () => {
  let s = setQty(ready(), 1, 2);
  const a = beginSend(s); s = applyResponse(a.state, net(), a.body).state;
  s = setQty(s, 1, 3);
  const b = beginSend(s);
  ok(b.key && b.key !== a.key);
});

test("customer: BUSY는 세 번까지 다시", () => {
  let s = setQty(ready(), 1, 1);
  let cur = beginSend(s); s = cur.state;
  const waits = [];
  for (let i = 0; i < 3; i++) {
    const r = applyResponse(s, busy(), cur.body); s = r.state; waits.push(r.retryAfterMs);
    ok(s.sending);
    cur = beginSend(s); s = cur.state;
  }
  eq(waits, [1000, 1000, 1000]);
  const r = applyResponse(s, busy(), cur.body);
  eq(r.retryAfterMs, null); ok(!r.state.sending);
  eq(r.state.reject.text, rejectMessage(503, { code: "BUSY" }, {}).text);
});

test("customer: 202 뒤 줄 하나, 수량 0", () => {
  let s = setQty(ready(), 1, 2);
  const a = beginSend(s);
  s = applyResponse(a.state, ok202(), a.body).state;
  eq(s.orders.length, 1); eq(orderView(s)[0].statusText, "접수됨");
  eq(s.cart, {}); eq(s.key, null); ok(!s.sending);
  eq(orderView(s)[0].itemsText, "미니멘토스 2");
});

test("customer: race — order_changed가 202보다 먼저", () => {
  let s = setQty(ready(), 1, 1);
  const a = beginSend(s);
  const m = applyMessage(a.state, { type: "order_changed", data: { order_id: "O-1", status_text: "준비 중" } });
  eq(m.effects, [{ kind: "reload-after", ms: 2000, orderId: "O-1" }]);
  s = applyResponse(m.state, ok202("O-1"), a.body).state;
  eq(s.orders.length, 1); eq(orderView(s)[0].statusText, "준비 중");
});

test("customer: 보내는 중에는 두 번째 보내기를 막음", () => {
  const s = setQty(ready(), 1, 1);
  ok(canOrder(s));
  ok(!canOrder(beginSend(s).state));
});

test("customer: stock이 줄면 고른 수량을 낮춤", () => {
  let s = setQty(ready(), 1, 4);
  s = beginSend(s).state; s = { ...s, sending: false };
  ok(s.key);
  const m = applyMessage(s, { type: "stock", data: { items: [{ product_id: 1, stock_qty: 2 }] } });
  eq(qtyOf(m.state, 1), 2); eq(m.state.key, null);
  eq(productView(m.state).find(p => p.productId === 1).stock, 2);
});

test("customer: 다시 받은 스냅샷에 items가 없으면 기억한 품목을 유지", () => {
  let s = setQty(ready(), 1, 2);
  const a = beginSend(s);
  s = applyResponse(a.state, ok202("O-1"), a.body).state;
  s = applyMessage(s, snap({ orders: [{ order_id: "O-1", status_text: "준비 중" }] })).state;
  eq(orderView(s)[0].itemsText, "미니멘토스 2"); eq(orderView(s)[0].statusText, "준비 중");
});

test("customer: 고른 것이 없으면 주문할 수 없음", () => {
  let s = ready();
  eq(cartTotal(s), 0); ok(!canOrder(s));
  s = { ...s, query: "없는이름" };
  eq(productView(s), []);
  eq(productView({ ...s, query: "멘토" }).length, 1);
});

test("customer: 재고 초과 거절은 수량을 재고까지 낮춤", () => {
  let s = setQty(ready(), 1, 5);
  const a = beginSend(s);
  const r = applyResponse(a.state, res(422, { code: "OUT_OF_STOCK", message: "", detail: { items: [{ product_id: 1, qty: 5, stock_qty: 2 }] } }), a.body);
  eq(qtyOf(r.state, 1), 2); eq(r.state.key, null); ok(!r.state.sending);
  ok(r.state.reject.text.includes("미니멘토스 재고가 2개뿐"));
  eq(r.retryAfterMs, null);
});

test("customer: 이름 없는 상품 · limits 없는 스냅샷", () => {
  const warn = console.warn; let warned = 0; console.warn = () => { warned++; };
  let s;
  try {
    s = applyMessage(initialState(1), { type: "snapshot", data: { orders: [], stock: { items: JSON.parse(JSON.stringify(P)) }, notice: null } }).state;
  } finally { console.warn = warn; }
  eq(s.maxItems, 8); ok(warned > 0);
  eq(productView(s).find(p => p.productId === 3).name, "상품 3");
});

test("customer: 점검 중 · 연결 끊김이면 주문 불가", () => {
  let s = setQty(ready(), 1, 1);
  ok(canOrder(s));
  const n = applyMessage(s, { type: "customer_notice", data: { kind: "MAINTENANCE", message: "점검 중입니다." } }).state;
  ok(!canOrder(n)); eq(n.notice.message, "점검 중입니다.");
  ok(!canOrder(setOnline(s, false)));
  ok(!canOrder(setQty(applyMessage(initialState(1), snap()).state, 1, 1)));   // 초기 online false
});

test("customer: order_changed는 아는 주문의 상태 말만 바꿈", () => {
  let s = ready({ orders: [{ order_id: "O-9", status_text: "접수됨", created_at: "2026-11-26T09:00:00Z", items: [{ product_id: 2, qty: 1 }] }] });
  const m = applyMessage(s, { type: "order_changed", data: { order_id: "O-9", status_text: "출고 중" } });
  eq(m.effects, []); eq(orderView(m.state)[0], { orderId: "O-9", statusText: "출고 중", step: 3, itemsText: "과즙팡 1" });
});

test("customer: 주문은 created_at 역순, 없으면 받은 순서 거꾸로", () => {
  let s = ready({ orders: [
    { order_id: "A", status_text: "접수됨", created_at: "2026-11-26T09:00:00Z" },
    { order_id: "B", status_text: "접수됨", created_at: "2026-11-26T10:00:00Z" }] });
  eq(orderView(s).map(o => o.orderId), ["B", "A"]);
  s = ready({ orders: [{ order_id: "A", status_text: "접수됨" }, { order_id: "B", status_text: "접수됨" }] });
  eq(orderView(s).map(o => o.orderId), ["B", "A"]);
});

test("customer: 409 · 모르는 상품 · 점검 거절", () => {
  let s = setQty(ready(), 1, 1);
  let a = beginSend(s);
  let r = applyResponse(a.state, res(409, { code: "IDEMPOTENCY_CONFLICT" }), a.body);
  eq(r.state.key, null); ok(r.state.reject.text.includes("IDEMPOTENCY_CONFLICT"));
  a = beginSend(setQty(ready(), 1, 1));
  r = applyResponse(a.state, res(503, { code: "MAINTENANCE" }), a.body);
  eq(r.state.reject.action, "maintenance"); ok(!canOrder(r.state));
  a = beginSend(setQty(ready(), 1, 1));
  r = applyResponse(a.state, res(422, { code: "UNKNOWN_PRODUCT" }), a.body);
  eq(r.state.reject.action, "reload");
});

test("customer: 상태 함수는 받은 상태를 고치지 않음", () => {
  const s = ready(); const before = JSON.stringify(s);
  setQty(s, 1, 3); beginSend(s); applyMessage(s, { type: "stock", data: { items: [] } });
  eq(JSON.stringify(s), before);
});

test("words: 고객 거절 문구에 내부 말이 없음", () => {
  const bad = /장애|보류|일시|마감|결품|배정|운반/;
  const names = { 1: "미니멘토스" };
  const cases = [
    [422, { code: "OUT_OF_STOCK", detail: { items: [{ product_id: 1, qty: 5, stock_qty: 2 }] } }],
    [422, { code: "OUT_OF_STOCK", detail: null }],
    [422, { code: "OVER_MAX_ITEMS", detail: { max_order_items: 8 } }],
    [422, { code: "OVER_MAX_ITEMS" }],
    [422, { code: "UNKNOWN_PRODUCT" }],
    [503, { code: "MAINTENANCE" }],
    [503, { code: "BUSY" }],
    [404, { code: "UNKNOWN_ACCOUNT" }],
    [400, { code: "BAD_REQUEST" }],
    [409, { code: "IDEMPOTENCY_CONFLICT" }],
    [500, null],
  ];
  for (const [st, body] of cases) {
    const t = rejectMessage(st, body, names).text;
    ok(!bad.test(t), `${st} ${body && body.code}: ${t}`);
  }
});

test("customer: BUSY 한 번 뒤 망 오류도 같은 키로 한 번 다시", () => {
  let s = setQty(ready(), 1, 1);
  let cur = beginSend(s); s = cur.state;
  let r = applyResponse(s, busy(), cur.body); s = r.state; eq(r.retryAfterMs, 1000);
  cur = beginSend(s); s = cur.state;
  r = applyResponse(s, net(), cur.body); s = r.state; eq(r.retryAfterMs, 0); ok(s.sending);
  const again = beginSend(s); eq(again.key, cur.key); s = again.state;
  r = applyResponse(s, net(), again.body);
  eq(r.retryAfterMs, null); ok(!r.state.sending);
});

test("customer: 망 오류 뒤에도 BUSY는 세 번까지", () => {
  let s = setQty(ready(), 1, 1);
  let cur = beginSend(s); s = cur.state;
  let r = applyResponse(s, net(), cur.body); s = r.state; eq(r.retryAfterMs, 0);
  const waits = [];
  for (let i = 0; i < 3; i++) {
    cur = beginSend(s); s = cur.state;
    r = applyResponse(s, busy(), cur.body); s = r.state; waits.push(r.retryAfterMs);
  }
  eq(waits, [1000, 1000, 1000]);
});

test("customer: created_at 없는 스냅샷이어도 방금 넣은 주문이 맨 위", () => {
  let s = setQty(ready({ orders: [{ order_id: "A", status_text: "접수됨" }, { order_id: "B", status_text: "접수됨" }] }), 1, 1);
  const a = beginSend(s);
  s = applyResponse(a.state, ok202("N"), a.body).state;
  eq(orderView(s).map(o => o.orderId), ["N", "B", "A"]);
});

// ---- 전체 검토 반영 ----
test("customer: 주문 본문에 account_id가 있음 (실제 _OrderBody 필수)", () => {
  const a = beginSend(setQty(ready(), 1, 2));
  eq(a.body.account_id, 1);
  eq(a.body.items, [{ product_id: 1, qty: 2 }]);
});

test("customer: UUID 대체 함수는 v4 모양", () => {
  const re = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  for (let i = 0; i < 20; i++) ok(re.test(makeUuid(null)), "대체 경로");
  ok(re.test(makeUuid({ getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = (i * 37 + 11) & 255; return a; } })));
  eq(makeUuid({ randomUUID: () => "fixed" }), "fixed");
});

test("customer: 점검 줄 문구는 한 곳(words.js)에서, 다른 kind는 서버 message", () => {
  eq(noticeText({ kind: "MAINTENANCE", message: "점검 중" }), MAINTENANCE_TEXT);
  eq(MAINTENANCE_TEXT, "점검 중입니다. 잠시 뒤 다시 주문해 주세요.");
  eq(noticeText({ kind: "INFO", message: "내일 휴무" }), "내일 휴무");
  eq(noticeText(null), "");
});

test("customer: 점검이 아닌 안내는 주문을 막지 않음", () => {
  const s = setQty(applyMessage(ready(), { type: "customer_notice", data: { kind: "INFO", message: "내일 휴무" } }).state, 1, 1);
  ok(canOrder(s));
  ok(!canOrder(applyMessage(s, { type: "customer_notice", data: { kind: "MAINTENANCE", message: "점검 중" } }).state));
});

test("customer: 202인데 본문이 비면 보냄 상태를 풀고 안내", () => {
  for (const b of [null, {}]) {
    const a = beginSend(setQty(ready(), 1, 1));
    const r = applyResponse(a.state, res(202, b), a.body);
    ok(!r.state.sending); ok(r.state.reject && r.state.reject.text.length > 0);
    eq(r.retryAfterMs, null);
  }
});

test("customer: limits 없는 스냅샷 경고는 한 번만", () => {
  const warn = console.warn; let warned = 0; console.warn = () => { warned++; };
  try {
    const data = { orders: [], stock: { items: [] }, notice: null };
    let s = applyMessage(initialState(1), { type: "snapshot", data }).state;
    const after1 = warned;
    applyMessage(s, { type: "snapshot", data }); applyMessage(s, { type: "snapshot", data });
    eq(warned, after1);
  } finally { console.warn = warn; }
});
