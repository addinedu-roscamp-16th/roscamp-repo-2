// ---- 고객 화면 상태 계산 (순수 함수: 화면 · 네트워크를 모르고, 받은 상태를 고치지 않음) ----
import { productName, progressIndex, rejectMessage } from "../common/words.js";

const DEFAULT_MAX_ITEMS = 8;
const RELOAD_WAIT_MS = 2000;
const MAX_BUSY_RETRIES = 3;
const MAX_NET_RETRIES = 1;

// ---- 처음 상태 ----
export function initialState(accountId) {
  return {
    accountId, products: [], orders: [], cart: {}, key: null, sending: false,
    online: false, notice: null, reject: null, maxItems: DEFAULT_MAX_ITEMS,
    query: "", knownItems: {}, pendingChanges: {}, busyAttempts: 0, netAttempts: 0,
  };
}

// ---- 도우미 ----
const clone = (o) => JSON.parse(JSON.stringify(o));
const namesOf = (s) => Object.fromEntries(s.products.map(p => [p.product_id, productName(p)]));
const stockOf = (s, id) => { const p = s.products.find(x => x.product_id === id); return p ? p.stock_qty : 0; };
const sumCart = (cart) => Object.values(cart).reduce((a, b) => a + b, 0);

// 고른 수량을 재고 안으로 낮춤. 하나라도 바뀌면 changed
function clampCart(s) {
  const cart = {}; let changed = false;
  for (const [id, q] of Object.entries(s.cart)) {
    const n = Math.min(q, stockOf(s, Number(id)));
    if (n !== q) changed = true;
    if (n > 0) cart[id] = n;
  }
  return changed ? { ...s, cart, key: null } : s;
}

function normalizeProducts(items) {
  return (items || []).map(p => ({ product_id: p.product_id, kind: p.kind, name: p.name, stock_qty: p.stock_qty }));
}

function noticeOf(data) {
  return data && data.message ? { kind: data.kind, message: data.message } : null;
}

// ---- 서버 메시지 반영 ----
export function applyMessage(s, msg) {
  const data = msg.data;
  if (msg.type === "snapshot") {
    let maxItems = DEFAULT_MAX_ITEMS;
    if (data.limits && data.limits.max_order_items != null) maxItems = data.limits.max_order_items;
    else console.warn("스냅샷에 limits가 없어 한 주문 최대 수량을 8로 둡니다.");
    const orders = (data.orders || []).map(o => ({
      order_id: o.order_id, status_text: o.status_text, created_at: o.created_at,
      items: o.items || s.knownItems[o.order_id] || [],
    }));
    const pendingChanges = { ...s.pendingChanges };
    for (const o of orders) delete pendingChanges[o.order_id];
    const next = clampCart({
      ...s, orders, maxItems, pendingChanges, notice: noticeOf(data.notice),
      products: normalizeProducts(data.stock && data.stock.items),
    });
    return { state: next, effects: [] };
  }
  if (msg.type === "order_changed") {
    if (s.orders.some(o => o.order_id === data.order_id)) {
      const orders = s.orders.map(o => o.order_id === data.order_id ? { ...o, status_text: data.status_text } : o);
      return { state: { ...s, orders }, effects: [] };
    }
    const first = !Object.hasOwn(s.pendingChanges, data.order_id);
    return {
      state: { ...s, pendingChanges: { ...s.pendingChanges, [data.order_id]: data.status_text } },
      effects: first ? [{ kind: "reload-after", ms: RELOAD_WAIT_MS, orderId: data.order_id }] : [],
    };
  }
  if (msg.type === "stock") {
    const products = s.products.map(p => ({ ...p }));
    for (const it of (data.items || [])) {
      const p = products.find(x => x.product_id === it.product_id);
      if (p) { p.stock_qty = it.stock_qty; if (it.name != null) p.name = it.name; if (it.kind != null) p.kind = it.kind; }
      else products.push(normalizeProducts([it])[0]);
    }
    return { state: clampCart({ ...s, products }), effects: [] };
  }
  if (msg.type === "customer_notice") {
    return { state: { ...s, notice: noticeOf(data) }, effects: [] };
  }
  return { state: s, effects: [] };
}

// ---- 고르기 ----
export function setQty(s, productId, qty) {
  const p = s.products.find(x => x.product_id === productId);
  if (!p) return s;
  const others = sumCart(s.cart) - (s.cart[productId] || 0);
  const limit = Math.max(0, Math.min(p.stock_qty, s.maxItems - others));
  const n = Math.max(0, Math.min(Math.trunc(Number(qty)) || 0, limit));
  if (n === (s.cart[productId] || 0)) return s;
  const cart = { ...s.cart };
  if (n > 0) cart[productId] = n; else delete cart[productId];
  return { ...s, cart, key: null };
}

export function setOnline(s, online) { return { ...s, online: !!online }; }

