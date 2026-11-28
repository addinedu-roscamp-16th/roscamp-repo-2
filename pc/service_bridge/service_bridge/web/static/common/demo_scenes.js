// ---- 가짜 데이터 장면 일곱 개 (설계 4.3). 화면마다 따로 돈다 ----

// ---- 상품 셋 (스토리보드의 세 상품) ----
const PRODUCTS = [
  { product_id: 1, kind: "mentos", name: "미니멘토스 레인보우", stock_qty: 12 },
  { product_id: 2, kind: "mygummy", name: "과즙팡 비타민 마이구미", stock_qty: 5 },
  { product_id: 3, kind: "choco_bar", name: "자유시간 Mini", stock_qty: 0 },
];
const FLOW = ["접수됨", "준비 중", "출고 중", "출고 완료"];       // 고객 상태 말
const ADMIN_FLOW = ["RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING", "SHIPPED"];
const STEP_MS = 1500;

const clone = (x) => JSON.parse(JSON.stringify(x));
const orderNo = (n) => `O-20261126-${String(n).padStart(4, "0")}`;
const at = (hhmm) => `2026-11-26T${hhmm}:00+09:00`;

// ---- 응답 모양: 헤더는 대소문자 가리지 않고 get ----
function reply(status, body, headers = {}) {
  const h = {};
  for (const k of Object.keys(headers)) h[k.toLowerCase()] = String(headers[k]);
  return { status, body, headers: { get: (name) => (Object.hasOwn(h, String(name).toLowerCase()) ? h[String(name).toLowerCase()] : null) } };
}
const reject = (status, code, message, detail = null, headers = {}) => reply(status, { code, message, detail }, headers);

// ---- 장면 모델: data는 스냅샷 data 그대로, 나머지는 대본이 쓰는 번호표 ----
function customerModel() {
  return {
    data: {
      orders: [{ order_id: orderNo(1), status_text: "출고 완료", created_at: at("09:10"), items: [{ product_id: 2, qty: 1 }] }],
      stock: { items: clone(PRODUCTS) },
      notice: null,
      limits: { max_order_items: 8 },
    },
    nextNo: 2,
  };
}
// 운반 건 번호는 실제와 같은 문자열 "<주문 번호>-<순번>"
const tripNo = (order_id, seq) => `${order_id}-${seq}`;
const trip = (trip_id, order_id, status, robot_id, items) => ({
  trip_id, order_id, status, robot_id,
  items: items.map(([product_id, q, placed]) => ({ product_id, qty_ordered: q, qty_placed: placed, qty_missing: 0 })),
});
const robot = (robot_id, fsm_state, fault, battery, x, y, trip_id) => ({ robot_id, fsm_state, fault, battery, x, y, trip_id });

function adminModel() {
  return {
    data: {
      orders: [{ order_id: orderNo(1), status: "SHIPPED", created_at: at("09:10"), items: [{ product_id: 2, qty: 1 }] }],
      trips: [trip(tripNo(orderNo(1), 1), orderNo(1), "SHIPPED", "pode1", [[2, 1, 1]])],
      robots: [robot("pode1", "IDLE", "NONE", 86, 1.2, 0.8, ""), robot("pode2", "IDLE", "NONE", 92, 2.4, 0.8, "")],
      work_state: { system_mode: "NORMAL", central: "RUNNING" },
      stock: { items: clone(PRODUCTS) },
    },
    nextNo: 2,
  };
}

// ---- 고객: 주문을 받아 들이고 접수됨 → 출고 완료까지 1.5초 간격 ----
// first: 처음 보낼 단계(race는 접수됨을 먼저 보내서 1), stockDelay: 202 뒤 stock까지 ms
function acceptOrder(ctx, body, { first = 0, stockDelay = 0 } = {}) {
  const m = ctx.model, d = m.data;
  const order_id = orderNo(m.nextNo++);
  const items = ((body && body.items) || []).map(i => ({ product_id: i.product_id, qty: i.qty }));
  const order = { order_id, status_text: FLOW[0], created_at: new Date(ctx.now()).toISOString(), items };
  d.orders.push(order);
  // 재고는 주문 때 줄지 않고 출고 때 준다(이번 주 백엔드는 바꾸지 않음): stock 메시지는 같은 값을 다시 보냄
  const trip_ids = [tripNo(order_id, 1)];
  for (let i = first; i < FLOW.length; i++) {
    ctx.after(STEP_MS * (i - first + 1), () => {
      order.status_text = FLOW[i];
      ctx.emit("order_changed", { order_id, status_text: FLOW[i] });
    });
  }
  ctx.after(stockDelay, () => ctx.emit("stock", clone(d.stock)));
  return { order, response: reply(202, { order_id, status: "RECEIVED", trip_ids }) };
}

