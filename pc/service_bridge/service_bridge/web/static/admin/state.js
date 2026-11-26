// ---- 관리자 화면 상태 계산 (순수 함수: 받은 상태를 고치지 않음) ----
import { adminStatusText, adminColumn, robotWord, productName } from "../common/words.js";

// ---- 처음 상태 ----
export function initialState() {
  return { orders: {}, trips: {}, robots: [], mode: null, products: [] };
}

const byId = (list, key) => Object.fromEntries((list || []).map(x => [x[key], x]));

// ---- 서버 메시지 반영 ----
export function applyMessage(s, msg) {
  const d = msg.data || {};
  switch (msg.type) {
    case "snapshot":
      return {
        orders: byId(d.orders, "order_id"), trips: byId(d.trips, "trip_id"),
        robots: d.robots || [], mode: d.work_state ? d.work_state.system_mode : null,
        products: (d.stock && d.stock.items) || [],
      };
    case "order_changed": {
      const old = s.orders[d.order_id] || { order_id: d.order_id };
      return { ...s, orders: { ...s.orders, [d.order_id]: { ...old, status: d.status } } };
    }
    case "trip_changed":
      return { ...s, trips: { ...s.trips, [d.trip_id]: d } };
    case "robots":
      return { ...s, robots: d.robots || [] };
    case "stock":
      return { ...s, products: d.items || [] };
    default:
      return s;
  }
}

// ---- 품목 글: "이름 ×수량" ----
function itemsText(s, items, qtyKey) {
  const name = (id) => {
    const p = s.products.find(x => x.product_id === id);
    return productName(p || { product_id: id });
  };
  return (items || []).map(it => `${name(it.product_id)} ×${it[qtyKey]}`).join(" · ");
}

// ---- 칸 나누기: 운반 건 기준, 주문 시각 · 운반 번호 순. 운반 건 없는 주문은 주문 상태로 카드 하나 ----
export function columns(s) {
  const rows = [];
  const withTrip = new Set();
  for (const t of Object.values(s.trips)) {
    withTrip.add(t.order_id);
    const o = s.orders[t.order_id];
    rows.push({
      at: (o && o.created_at) || "", key: t.trip_id,
      status: t.status,
      card: { tripId: t.trip_id, orderId: t.order_id, statusText: adminStatusText(t.status), robotId: t.robot_id || null, itemsText: itemsText(s, t.items, "qty_ordered") },
    });
  }
  for (const o of Object.values(s.orders)) {
    if (withTrip.has(o.order_id)) continue;
    rows.push({
      at: o.created_at || "", key: o.order_id, status: o.status,
      card: { tripId: null, orderId: o.order_id, statusText: adminStatusText(o.status), robotId: null, itemsText: itemsText(s, o.items, "qty") },
    });
  }
  rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const out = { action: [], progress: [], received: [], done: [] };
  for (const r of rows) out[adminColumn(r.status)].push(r.card);
  return out;
}

// ---- 로봇 카드: 받은 로봇을 모두 그림. 보조 줄은 운반 건 번호, 없으면 배터리 ----
export function robotCards(s) {
  return s.robots.map(r => {
    const w = robotWord(r);
    return { robotId: r.robot_id, text: w.text, tone: w.tone, sub: r.trip_id ? r.trip_id : `배터리 ${r.battery}%` };
  });
}

// ---- 재고 한 줄 ----
export function stockLine(s) {
  return s.products.map(p => `${productName(p)} ${p.stock_qty}`).join(" · ");
}