export function cartTotal(s) { return sumCart(s.cart); }

export function canOrder(s) {
  return cartTotal(s) > 0 && !s.sending && !s.notice && s.online;
}

// ---- 보내기 ----
// 새로 보내기면 시도 횟수를 지우고, 이미 보내는 중(다시 보내기)이면 이어 센다.
export function beginSend(s) {
  const key = s.key || crypto.randomUUID();
  const body = { items: Object.entries(s.cart).map(([id, qty]) => ({ product_id: Number(id), qty })) };
  const fresh = !s.sending;
  const state = { ...s, key, sending: true, reject: null,
    busyAttempts: fresh ? 0 : s.busyAttempts, netAttempts: fresh ? 0 : s.netAttempts };
  return { state, key, body };
}

export function applyResponse(s, resp, sentBody) {
  const status = resp.status, body = resp.body;
  if (status === 202) {
    const items = clone(sentBody.items);
    const id = body.order_id;
    const known = s.orders.find(o => o.order_id === id);
    const statusText = Object.hasOwn(s.pendingChanges, id) ? s.pendingChanges[id] : (known ? known.status_text : "접수됨");
    const line = { order_id: id, status_text: statusText, created_at: known && known.created_at ? known.created_at : new Date().toISOString(), items };
    const orders = known ? s.orders.map(o => o.order_id === id ? line : o) : [line, ...s.orders];
    const pendingChanges = { ...s.pendingChanges }; delete pendingChanges[id];
    return {
      state: { ...s, orders, pendingChanges, knownItems: { ...s.knownItems, [id]: items },
               cart: {}, key: null, sending: false, busyAttempts: 0, netAttempts: 0, reject: null },
      retryAfterMs: null,
    };
  }
  if (status === 503 && body && body.code === "BUSY" && s.busyAttempts < MAX_BUSY_RETRIES) {
    const busyAttempts = s.busyAttempts + 1;
    const ra = Number(resp.headers && resp.headers.get && resp.headers.get("Retry-After"));
    return { state: { ...s, busyAttempts, reject: { text: "잠시 기다려 주세요.", action: null } },
             retryAfterMs: ra > 0 ? ra * 1000 : 1000 };
  }
  if (status === 0) {
    if (s.netAttempts < MAX_NET_RETRIES) return { state: { ...s, netAttempts: s.netAttempts + 1 }, retryAfterMs: 0 };
    return { state: { ...s, busyAttempts: 0, netAttempts: 0, sending: false, reject: { text: "주문이 접수됐는지 확인 중입니다.", action: "reload" } },
             retryAfterMs: null };
  }
  // 그 밖의 거절: 안내 문구를 채우고 버튼을 푼다
  const reject = rejectMessage(status, body, namesOf(s));
  let next = { ...s, busyAttempts: 0, netAttempts: 0, sending: false, reject };
  if (reject.action === "lower") {
    const products = next.products.map(p => ({ ...p }));
    const cart = { ...next.cart };
    for (const it of ((body.detail && body.detail.items) || [])) {
      const p = products.find(x => x.product_id === it.product_id);
      if (p) p.stock_qty = it.stock_qty;
      const n = Math.min(cart[it.product_id] || 0, it.stock_qty);
      if (n > 0) cart[it.product_id] = n; else delete cart[it.product_id];
    }
    next = { ...next, products, cart, key: null };
  } else if (status === 409) {
    next = { ...next, key: null };
  } else if (reject.action === "maintenance") {
    next = { ...next, notice: { kind: "maintenance", message: reject.text } };
  }
  return { state: next, retryAfterMs: null };
}

// ---- 화면용 계산 ----
export function orderView(s) {
  const names = namesOf(s);
  // created_at 있는 주문이 먼저(최신 순, Date.parse), 없는 주문은 그 뒤에 받은 순서 거꾸로
  const time = (o) => Date.parse(o.created_at);
  const dated = s.orders.filter(o => !Number.isNaN(time(o)));
  const undated = s.orders.filter(o => Number.isNaN(time(o))).reverse();
  const ordered = [...dated].sort((a, b) => time(b) - time(a)).concat(undated);
  return ordered.map(o => ({
    orderId: o.order_id,
    statusText: o.status_text,
    step: progressIndex(o.status_text),
    itemsText: (o.items || []).map(it => `${names[it.product_id] || productName({ product_id: it.product_id })} ${it.qty}`).join(" · "),
  }));
}

export function productView(s) {
  const total = sumCart(s.cart);
  return s.products
    .map(p => {
      const qty = s.cart[p.product_id] || 0;
      return {
        productId: p.product_id, name: productName(p), kind: p.kind, stock: p.stock_qty,
        soldOut: p.stock_qty <= 0, qty,
        canInc: qty < p.stock_qty && total < s.maxItems, canDec: qty > 0,
      };
    })
    .filter(v => v.name.includes(s.query));
}