// ---- 관리자: 3초 뒤 주문이 들어와 운반 건 · Pode 카드가 1.5초 간격으로 바뀌고, 끝나면 5초 뒤 되풀이 ----
function adminLoop(ctx) {
  const m = ctx.model, d = m.data;
  const run = () => {
    const order_id = orderNo(m.nextNo++), trip_id = tripNo(order_id, 1);
    const items = [[1, 2], [2, 1]];
    const order = { order_id, status: "RECEIVED", created_at: new Date(ctx.now()).toISOString(), items: items.map(([product_id, qty]) => ({ product_id, qty })) };
    const t = trip(trip_id, order_id, "RECEIVED", null, items.map(([p, q]) => [p, q, 0]));
    const pode = d.robots[0];
    d.orders.push(order); d.trips.push(t);
    ADMIN_FLOW.forEach((status, i) => ctx.after(STEP_MS * i, () => {
      order.status = t.status = status;
      if (i >= 1) t.robot_id = "pode1";
      if (status === "TRANSPORTING" || status === "SHIPPED") t.items.forEach(it => { it.qty_placed = it.qty_ordered; });
      Object.assign(pode, [
        { fsm_state: "IDLE", trip_id: "" },
        { fsm_state: "MOVING", trip_id, x: 1.8 },
        { fsm_state: "DOCKED", trip_id, x: 2.6, battery: pode.battery - 1 },
        { fsm_state: "MOVING", trip_id, x: 3.4, battery: pode.battery - 2 },
        { fsm_state: "IDLE", trip_id: "", x: 1.2 },
      ][i]);
      ctx.emit("order_changed", { order_id, status });
      ctx.emit("trip_changed", clone(t));
      ctx.emit("robots", { robots: clone(d.robots) });
      if (i === ADMIN_FLOW.length - 1) ctx.after(5000, run);
    }));
  };
  ctx.after(3000, run);
}

// ---- many: 고객 주문 다섯 개(말 다섯 가지) ----
function manyCustomer() {
  const m = customerModel(), o = m.data.orders;
  const mk = (n, status_text, h, items) => ({ order_id: orderNo(n), status_text, created_at: at(h), items });
  o.length = 0;
  o.push(
    mk(1, "취소됨", "08:30", [{ product_id: 3, qty: 1 }]),
    mk(2, "일부 출고", "08:50", [{ product_id: 1, qty: 3 }, { product_id: 2, qty: 2 }]),
    mk(3, "출고 완료", "09:10", [{ product_id: 2, qty: 1 }]),
    mk(4, "출고 중", "09:40", [{ product_id: 1, qty: 2 }]),
    mk(5, "준비 중", "10:00", [{ product_id: 1, qty: 1 }, { product_id: 2, qty: 1 }]),
  );
  m.nextNo = 6;
  return m;
}

// ---- many: 관리자 주문 네 개 · 운반 건 다섯 개 (8개 주문 → 운반 건 2개, 각 4개), Pode 하나 오류 ----
function manyAdmin() {
  const m = adminModel(), d = m.data;
  const od = (n, status, h, items) => ({ order_id: orderNo(n), status, created_at: at(h), items: items.map(([product_id, qty]) => ({ product_id, qty })) });
  d.orders = [
    od(1, "FILLING_PAUSE", "09:00", [[1, 5], [2, 3]]),
    od(2, "HELD", "09:20", [[2, 2]]),
    od(3, "TRANSPORTING", "09:40", [[1, 2]]),
    od(4, "SHIPPED", "08:40", [[2, 1]]),
  ];
  d.trips = [
    trip(tripNo(orderNo(1), 1), orderNo(1), "FILLING_PAUSE", "pode1", [[1, 4, 2]]),
    trip(tripNo(orderNo(1), 2), orderNo(1), "RECEIVED", null, [[1, 1, 0], [2, 3, 0]]),
    trip(tripNo(orderNo(2), 1), orderNo(2), "HELD", null, [[2, 2, 0]]),
    trip(tripNo(orderNo(3), 1), orderNo(3), "TRANSPORTING", "pode2", [[1, 2, 2]]),
    trip(tripNo(orderNo(4), 1), orderNo(4), "SHIPPED", "pode2", [[2, 1, 1]]),
  ];
  d.robots = [
    robot("pode1", "DOCKED", "NONE", 74, 2.6, 0.8, tripNo(orderNo(1), 1)),
    robot("pode2", "MOVING", "NONE", 58, 3.4, 1.5, tripNo(orderNo(3), 1)),
    robot("pode3", "ERROR", "ERROR", 31, 0.6, 2.0, ""),
  ];
  m.nextNo = 5;
  return m;
}

