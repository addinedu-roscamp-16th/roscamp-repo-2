// ---- common/words.js 시험 ----
import { test, eq, ok } from "./harness.js";
import {
  PROGRESS_STEPS, progressIndex, adminColumn, adminStatusText,
  robotWord, productName, rejectMessage, modeText,
} from "../common/words.js";

test("words: 진행 칸", () => { eq(progressIndex("준비 중"), 2); eq(progressIndex("일부 출고"), null); });
test("words: 관리자 칸 나누기", () => {
  eq(["FILLING_PAUSE","HELD","RECEIVED","ASSIGNED","FILLING","TRANSPORTING","SHIPPED","PARTIAL","CANCELLED"].map(adminColumn),
     ["action","action","received","progress","progress","progress","done","done","done"]);
});
test("words: 모르는 관리자 상태는 원래 값", () => eq(adminStatusText("NEW_STATE"), "NEW_STATE"));
test("words: 관리자 상태 이름", () => eq(adminStatusText("FILLING_PAUSE"), "담기 일시 중지"));
test("words: 오류 로봇", () => eq(robotWord({fsm_state: "IDLE", fault: "ERROR"}), {text: "오류", tone: "bad"}));
test("words: 정상 로봇과 모르는 상태", () => {
  eq(robotWord({fsm_state: "IDLE", fault: "NONE"}), {text: "대기", tone: "ok"});
  eq(robotWord({fsm_state: "XYZ", fault: "NONE"}), {text: "XYZ", tone: "busy"});
});
test("words: 작동 방식 말", () => { eq(modeText("NORMAL"), "실행 중"); eq(modeText("TEST"), "시험 모드"); });
test("words: 이름 없는 상품", () => eq(productName({product_id: 3}), "상품 3"));
test("words: 재고 초과 문구", () => {
  const r = rejectMessage(422, {code: "OUT_OF_STOCK", detail: {items: [{product_id: 1, qty: 5, stock_qty: 2}]}}, {1: "미니멘토스 레인보우"});
  eq(r.action, "lower"); ok(r.text.includes("수량을 줄여 다시 주문해 주세요"));
  ok(r.text.includes("미니멘토스 레인보우") && r.text.includes("2개"), r.text);
});
test("words: 점검 중", () => eq(rejectMessage(503, {code: "MAINTENANCE"}, {}).action, "maintenance"));
test("words: 그 밖 거절은 코드를 보임", () => ok(rejectMessage(404, {code: "UNKNOWN_ACCOUNT"}, {}).text.includes("UNKNOWN_ACCOUNT")));
test("words: 고객 말에 내부 말 없음", () => {
  for (const w of PROGRESS_STEPS) ok(!/장애|보류|일시|마감|결품|배정|운반/.test(w), w);
});
