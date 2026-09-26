// Full user journey in the PACKAGED desktop app, driven through Chrome DevTools Protocol.
//
//   node scripts/app_journey.mjs            (after `npm run pack` in desktop/)
//
// New recording (task + done-when) -> record while the guarded input script works in the test
// bench -> stop from the app -> automatic processing -> review page: before/after images, replay,
// checklist verdict, outcome -> export + download -> reload (persistence) -> recordings page.
// Test data created in Documents\thinkaloud is deleted at the end (it contains screen captures).
// Writes docs/evidence/app_journey_report.json.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const APP = path.join(ROOT, "desktop", "dist", "win-unpacked", "thinkaloud.exe");
const ELECTRON = path.join(ROOT, "desktop", "node_modules", "electron", "dist", "electron.exe");
const PY = path.join(ROOT, ".venv", "Scripts", "python.exe");
const DATA = path.join(os.homedir(), "Documents", "thinkaloud");
const PORT = 9337;
const TASK = "Journey test: add headphones to the cart and open page 2";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const checks = [];
const ok = (name, cond, detail = "") => { checks.push({ name, ok: !!cond, detail: String(detail).slice(0, 300) }); console.log(`${cond ? "PASS" : "FAIL"} ${name}${detail ? `  (${String(detail).slice(0, 160)})` : ""}`); };

async function cdpPage() {
  for (let i = 0; i < 120; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = list.find((t) => t.type === "page" && /^http:\/\/127\.0\.0\.1:\d+\//.test(t.url));
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(500);
  }
  throw new Error("app did not expose its main window over CDP");
}

function connect(url) {
  const ws = new WebSocket(url);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const ready = new Promise((r) => (ws.onopen = r));
  const send = async (method, params = {}) => {
    await ready;
    const n = ++id;
    ws.send(JSON.stringify({ id: n, method, params }));
    return new Promise((r) => pending.set(n, r));
  };
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? "evaluate failed");
    return r.result?.result?.value;
  };
  return { send, evaluate, close: () => ws.close() };
}

async function waitFor(c, expr, timeoutMs = 30000, label = expr) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try { const v = await c.evaluate(expr); if (v) return v; } catch { /* navigating */ }
    await sleep(300);
  }
  const state = await c.evaluate(`JSON.stringify({ url: location.href, bridge: !!window.thinkaloud,
    inputs: document.querySelectorAll('.field input').length, options: document.querySelectorAll('select option').length,
    text: document.body.innerText.slice(0, 400) })`).catch((e) => String(e));
  throw new Error(`timed out waiting for ${label}; page: ${state}`);
}

const setInput = (sel, value) => `(() => { const el = document.querySelector(${JSON.stringify(sel)});
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, ${JSON.stringify(value)});
  el.dispatchEvent(new Event("input", { bubbles: true })); return true; })()`;
const clickText = (sel, text) => `(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.textContent.trim().startsWith(${JSON.stringify(text)}));
  if (!b) return false; b.click(); return true; })()`;