// ---- 장면 표: views(어느 화면에서 도는가), init(view) → 모델, start(ctx), placeOrder(ctx, body, key) ----
const normalStart = (ctx) => { if (ctx.view === "admin") adminLoop(ctx); };
const normalOrder = (ctx, body) => acceptOrder(ctx, body).response;

export const SCENES = {
  normal: {
    views: ["customer", "admin"],
    init: (view) => (view === "admin" ? adminModel() : customerModel()),
    start: normalStart,
    placeOrder: async (ctx, body) => normalOrder(ctx, body),
  },
  // 첫 주문은 재고 부족으로 거절, 그 뒤 재고는 2
  stock: {
    views: ["customer"],
    init: () => customerModel(),
    start: () => {},
    placeOrder: async (ctx, body) => {
      if (!ctx.scratch.rejected) {
        ctx.scratch.rejected = true;
        const want = ((body && body.items) || []).find(i => i.product_id === 1);
        ctx.model.data.stock.items.find(s => s.product_id === 1).stock_qty = 2;
        ctx.after(0, () => ctx.emit("stock", clone(ctx.model.data.stock)));
        return reject(422, "OUT_OF_STOCK", "재고가 부족합니다.", { items: [{ product_id: 1, qty: want ? want.qty : 3, stock_qty: 2 }] });
      }
      return normalOrder(ctx, body);
    },
  },
  // 같은 키 두 번은 503 BUSY, 세 번째 202
  busy: {
    views: ["customer"],
    init: () => customerModel(),
    start: () => {},
    placeOrder: async (ctx, body, key) => {
      const n = ctx.scratch[key] = (ctx.scratch[key] || 0) + 1;
      console.info(`[가짜 데이터] 주문 키 ${key} (${n}번째 보냄)`);
      if (n <= 2) return reject(503, "BUSY", "주문이 몰려 있습니다.", null, { "Retry-After": "1" });
      delete ctx.scratch[key];
      return normalOrder(ctx, body);
    },
  },
  // 점검 안내, 주문은 503 MAINTENANCE, 관리자는 시험 모드
  maintenance: {
    views: ["customer", "admin"],
    init: (view) => {
      const m = view === "admin" ? adminModel() : customerModel();
      if (view === "admin") m.data.work_state.system_mode = "TEST";
      else m.data.notice = { kind: "MAINTENANCE", message: "점검 중" };
      return m;
    },
    notice: true,
    start: normalStart,
    placeOrder: async () => reject(503, "MAINTENANCE", "점검 중입니다."),
  },
  // normal처럼 가다가 두 번째 order_changed 뒤 끊김, 3초 뒤 다시 붙고 스냅샷
  reconnect: {
    views: ["customer", "admin"],
    init: (view) => (view === "admin" ? adminModel() : customerModel()),
    dropAfter: 2, dropMs: 3000,
    start: normalStart,
    placeOrder: async (ctx, body) => normalOrder(ctx, body),
  },
  // order_changed(접수됨)가 202보다 0.5초 먼저
  race: {
    views: ["customer"],
    init: () => customerModel(),
    start: () => {},
    placeOrder: (ctx, body) => {
      const { order, response } = acceptOrder(ctx, body, { first: 1, stockDelay: 500 });
      ctx.emit("order_changed", { order_id: order.order_id, status_text: FLOW[0] });
      return new Promise(res => ctx.after(500, () => res(response)));
    },
  },
  // 주문 · 운반 건 여러 개
  many: {
    views: ["customer", "admin"],
    init: (view) => (view === "admin" ? manyAdmin() : manyCustomer()),
    start: () => {},
    placeOrder: async (ctx, body) => normalOrder(ctx, body),
  },
};
