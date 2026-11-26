// ---- 브라우저 시험 도구: test / eq / ok / run ----
const tests = [];

export function test(name, fn) { tests.push({ name, fn }); }

export function ok(cond, msg) {
  if (!cond) throw new Error(msg || "조건이 거짓");
}

export function eq(actual, expected, msg) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error((msg ? msg + ": " : "") + `기대 ${e}, 실제 ${a}`);
}

// ---- 등록된 시험을 차례로 돌리고 결과를 페이지에 씀 ----
export async function run() {
  let pass = 0;
  const fails = [];
  for (const t of tests) {
    try { await t.fn(); pass++; }
    catch (e) { fails.push(`${t.name} - ${e.message}`); }
  }
  const out = document.createElement("pre");
  out.id = "result";
  out.textContent = [`결과: 통과 ${pass} · 실패 ${fails.length}`, ...fails.map(f => `실패: ${f}`)].join("\n");
  document.body.appendChild(out);
}
