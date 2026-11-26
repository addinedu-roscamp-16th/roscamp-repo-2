// ---- 가짜 데이터 출처: Source 모양(onMessage · onState · placeOrder · reloadSnapshot · close) ----
import { SCENES } from "./demo_scenes.js";

const realClock = { setTimeout: (fn, ms) => setTimeout(fn, ms), now: () => Date.now() };

// ---- 가짜 출처 만들기: 화면마다 따로 돌고, 모든 시간은 clock으로 잰다 ----
export function createDemo({ view, scene }, clock = realClock) {
  let def = Object.hasOwn(SCENES, scene) ? SCENES[scene] : SCENES.normal;
  if (!def.views.includes(view)) def = SCENES.normal;       // 그 화면에 없는 장면이면 normal
  const msgHandlers = [], stateHandlers = [];
  const model = def.init(view);
  let seq = 0, connected = true, closed = false, orderEvents = 0, dropped = false;

  const emitState = (s) => { if (!closed) stateHandlers.forEach(h => h(s)); };
  const deliver = (m) => { msgHandlers.forEach(h => h(m)); };
  const after = (ms, fn) => clock.setTimeout(() => { if (!closed) fn(); }, ms);
  const snapshot = () => ({ type: "snapshot", ts: new Date(clock.now()).toISOString(), data: JSON.parse(JSON.stringify(model.data)) });

  // 끊긴 동안(connected=false)에는 모델만 바뀌고 메시지는 나가지 않는다
  function emit(type, data) {
    if (closed || !connected) return;
    deliver({ type, seq: ++seq, ts: new Date(clock.now()).toISOString(), data });
    if (type === "order_changed" && def.dropAfter && !dropped && ++orderEvents >= def.dropAfter) {
      dropped = true; connected = false;
      emitState("reconnecting");
      after(def.dropMs, () => { connected = true; emitState("open"); deliver(snapshot()); });
    }
  }

  const ctx = { view, model, emit, after, now: () => clock.now(), scratch: {} };

  // 처음 연결은 0ms 뒤: 핸들러를 만든 직후에 달아도 첫 상태와 스냅샷을 받는다
  after(0, () => {
    emitState("open");
    deliver(snapshot());
    if (def.notice && model.data.notice) emit("customer_notice", { ...model.data.notice });
    def.start(ctx);
  });

  return {
    onMessage(h) { msgHandlers.push(h); },
    onState(h) { stateHandlers.push(h); },
    async placeOrder(body, key) {
      // 실제 _OrderBody처럼 account_id 정수 · 비지 않은 items가 아니면 400
      const okBody = body && Number.isInteger(body.account_id) && Array.isArray(body.items) && body.items.length > 0;
      if (!okBody) return { status: 400, body: { code: "BAD_REQUEST", message: "요청 본문이 올바르지 않습니다.", detail: null }, headers: { get: () => null } };
      return def.placeOrder(ctx, body, key);
    },
    async reloadSnapshot() { if (!closed) deliver(snapshot()); },
    close() { closed = true; },
  };
}