async function main() {
  if (!fs.existsSync(APP)) throw new Error(`build the app first: ${APP}`);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ta-journey-"));
  const layout = path.join(tmp, "layout.json");
  const tb = spawn(ELECTRON, [path.join(ROOT, "scripts", "testbench")], { env: { ...process.env, THINKALOUD_TB_LAYOUT: layout }, stdio: "ignore" });
  const app = spawn(APP, [`--remote-debugging-port=${PORT}`], { stdio: "ignore", env: { ...process.env, THINKALOUD_USER_DATA: path.join(tmp, "profile") } });
  let sessionId = null;
  let exportZip = null;
  try {
    for (let i = 0; i < 100 && !fs.existsSync(layout); i++) await sleep(200);
    const c = connect(await cdpPage());
    await c.send("Runtime.enable");
    const base = await waitFor(c, "location.origin.startsWith('http://127.0.0.1') && location.origin", 60000, "viewer");
    await c.evaluate(`location.href = ${JSON.stringify(base + "/record")}`);
    await waitFor(c, "!!window.thinkaloud && document.querySelectorAll('.field input').length === 2 && document.querySelectorAll('select option').length > 0", 30000, "record form");
    await c.evaluate(setInput(".field input", TASK));
    await c.evaluate(`(() => { const el = document.querySelectorAll('.field input')[1];
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, "Page 2 is open");
      el.dispatchEvent(new Event("input", { bubbles: true })); return true; })()`);
    ok("record form: microphones listed", await c.evaluate("document.querySelectorAll('select option').length"));
    await sleep(500);
    ok("record form: start enabled", await c.evaluate(clickText("button", "Start recording")));
    await waitFor(c, "document.querySelector('h1')?.textContent === 'Recording'", 20000, "recording state");
    ok("app shows the recording state", true);

    const inputs = spawnSync(PY, [path.join(ROOT, "scripts", "e2e_capture.py"), "--inputs-only", "--layout", layout, "--flash-seconds", "4"], { encoding: "utf-8", timeout: 180000 });
    ok("guarded inputs performed in the test window", inputs.status === 0, (inputs.stdout + inputs.stderr).trim().split("\n").pop());
    await c.evaluate("window.thinkaloud.stopRecording()");
    const reviewPath = await waitFor(c, "location.pathname.startsWith('/s/') && location.pathname", 600000, "processing to finish");
    sessionId = decodeURIComponent(reviewPath.slice(3));
    ok("stopping processes the recording and opens the review", sessionId, sessionId);
    await waitFor(c, "document.querySelector('.stage img')?.complete", 20000, "stage image");

    const desc = await c.evaluate("[...document.querySelectorAll('.action')].map(x => x.textContent).join(' | ')");
    ok("review page shows the task", await c.evaluate(`document.querySelector('h1').textContent.includes(${JSON.stringify("Journey test")})`));
    ok("before image loads", await c.evaluate("document.querySelector('.stage img').naturalWidth > 0"), desc);
    await c.evaluate(clickText(".stage-bar button", "After"));
    await sleep(800);
    ok("after view renders an image or an explicit reason", await c.evaluate("!!(document.querySelector('.stage img')?.naturalWidth || document.querySelector('.noshot b')?.textContent)"));
    // step with the Add to Cart click: walk steps until its description shows up
    const seen = [];
    for (let i = 0; i < 30; i++) {
      const d = await c.evaluate("document.querySelector('.action')?.textContent ?? '(end)'");
      seen.push(d);
      if (d === "Clicked the Add to Cart button" || d === "(end)") break;
      await c.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' })), true");
      await sleep(250);
    }
    ok("step described from UI Automation in the app", seen.includes("Clicked the Add to Cart button"), seen.join(" | "));
    await c.evaluate(clickText(".stage-bar button", "Replay"));
    const played = await c.evaluate(`(async () => { const v = document.querySelector('video'); if (!v) return 'no video';
      v.muted = true; await v.play(); await new Promise(r => setTimeout(r, 2500)); const out = { t: v.currentTime, ready: v.readyState, dur: v.duration };
      v.pause(); return out; })()`);
    ok("replay plays", played && played.t > 0.5 && played.ready >= 2, JSON.stringify(played));
    const playhead = await c.evaluate("!!document.querySelector('.tl line[stroke=\"var(--ok)\"]')");
    ok("timeline shows the replay position", playhead);

    await c.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'e' })), true");
    await waitFor(c, "!!document.querySelector('input[aria-label=\"New checklist item\"]')", 10000, "end panel");
    await c.evaluate(setInput("input[aria-label='New checklist item']", "Page 2 is shown"));
    await c.evaluate("document.querySelector('input[aria-label=\"New checklist item\"]').form.requestSubmit(), true");
    await sleep(300);
    await c.evaluate(clickText(".check-item button", "Met"));
    await c.evaluate(clickText(".rv-actions .seg button", "Task done"));
    const saved = await waitFor(c, "document.querySelector('.save-state')?.textContent.startsWith('Review saved') && document.querySelector('.save-state').textContent", 15000, "autosave");
    ok("review autosaves", saved, saved);

    const exp = await c.evaluate(`fetch('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [${JSON.stringify(sessionId)}], formats: ['dataset', 'claude'], include_media: true }) }).then(r => r.json())`);
    exportZip = exp?.zip ?? null;
    ok("export succeeds and the bundle validates", exp?.ok && exp.validation?.ok, JSON.stringify({ ok: exp?.ok, v: exp?.validation?.ok, errors: exp?.validation?.errors?.slice(0, 3), problems: exp?.errors?.slice(0, 3) }));
    const dl = await c.evaluate(`fetch(${JSON.stringify(exp?.download ?? "")}).then(async r => ({ status: r.status, type: r.headers.get('content-type'), bytes: (await r.arrayBuffer()).byteLength }))`);
    ok("export downloads as a zip", dl.status === 200 && dl.type === "application/zip" && dl.bytes > 1000, JSON.stringify(dl));
    if (exportZip) {
      const v = spawnSync(PY, [path.join(ROOT, "processor", "thinkaloud", "dataset.py"), exportZip, "--json"], { encoding: "utf-8" });
      ok("downloaded bundle validates independently", v.status === 0, v.stdout.trim().split("\n").pop());
    }

    await c.evaluate("location.reload()");
    await waitFor(c, "!!document.querySelector('.rv-actions .seg')", 30000, "reload");
    await sleep(800);
    const persisted = await c.evaluate(`(() => { const b = [...document.querySelectorAll('.rv-actions .seg button')].find(x => x.textContent === 'Task done');
      return b?.getAttribute('aria-pressed') === 'true'; })()`);
    ok("outcome persists across reload", persisted);

    await c.evaluate(`location.href = ${JSON.stringify(base + "/")}`);
    await waitFor(c, "document.querySelectorAll('tbody tr').length > 0", 30000, "recordings page");
    const row = await c.evaluate(`(() => { const tr = [...document.querySelectorAll('tbody tr')].find(t => t.textContent.includes(${JSON.stringify(sessionId)}));
      return tr ? { status: tr.querySelector('.status')?.textContent, steps: tr.children[3].textContent, review: tr.children[7].textContent } : null; })()`);
    ok("recordings page shows the processed, reviewed recording", row && row.status === "processed" && +row.steps > 0 && row.review.includes("Task done"), JSON.stringify(row));
    c.close();
  } finally {
    // only the processes this script started (the app's viewer/engine children included)
    spawnSync("taskkill", ["/PID", String(app.pid), "/T", "/F"], { stdio: "ignore" });
    spawnSync("taskkill", ["/PID", String(tb.pid), "/T", "/F"], { stdio: "ignore" });
    if (sessionId) fs.rmSync(path.join(DATA, "sessions", sessionId), { recursive: true, force: true });
    if (exportZip) {
      fs.rmSync(exportZip, { force: true });
      fs.rmSync(exportZip.replace(/\.zip$/, ""), { recursive: true, force: true });
    }
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  const report = { at: new Date().toISOString(), checks, passed: checks.filter((c) => c.ok).length, total: checks.length };
  fs.mkdirSync(path.join(ROOT, "docs", "evidence"), { recursive: true });
  fs.writeFileSync(path.join(ROOT, "docs", "evidence", "app_journey_report.json"), JSON.stringify(report, null, 2));
  console.log(`${report.passed}/${report.total} checks passed; test recording and export deleted`);
  process.exit(report.passed === report.total ? 0 : 1);
}

main().catch((e) => { console.error("JOURNEY FAILED:", e.message); process.exit(1); });
