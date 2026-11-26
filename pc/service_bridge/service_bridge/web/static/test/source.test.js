// ---- common/source.js · common/live.js 시험 ----
import { test, eq, ok } from "./harness.js";
import { readParams, makeSeqFilter, backoffMs } from "../common/source.js";
import { wsUrl } from "../common/live.js";

test("source: 주소 인자", () => {
  eq(readParams("?account=3&demo=busy"), {demo: "busy", account: 3});
  eq(readParams("?account=abc"), {demo: null, account: null});
});
test("source: seq 거르기", () => {
  const f = makeSeqFilter();
  ok(f.accept({type: "order_changed", seq: 5})); ok(!f.accept({type: "order_changed", seq: 5}));
  ok(f.accept({type: "stock"})); ok(f.accept({type: "snapshot"})); ok(f.accept({type: "order_changed", seq: 1}));
});
test("source: 다시 연결 간격", () => eq([0, 1, 2, 7].map(backoffMs), [1000, 2000, 5000, 5000]));
test("live: WS 주소", () => {
  eq(wsUrl({protocol: "http:", host: "h:8000"}, "customer", 1), "ws://h:8000/ws/customer?account_id=1");
  eq(wsUrl({protocol: "https:", host: "h"}, "admin", null), "wss://h/ws/admin");
});
