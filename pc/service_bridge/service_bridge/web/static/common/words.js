// ---- 화면 공용 말 표 (상태 말 · 관리자 상태 · 로봇 상태 · 거절 안내) ----

// ---- 고객 진행 막대 ----
export const PROGRESS_STEPS = ["접수됨", "준비 중", "출고 중", "출고 완료"];

// 상태 말 → 진행 칸(1~4). 표에 없으면 null
export function progressIndex(statusText) {
  const i = PROGRESS_STEPS.indexOf(statusText);
  return i < 0 ? null : i + 1;
}

// ---- 관리자 운반 건 상태 ----
export const ADMIN_STATUS = {
  RECEIVED: "접수됨",
  ASSIGNED: "배정됨",
  FILLING: "담는 중",
  FILLING_PAUSE: "담기 일시 중지",
  HELD: "주문 처리 보류",
  TRANSPORTING: "운반 중",
  SHIPPED: "출고됨",
  PARTIAL: "일부 출고됨",
  CANCELLED: "취소됨",
};

// 표에 없으면 원래 값
export function adminStatusText(status) {
  return Object.hasOwn(ADMIN_STATUS, status) ? ADMIN_STATUS[status] : status;
}

// 현황 화면 칸: action(조치 필요) / progress(진행 중) / received(접수됨) / done(완료)
export function adminColumn(status) {
  if (status === "FILLING_PAUSE" || status === "HELD") return "action";
  if (status === "RECEIVED") return "received";
  if (status === "SHIPPED" || status === "PARTIAL" || status === "CANCELLED") return "done";
  return "progress";
}

// ---- 작업 상태 ----
export const MODE_TEXT = { NORMAL: "실행 중", TEST: "시험 모드" };

export function modeText(mode) {
  return Object.hasOwn(MODE_TEXT, mode) ? MODE_TEXT[mode] : mode;
}

// ---- 로봇 상태 (fsm_state 값은 RobotState.msg의 STATE_* 와 같다) ----
export const FSM_TEXT = {
  IDLE: { text: "대기", tone: "ok" },
  MOVING: { text: "이동 중", tone: "busy" },
  WAITING: { text: "기다림", tone: "busy" },
  DOCKING: { text: "도킹 중", tone: "busy" },
  DOCKED: { text: "도킹됨", tone: "busy" },
  BLOCKED: { text: "막힘", tone: "bad" },
  LOCALIZING: { text: "위치 확인 중", tone: "busy" },
  RECOVERING: { text: "복구 중", tone: "bad" },
  ERROR: { text: "오류", tone: "bad" },
};

// fault가 NONE이 아니면 오류, 아니면 표, 표에 없으면 원래 값
export function robotWord(robot) {
  if (robot.fault !== "NONE") return { text: "오류", tone: "bad" };
  if (Object.hasOwn(FSM_TEXT, robot.fsm_state)) return { ...FSM_TEXT[robot.fsm_state] };
  return { text: robot.fsm_state, tone: "busy" };
}

// ---- 상품 이름 ----
export function productName(p) {
  return p.name ? p.name : `상품 ${p.product_id}`;
}

// ---- 고객 거절 안내 (설계 3.3) ----
export function rejectMessage(status, body, names) {
  const code = body && body.code;
  const detail = body && body.detail;
  if (status === 422 && code === "OUT_OF_STOCK") {
    // detail 모양: {items: [{product_id, qty, stock_qty}]} (core/intake.py)
    const items = (detail && detail.items) || [];
    const parts = items.map(it => {
      const name = productName({ product_id: it.product_id, name: names[it.product_id] });
      return `${name} 재고가 ${it.stock_qty}개뿐입니다.`;
    });
    const head = parts.length ? parts.join(" ") : "재고가 부족합니다.";
    return { text: `${head} 수량을 줄여 다시 주문해 주세요.`, action: "lower" };
  }
  if (status === 422 && code === "OVER_MAX_ITEMS") {
    const n = detail && detail.max_order_items;
    return { text: n != null ? `한 주문은 최대 ${n}개까지입니다.` : "한 주문 최대 수량을 넘었습니다.", action: null };
  }
  if (status === 422 && code === "UNKNOWN_PRODUCT") {
    return { text: "상품 정보가 바뀌었습니다. 화면을 새로 불러옵니다.", action: "reload" };
  }
  if (status === 503 && code === "MAINTENANCE") {
    return { text: "점검 중입니다. 잠시 뒤 다시 주문해 주세요.", action: "maintenance" };
  }
  if (status === 503 && code === "BUSY") {
    return { text: "주문이 몰려 처리하지 못했습니다. 다시 눌러 주세요.", action: null };
  }
  return { text: `주문을 보내지 못했습니다. (${code || status})`, action: null };
}
