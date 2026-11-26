// ---- admin/state.js 시험 ----
import { test, eq, ok } from "./harness.js";
import { initialState, applyMessage, columns, robotCards, stockLine } from "../admin/state.js";
import { SCENES } from "../common/demo_scenes.js";

const P = [
  { product_id: 1, name: "미니멘토스", stock_qty: 12 },
  { product_id: 2, name: "과즙팡", stock_qty: 5 },
];
const trip = (trip_id, order_id, status, robot_id = null) =>
  ({ trip_id, order_id, status, robot_id, items: [{ product_id: 1, qty_ordered: 2, qty_placed: 0, qty_missing: 0 }] });
const snap = (data) => ({ type: "snapshot", data: { orders: [], trips: [], robots: [], work_state: { system_mode: "NORMAL" }, stock: { items: P }, ...data } });
const ids = (cards) => cards.map(c => c.tripId);

test("admin: 칸 나누기", () => {
  const m = SCENES.many.init("admin");
  const s = applyMessage(initialState(), { type: "snapshot", data: m.data });
  const c = columns(s);
  eq(c.action.map(x => x.statusText), ["담기 일시 중지", "주문 처리 보류"]);
  eq(c.done.map(x => x.statusText), ["출고됨"]);
  eq(c.received.length, 1); eq(c.progress.length, 1);
  eq(s.mode, "NORMAL");
});

test("admin: trip_changed로 칸 옮김", () => {
  let s = applyMessage(initialState(), snap({
    orders: [{ order_id: "O-1", status: "RECEIVED", created_at: "2026-11-26T09:00:00+09:00" }],
    trips: [trip("O-1-1", "O-1", "RECEIVED")],
  }));
  eq(ids(columns(s).received), ["O-1-1"]);
  s = applyMessage(s, { type: "trip_changed", data: trip("O-1-1", "O-1", "ASSIGNED", "pode1") });
  eq(columns(s).received.length, 0);
  eq(columns(s).progress[0].robotId, "pode1");
  eq(columns(s).progress[0].statusText, "배정됨");
});

test("admin: 운반 건 없는 주문도 카드", () => {
  const s = applyMessage(initialState(), snap({
    orders: [{ order_id: "O-9", status: "RECEIVED", created_at: "x", items: [{ product_id: 2, qty: 3 }] }],
  }));
  const c = columns(s).received;
  eq(c.length, 1); eq(c[0].tripId, null); eq(c[0].orderId, "O-9");
  ok(c[0].itemsText.includes("과즙팡"));
  const s2 = applyMessage(s, { type: "order_changed", data: { order_id: "O-9", status: "CANCELLED" } });
  eq(columns(s2).done.length, 1);
});

test("admin: 모르는 상태 값은 진행 중 칸에 원래 값으로", () => {
  const s = applyMessage(initialState(), snap({ orders: [{ order_id: "O-1", status: "NEW" }], trips: [trip("O-1-1", "O-1", "NEW")] }));
  eq(columns(s).progress.map(c => c.statusText), ["NEW"]);
});

test("admin: 주문 시각 · 운반 번호 순", () => {
  const s = applyMessage(initialState(), snap({
    orders: [{ order_id: "B", status: "X", created_at: "2" }, { order_id: "A", status: "X", created_at: "1" }],
    trips: [trip("B-1", "B", "FILLING"), trip("A-2", "A", "FILLING"), trip("A-1", "A", "FILLING")],
  }));
  eq(ids(columns(s).progress), ["A-1", "A-2", "B-1"]);
});

test("admin: robots 0대면 빈 목록", () => eq(robotCards(initialState()), []));

test("admin: 오류 로봇 카드", () => {
  let s = applyMessage(initialState(), snap({ robots: [
    { robot_id: "pode1", fsm_state: "IDLE", fault: "NONE", battery: 82, trip_id: "" },
    { robot_id: "pode2", fsm_state: "MOVING", fault: "ERROR", battery: 30, trip_id: "O-1-1" },
  ] }));
  const r = robotCards(s);
  eq(r[0], { robotId: "pode1", text: "대기", tone: "ok", sub: "배터리 82%" });
  eq(r[1].tone, "bad"); eq(r[1].sub, "O-1-1"); eq(r[1].text, "오류");
  s = applyMessage(s, { type: "robots", data: { robots: [] } });
  eq(robotCards(s), []);
});

test("admin: 재고 한 줄", () => {
  let s = applyMessage(initialState(), snap({}));
  eq(stockLine(s), "미니멘토스 12 · 과즙팡 5");
  s = applyMessage(s, { type: "stock", data: { items: [{ product_id: 7, stock_qty: 1 }] } });
  eq(stockLine(s), "상품 7 1");
});
