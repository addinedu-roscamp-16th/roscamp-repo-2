// ---- 고객 주문 화면: 상태 하나를 들고, 메시지 · 누름마다 state.js를 불러 다시 그림 ----
import { readParams, openSource } from "../common/source.js";
import { PROGRESS_STEPS } from "../common/words.js";
import {
  initialState, applyMessage, applyResponse, setQty, setOnline, beginSend,
  canOrder, cartTotal, orderView, productView,
} from "./state.js";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const params = readParams(location.search);
const accountId = params.account !== null ? params.account : (params.demo ? 1 : null);

let s = initialState(accountId);
let source = null;
let connText = "연결 중", connTone = "wait";
let reloading = false;
const warned = new Set();
// ---- 상품 그림 상태: 종류마다 한 번만 불러 보고(ok · fail), 실패한 종류는 다시 요청하지 않음 ----
const imgState = new Map();
function probeImage(kind) {
  if (imgState.has(kind)) return;
  imgState.set(kind, "loading");
  const im = new Image();
  im.onload = () => { imgState.set(kind, "ok"); render(); };
  im.onerror = () => { imgState.set(kind, "fail"); };
  im.src = `../img/${kind}.png`;
}

// ---- 그리기 ----
function renderConn() {
  $("conn").className = "conn " + connTone;
  $("conn-text").textContent = connText;
}

function renderProducts() {
  const box = $("products");
  box.replaceChildren();
  const list = productView(s);
  if (!list.length) {
    box.append(h("p", "empty", s.products.length ? "찾는 상품이 없습니다." : "상품이 없습니다."));
    return;
  }
  for (const v of list) {
    const card = h("div", "card" + (v.soldOut ? " sold" : ""));
    const thumb = h("div", "thumb", "상품 이미지");
    if (v.kind) {
      probeImage(v.kind);
      if (imgState.get(v.kind) === "ok") {
        const img = document.createElement("img");
        img.alt = v.name;
        img.src = `../img/${v.kind}.png`;
        thumb.textContent = "";
        thumb.append(img);
      }
    }
    const stock = h("div", "p-stock" + (v.soldOut ? " out" : ""), v.soldOut ? "품절" : `재고 ${v.stock}`);
    const step = h("div", "stepper");
    const minus = h("button", null, "−"), plus = h("button", null, "+");
    minus.type = plus.type = "button";
    minus.setAttribute("aria-label", `${v.name} 줄이기`);
    plus.setAttribute("aria-label", `${v.name} 늘리기`);
    minus.disabled = s.sending || v.soldOut || !v.canDec;
    plus.disabled = s.sending || v.soldOut || !v.canInc;
    minus.onclick = () => { s = setQty(s, v.productId, v.qty - 1); render(); };
    plus.onclick = () => { s = setQty(s, v.productId, v.qty + 1); render(); };
    step.append(minus, h("span", "qty", String(v.qty)), plus);
    card.append(thumb, h("div", "p-name", v.name), stock, step);
    box.append(card);
  }
}

function renderOrders() {
  const box = $("orders");
  box.replaceChildren();
  const list = orderView(s);
  if (!list.length) { box.append(h("p", "empty", "아직 주문이 없습니다.")); return; }
  for (const o of list) {
    const card = h("div", "order");
    const head = h("div", "o-head");
    head.append(h("span", "o-no", `주문 ${o.orderId}`), h("span", "o-status" + (o.step ? " s" + o.step : ""), o.statusText));
    card.append(head, h("div", "o-items", o.itemsText));
    if (o.step) {
      const bars = h("div", "bars" + (o.step === 4 ? " done" : ""));
      for (let i = 1; i <= 4; i++) bars.append(h("i", i <= o.step ? "on" : ""));
      const labels = h("div", "labels");
      for (const t of PROGRESS_STEPS) labels.append(h("span", null, t));
      card.append(bars, labels);
    } else if (o.statusText !== "일부 출고" && o.statusText !== "취소됨" && !warned.has(o.statusText)) {
      warned.add(o.statusText);
      console.warn(`모르는 상태 말: ${o.statusText}`);
    }
    box.append(card);
  }
}

function render() {
  renderConn();
  const n = $("notice");
  n.hidden = !s.notice;
  n.textContent = s.notice ? s.notice.message : "";
  renderProducts();
  renderOrders();
  $("summary").textContent = `고른 상품 ${cartTotal(s)}개 · 한 주문 최대 ${s.maxItems}개`;
  const btn = $("order-btn");
  btn.textContent = s.sending ? "주문 중…" : "주문하기";
  btn.disabled = !canOrder(s) || reloading;
  const rj = $("reject");
  rj.hidden = !s.reject;
  rj.textContent = s.reject ? s.reject.text : "";
}

// ---- 메시지 · 연결 상태 ----
function onMessage(msg) {
  const r = applyMessage(s, msg);
  s = r.state;
  for (const ef of r.effects) {
    if (ef.kind === "reload-after") {
      setTimeout(() => {
        if (!s.orders.some(o => o.order_id === ef.orderId)) source.reloadSnapshot();
      }, ef.ms);
    }
  }
  render();
}

function onState(st) {
  if (st === "open") { connText = "연결됨"; connTone = "ok"; s = setOnline(s, true); }
  else if (st === "unknown-account") { connText = "모르는 계정입니다"; connTone = "bad"; s = setOnline(s, false); }
  else if (st === "reconnecting") { connText = "다시 연결 중"; connTone = "wait"; s = setOnline(s, false); }
  else { connText = "연결 중"; connTone = "wait"; s = setOnline(s, false); }
  render();
}

// ---- 주문 보내기: 202 · 거절이면 끝, retryAfterMs가 있으면 기다렸다 같은 키로 다시 ----
async function send(first) {
  let r = first;
  for (;;) {
    s = r.state; render();
    const resp = await source.placeOrder(r.body, r.key);
    const out = applyResponse(s, resp, r.body);
    s = out.state; render();
    if (out.retryAfterMs === null) break;
    await sleep(out.retryAfterMs);
    // 같은 키 · 같은 본문으로 다시 보냄(기다리는 동안 장바구니가 바뀌어도 이미 나간 요청은 끝까지 마무리)
    r = { state: { ...s, sending: true }, key: r.key, body: r.body };
  }
  if (s.reject && s.reject.action === "reload") {
    reloading = true; render();
    try { await source.reloadSnapshot(); } finally { reloading = false; s = { ...s, reject: null }; render(); }
  }
}

function onOrderClick() {
  if (!canOrder(s) || reloading) return;
  send(beginSend(s));
}

// ---- 시작 ----
$("account").textContent = accountId !== null ? `계정 ${accountId}` : "";
$("search").addEventListener("input", (e) => { s = { ...s, query: e.target.value }; render(); });
$("order-btn").addEventListener("click", onOrderClick);

if (accountId === null) {
  $("main").hidden = true;
  $("conn").hidden = true;
  $("fatal").hidden = false;
  $("fatal").textContent = "주소에 ?account=번호를 붙여 주세요";
} else {
  render();
  source = await openSource({ view: "customer", accountId, demo: params.demo });
  source.onMessage(onMessage);
  source.onState(onState);
}
