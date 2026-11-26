// ---- common/demo.js · demo_scenes.js 시험 ----
import { test, eq, ok } from "./harness.js";
import { createDemo } from "../common/demo.js";
import { SCENES } from "../common/demo_scenes.js";

// ---- 가짜 시계: setTimeout을 모아 두고 advance(ms)로 시간 순서대로 돌림 ----
function fakeClock() {
  let t = 0, id = 0, q = [];
  return {
    setTimeout(fn, ms) { q.push({ id: ++id, at: t + (ms || 0), fn }); return id; },
    now: () => t,
    advance(ms) {
      const end = t + ms;
      for (;;) {
        q.sort((a, b) => a.at - b.at || a.id - b.id);
        if (!q.length || q[0].at > end) break;
        const x = q.shift(); t = x.at; x.fn();
      }
      t = end;
    },
  };
}
function open(view, scene) {
  const clock = fakeClock(), msgs = [], states = [];
  const src = createDemo({ view, scene }, clock);
  src.onMessage(m => msgs.push(m));
  src.onState(s => states.push(s));
  clock.advance(0);
  return { clock, msgs, states, src };
}
const tick = () => new Promise(r => setTimeout(r, 0));

test("demo: 첫 메시지는 snapshot", async () => {
  const d = open("customer", "normal");
  eq(d.msgs[0].type, "snapshot");
  eq(d.msgs[0].data.limits.max_order_items, 8);
  eq(d.msgs[0].data.stock.items.map(i => i.name), ["미니멘토스 레인보우", "과즙팡 비타민 마이구미", "자유시간 Mini"]);
  eq(d.msgs[0].data.orders.length, 1);
  eq(d.msgs[0].data.orders[0].status_text, "출고 완료");
  eq(d.states[0], "open");
});
test("demo: 관리자 normal은 3초 뒤 주문이 들어와 SHIPPED까지", async () => {
  const d = open("admin", "normal");
  eq(d.msgs[0].data.work_state.system_mode, "NORMAL");
  d.clock.advance(2999); eq(d.msgs.filter(m => m.type === "trip_changed").length, 0);
  d.clock.advance(1 + 1500 * 5 + 10);
  const st = d.msgs.filter(m => m.type === "trip_changed").map(m => m.data.status);
  eq(st, ["RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING", "SHIPPED"]);
  ok(d.msgs.some(m => m.type === "robots" && m.data.robots[0].robot_id === "pode1"));
  d.clock.advance(5000 + 100);   // 5초 뒤 다음 주문
  ok(d.msgs.filter(m => m.type === "order_changed").some(m => m.data.order_id === "O-20261126-0003"));
});
test("demo: normal 주문 흐름", async () => {
  const d = open("customer", "normal");
  const r = await d.src.placeOrder({ items: [{ product_id: 1, qty: 2 }] }, "k1");
  eq(r.status, 202); ok(r.body.order_id.startsWith("O-20261126-"));
  eq(r.body.status, "RECEIVED");
  d.clock.advance(6000);
  const ev = d.msgs.filter(m => m.type === "order_changed");
  eq(ev.map(m => m.data.status_text), ["접수됨", "준비 중", "출고 중", "출고 완료"]);
  eq(ev.map(m => m.seq), [1, 2, 3, 4].map(n => ev[0].seq + n - 1));
  const stock = d.msgs.find(m => m.type === "stock");
  eq(stock.data.items.find(i => i.product_id === 1).stock_qty, 10);
});
test("demo: stock 장면은 첫 주문을 OUT_OF_STOCK으로 거절", async () => {
  const d = open("customer", "stock");
  const r = await d.src.placeOrder({ items: [{ product_id: 1, qty: 5 }] }, "k1");
  eq(r.status, 422); eq(r.body.code, "OUT_OF_STOCK");
  eq(r.body.detail, { items: [{ product_id: 1, qty: 5, stock_qty: 2 }] });
  d.clock.advance(0);
  eq(d.msgs.find(m => m.type === "stock").data.items.find(i => i.product_id === 1).stock_qty, 2);
  eq((await d.src.placeOrder({ items: [{ product_id: 1, qty: 2 }] }, "k2")).status, 202);
});
test("demo: busy는 같은 키로 세 번째에 202", async () => {
  const d = open("customer", "busy");
  const orig = console.info, logged = [];
  console.info = (...a) => logged.push(a.join(" "));
  try {
    const a = await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "kk");
    const b = await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "kk");
    const c = await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "kk");
    eq([a.status, b.status, c.status], [503, 503, 202]);
    eq(a.body.code, "BUSY"); eq(a.headers.get("Retry-After"), "1");
    eq(c.headers.get("Retry-After"), null);
  } finally { console.info = orig; }
  eq(logged.length, 3); ok(logged.every(l => l.includes("kk")));
});
test("demo: maintenance", async () => {
  const d = open("customer", "maintenance");
  eq(d.msgs[0].data.notice.kind, "MAINTENANCE");
  eq(d.msgs[1].type, "customer_notice");
  eq(d.msgs[1].data, { kind: "MAINTENANCE", message: "점검 중" });
  const r = await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k");
  eq(r.status, 503); eq(r.body.code, "MAINTENANCE");
  eq(open("admin", "maintenance").msgs[0].data.work_state.system_mode, "TEST");
});
test("demo: reconnect는 두 번째 사건 뒤 끊겼다가 3초 뒤 스냅샷", async () => {
  const d = open("customer", "reconnect");
  await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k");
  d.clock.advance(3000);      // 접수됨, 준비 중
  eq(d.states, ["open", "reconnecting"]);
  const before = d.msgs.length;
  d.clock.advance(2999); eq(d.states.length, 2); eq(d.msgs.length, before);
  d.clock.advance(1);
  eq(d.states, ["open", "reconnecting", "open"]);
  eq(d.msgs[d.msgs.length - 1].type, "snapshot");
  eq(d.msgs[d.msgs.length - 1].data.orders[1].status_text, "출고 완료");
  // 끊긴 동안의 사건은 나가지 않고 스냅샷에만 반영됨
  eq(d.msgs.filter(m => m.type === "order_changed").map(m => m.data.status_text), ["접수됨", "준비 중"]);
});
test("demo: reconnect 관리자도 끊겼다 이어짐", async () => {
  const d = open("admin", "reconnect");
  d.clock.advance(20000);
  ok(d.states.includes("reconnecting")); eq(d.states[d.states.length - 1], "open");
  eq(d.msgs.filter(m => m.type === "snapshot").length, 2);
});
test("demo: race는 order_changed가 202보다 먼저", async () => {
  const d = open("customer", "race");
  const order = [];
  d.src.onMessage(m => { if (m.type === "order_changed") order.push("evt"); });
  const p = d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k").then(r => { order.push("202"); return r; });
  await tick(); d.clock.advance(0); await tick();
  eq(order, ["evt"]);
  d.clock.advance(499); await tick(); eq(order, ["evt"]);
  d.clock.advance(1); const r = await p;
  eq(order, ["evt", "202"]); eq(r.status, 202);
  eq(d.msgs.filter(m => m.type === "order_changed")[0].data.order_id, r.body.order_id);
});
test("demo: many 고객", async () => {
  const d = open("customer", "many");
  const o = d.msgs[0].data.orders;
  eq(o.length, 5);
  eq(new Set(o.map(x => x.status_text)).size, 5);
  ok(o.some(x => x.status_text === "취소됨") && o.some(x => x.status_text.includes("출고")));
});
test("demo: many 관리자", async () => {
  const d = open("admin", "many");
  const dd = d.msgs[0].data;
  eq(dd.orders.length, 4); eq(dd.trips.length, 5);
  const big = dd.orders.map(od => dd.trips.filter(t => t.order_id === od.order_id).length);
  ok(big.includes(2));
  const bigOrder = dd.trips.filter(t => t.order_id === dd.orders[big.indexOf(2)].order_id);
  eq(bigOrder.flatMap(t => t.items).reduce((s, i) => s + i.qty_ordered, 0), 8);
  ok(dd.trips.some(t => t.status === "FILLING_PAUSE") && dd.trips.some(t => t.status === "HELD"));
  eq(dd.robots.length, 3); eq(dd.robots.filter(r => r.fault === "ERROR").length, 1);
});
test("demo: 고객 메시지에 내부 상태 값 없음", async () => {
  const bad = ["RECEIVED", "ASSIGNED", "FILLING", "TRANSPORTING", "SHIPPED", "HELD", "PARTIAL", "CANCELLED", "배정", "운반", "보류", "일시", "결품", "장애"];
  for (const scene of ["many", "normal"]) {
    const d = open("customer", scene);
    const r = await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k");
    d.clock.advance(10000);
    const all = JSON.stringify(d.msgs);
    for (const w of bad) ok(!all.includes(w), `${scene}: ${w}`);
  }
});
test("demo: 모르는 장면은 normal", async () => {
  const a = open("customer", "xxx"), b = open("customer", "normal");
  eq(a.msgs[0].data, b.msgs[0].data);
});
test("demo: SCENES 이름과 화면", () => {
  eq(Object.keys(SCENES).sort(), ["busy", "maintenance", "many", "normal", "race", "reconnect", "stock"]);
  eq(SCENES.stock.views, ["customer"]);
  eq(SCENES.many.views, ["customer", "admin"]);
});
test("demo: reloadSnapshot은 snapshot을 다시 냄, close 뒤엔 조용", async () => {
  const d = open("customer", "normal");
  await d.src.reloadSnapshot(); eq(d.msgs.filter(m => m.type === "snapshot").length, 2);
  d.src.close(); await d.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k");
  const n = d.msgs.length; d.clock.advance(10000); eq(d.msgs.length, n);
});
test("demo: trip_id는 문자열 <주문>-<순번>, robots.trip_id는 있는 운반 건 또는 빈 문자열", async () => {
  const re = /^O-\d{8}-\d{4}-\d+$/;
  for (const scene of ["normal", "many"]) {
    const d = open("admin", scene);
    d.clock.advance(20000);
    const trips = new Set(), robots = [];
    for (const m of d.msgs) {
      if (m.type === "snapshot") { m.data.trips.forEach(t => trips.add(t.trip_id)); robots.push(...m.data.robots); }
      if (m.type === "trip_changed") trips.add(m.data.trip_id);
      if (m.type === "robots") robots.push(...m.data.robots);
    }
    ok(trips.size > 0 && [...trips].every(t => re.test(t)), `${scene}: trip_id 모양`);
    ok(robots.every(r => typeof r.trip_id === "string"), `${scene}: robots.trip_id 문자열`);
    ok(robots.filter(r => r.trip_id !== "").every(r => trips.has(r.trip_id)), `${scene}: robots.trip_id가 운반 건과 맞음`);
  }
  const c = open("customer", "normal");
  const r = await c.src.placeOrder({ items: [{ product_id: 1, qty: 1 }] }, "k");
  eq(r.body.trip_ids, [`${r.body.order_id}-1`]);
});
