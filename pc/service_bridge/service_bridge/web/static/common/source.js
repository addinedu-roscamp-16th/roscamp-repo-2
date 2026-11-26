// ---- 메시지 출처: 주소 인자 읽기, seq 거르기, 다시 연결 간격, 출처 고르기 ----

// ---- 주소 인자: ?demo=<장면>&account=<양의 정수> ----
export function readParams(search) {
  const p = new URLSearchParams(search || "");
  const raw = p.get("account");
  const n = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  return { demo: p.get("demo") || null, account: n !== null && n > 0 ? n : null };
}

// ---- seq 거르기: seq 없으면 통과, 마지막 seq 이하는 버림, snapshot은 기억을 지우고 통과 ----
export function makeSeqFilter() {
  let last = null;
  return {
    accept(msg) {
      if (msg && msg.type === "snapshot") { last = null; return true; }
      if (!msg || typeof msg.seq !== "number") return true;
      if (last !== null && msg.seq <= last) return false;
      last = msg.seq;
      return true;
    },
    reset() { last = null; },
  };
}

// ---- 다시 연결 간격(ms): 1초 → 2초 → 5초(최대) ----
export function backoffMs(attempt) {
  return attempt <= 0 ? 1000 : attempt === 1 ? 2000 : 5000;
}

// ---- 출처 고르기: demo가 있으면 가짜(demo.js), 없으면 실제(live.js) ----
export async function openSource({ view, accountId, demo }) {
  if (demo) {
    const { createDemo } = await import("./demo.js");
    const src = createDemo({ view, accountId, scene: demo });
    const band = document.createElement("div");
    band.textContent = `가짜 데이터: ${demo}`;
    band.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:9999;padding:4px 8px;" +
      "background:#fbbf24;color:#111418;font:600 13px sans-serif;text-align:center;";
    document.body.appendChild(band);
    return src;
  }
  const { createLive } = await import("./live.js");
  return createLive({ view, accountId });
}
