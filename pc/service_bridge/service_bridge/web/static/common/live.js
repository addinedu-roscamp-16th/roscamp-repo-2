// ---- 실제 연결: REST + WebSocket (절대 경로는 이 파일에만 둔다) ----
import { makeSeqFilter, backoffMs } from "./source.js";

export function apiUrl(path) { return path; }

// ---- WS 주소: https면 wss, 고객은 account_id를 붙임 ----
export function wsUrl(loc, view, accountId) {
  const scheme = loc.protocol === "https:" ? "wss:" : "ws:";
  const tail = view === "admin" ? "/ws/admin" : `/ws/customer?account_id=${accountId}`;
  return `${scheme}//${loc.host}${tail}`;
}

function snapshotPath(view, accountId) {
  return `/api/snapshot?view=${view}` + (view === "customer" && accountId != null ? `&account_id=${accountId}` : "");
}

const NO_HEADERS = { get() { return null; } };

export function createLive({ view, accountId }) {
  const msgHandlers = [], stateHandlers = [];
  const filter = makeSeqFilter();
  let ws = null, attempt = 0, timer = null, closed = false;

  const emitState = (s) => stateHandlers.forEach(h => h(s));
  const emitMsg = (m) => { if (filter.accept(m)) msgHandlers.forEach(h => h(m)); };

  function connect() {
    if (closed) return;
    ws = new WebSocket(wsUrl(location, view, accountId));
    ws.onopen = () => { attempt = 0; emitState("open"); };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      emitMsg(m);
    };
    ws.onclose = (ev) => {
      if (closed) return;
      if (ev.code === 4404) { emitState("unknown-account"); return; }
      emitState("reconnecting");
      timer = setTimeout(connect, backoffMs(attempt++));
    };
    ws.onerror = () => {};
  }

  async function readJson(res) {
    try { return await res.json(); } catch { return null; }
  }

  emitState("connecting");
  connect();

  return {
    onMessage(h) { msgHandlers.push(h); },
    onState(h) { stateHandlers.push(h); },
    async placeOrder(body, key) {
      try {
        const res = await fetch(apiUrl("/api/orders"), {
          method: "POST",
          headers: { "Content-Type": "application/json", "Idempotency-Key": key },
          body: JSON.stringify(body),
        });
        return { status: res.status, body: await readJson(res), headers: res.headers };
      } catch {
        return { status: 0, body: null, headers: NO_HEADERS };
      }
    },
    async reloadSnapshot() {
      try {
        const res = await fetch(apiUrl(snapshotPath(view, accountId)));
        const data = await readJson(res);
        if (res.ok && data) emitMsg({ type: "snapshot", ts: new Date().toISOString(), data });  // GET 본문은 봉투 없이 data 그대로
      } catch { /* 망 오류면 다음 WS 재연결의 스냅샷에 맡김 */ }
    },
    close() {
      closed = true;
      clearTimeout(timer);
      if (ws) ws.close();
    },
  };
}
