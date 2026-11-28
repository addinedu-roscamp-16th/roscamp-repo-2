// ---- 관리자 현황 화면: 상태 하나를 들고, 메시지마다 state.js를 불러 다시 그림 ----
import { readParams, openSource } from "../common/source.js";
import { modeText } from "../common/words.js";
import { initialState, applyMessage, columns, robotCards, stockLine, centralBadge } from "./state.js";

const $ = (id) => document.getElementById(id);
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const params = readParams(location.search);
let s = initialState();
let gotState = false;
let connText = "연결 중", connTone = "wait";

const COLS = [
  ["action", "조치 필요", "c-action"],
  ["progress", "진행 중", "c-progress"],
  ["received", "접수됨", "c-received"],
  ["done", "완료", "c-done"],
];

// ---- 그리기 ----
function render() {
  $("conn").className = "conn " + connTone;
  $("conn-text").textContent = connText;

  const mode = $("mode");
  mode.hidden = !gotState;
  $("mode-text").textContent = s.mode ? modeText(s.mode) : "";
  mode.className = "mode" + (s.mode === "NORMAL" ? " ok" : " warn");

  const cb = centralBadge(s);
  const central = $("central");
  central.hidden = !gotState || cb === null;
  $("central-text").textContent = cb ? cb.text : "";
  central.className = "mode central " + (cb ? cb.tone : "warn");

  const rb = $("robots");
  rb.replaceChildren();
  for (const r of robotCards(s)) {
    const c = h("div", "robot " + r.tone);
    c.append(h("div", "r-bar"), h("div", "r-name", r.robotId), h("div", "r-state", r.text), h("div", "r-sub", r.sub));
    rb.append(c);
  }

  const cols = columns(s);
  const box = $("cols");
  box.replaceChildren();
  for (const [key, title, cls] of COLS) {
    const col = h("div", "col " + cls);
    const head = h("div", "c-head");
    head.append(h("span", null, title), h("span", "c-n", ` ${cols[key].length}`));
    col.append(head);
    for (const c of cols[key]) {
      const status = c.statusText;
      const card = h("div", "tcard");
      card.dataset.status = status;
      card.append(h("div", "t-title", status));
      card.append(h("div", "t-no", c.tripId || c.orderId));
      const sub = [c.robotId, c.itemsText].filter(Boolean).join(" · ");
      if (sub) card.append(h("div", "t-sub", sub));
      col.append(card);
    }
    box.append(col);
  }

  $("stock").textContent = s.products.length ? `재고: ${stockLine(s)}` : "";
}

// ---- 메시지 · 연결 상태 ----
function onMessage(msg) {
  if (msg.type === "snapshot") gotState = true;
  s = applyMessage(s, msg);
  render();
}
function onState(st) {
  if (st === "open") { connText = "연결됨"; connTone = "ok"; }
  else if (st === "reconnecting") { connText = "다시 연결 중"; connTone = "wait"; }
  else { connText = "연결 중"; connTone = "wait"; }
  render();
}

// ---- 시작 ----
render();
const source = await openSource({ view: "admin", accountId: null, demo: params.demo });
source.onMessage(onMessage);
source.onState(onState);
