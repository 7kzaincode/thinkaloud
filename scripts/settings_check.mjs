// Settings page of the PACKAGED app: AI provider choice and key storage through the desktop
// bridge (safeStorage), driven over Chrome DevTools Protocol. Uses a throwaway profile and a
// dummy key; no request is sent to any AI provider.
//
//   node scripts/settings_check.mjs        (after `npm run pack` in desktop/)
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const APP = process.env.THINKALOUD_APP || path.join(ROOT, "desktop", "dist", "win-unpacked", "thinkaloud.exe");
const PORT = 9338;
const DUMMY = "test-key-not-real-0000000000000000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const checks = [];
const ok = (name, cond, detail = "") => { checks.push({ name, ok: !!cond, detail: String(detail).slice(0, 200) }); console.log(`${cond ? "PASS" : "FAIL"} ${name}${detail ? `  (${String(detail).slice(0, 160)})` : ""}`); };

async function page() {
  for (let i = 0; i < 120; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const p = list.find((t) => t.type === "page" && /^http:\/\/127\.0\.0\.1:\d+\//.test(t.url));
      if (p) return p.webSocketDebuggerUrl;
    } catch { /* not up */ }
    await sleep(500);
  }
  throw new Error("no main window");
}

function connect(url) {
  const ws = new WebSocket(url);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); } };
  const ready = new Promise((r) => (ws.onopen = r));
  const evaluate = async (expression) => {
    await ready;
    const n = ++id;
    ws.send(JSON.stringify({ id: n, method: "Runtime.evaluate", params: { expression, awaitPromise: true, returnByValue: true } }));
    const r = await new Promise((res) => pending.set(n, res));
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? "evaluate failed");
    return r.result?.result?.value;
  };
  return { evaluate, close: () => ws.close() };
}

async function waitFor(c, expr, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const v = await c.evaluate(expr); if (v) return v; } catch { /* reloading */ }
    await sleep(300);
  }
  throw new Error(`timed out: ${expr}`);
}

const status = `fetch("/api/ai/status").then((r) => r.json())`;

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ta-settings-"));
  const env = { ...process.env, THINKALOUD_USER_DATA: path.join(tmp, "profile") };
  for (const v of ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "THINKALOUD_AI_PROVIDER"]) delete env[v];
  const app = spawn(APP, [`--remote-debugging-port=${PORT}`], { stdio: "ignore", env });
  try {
    let c = connect(await page());
    const base = await waitFor(c, "location.origin.startsWith('http://127.0.0.1') && location.origin", 60000);
    await c.evaluate(`location.href = ${JSON.stringify(base + "/settings")}`);
    await waitFor(c, "!!window.thinkaloud && document.querySelectorAll('[role=radio]').length === 3");
    const s0 = await c.evaluate(status);
    ok("no keys: not configured", !s0.configured, JSON.stringify(s0));
    ok("provider choices shown", await c.evaluate(`[...document.querySelectorAll('[role=radio]')].map((b) => b.textContent).join("|")`) === "Automatic|Anthropic (Claude)|Google Gemini");
    ok("data-use note shown", await c.evaluate(`document.body.innerText.includes("free-tier")`));
    // store a dummy Gemini key through the bridge (the app encrypts it and restarts its server)
    await c.evaluate(`window.thinkaloud.setApiKey("gemini", ${JSON.stringify(DUMMY)}).catch(() => null)`).catch(() => null);
    c.close();
    await sleep(1500);
    c = connect(await page());
    await waitFor(c, "!!window.thinkaloud && document.readyState === 'complete'", 60000);
    const st = await c.evaluate("window.thinkaloud.apiKeyStatus()");
    ok("key stored encrypted, reported as a boolean", st.keys.gemini.stored === true && !JSON.stringify(st).includes(DUMMY), JSON.stringify(st));
    const keyFile = path.join(tmp, "profile", "gemini-key.bin");
    ok("key file is not plain text", fs.existsSync(keyFile) && !fs.readFileSync(keyFile).includes(Buffer.from(DUMMY)));
    const s1 = await c.evaluate(status);
    ok("automatic picks Gemini when only a Gemini key exists", s1.configured && s1.provider === "gemini" && s1.provider_name === "Google Gemini", JSON.stringify(s1));
    ok("the key never reaches the page", !JSON.stringify(s1).includes(DUMMY) && !(await c.evaluate("document.documentElement.outerHTML")).includes(DUMMY));
    // choose Anthropic explicitly: not configured (no Anthropic key), and says why
    await c.evaluate(`window.thinkaloud.setAiProvider("anthropic").catch(() => null)`).catch(() => null);
    c.close();
    await sleep(1500);
    c = connect(await page());
    await waitFor(c, "!!window.thinkaloud && document.readyState === 'complete'", 60000);
    const s2 = await c.evaluate(status);
    ok("explicit Anthropic without its key is not configured", !s2.configured && s2.provider === "anthropic", JSON.stringify(s2));
    await c.evaluate(`window.thinkaloud.setAiProvider(null).catch(() => null)`).catch(() => null);
    c.close();
    await sleep(1500);
    c = connect(await page());
    await waitFor(c, "!!window.thinkaloud && document.readyState === 'complete'", 60000);
    await c.evaluate(`window.thinkaloud.clearApiKey("gemini").catch(() => null)`).catch(() => null);
    c.close();
    await sleep(1500);
    c = connect(await page());
    await waitFor(c, "!!window.thinkaloud && document.readyState === 'complete'", 60000);
    const s3 = await c.evaluate(status);
    ok("removing the key: not configured again", !s3.configured && !fs.existsSync(keyFile), JSON.stringify(s3));
    c.close();
  } finally {
    spawnSync("taskkill", ["/PID", String(app.pid), "/T", "/F"], { stdio: "ignore" });
    await sleep(1000);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  const passed = checks.filter((x) => x.ok).length;
  console.log(`${passed}/${checks.length} checks passed`);
  fs.writeFileSync(path.join(ROOT, "docs", "evidence", "settings_check_report.json"),
    JSON.stringify({ at: new Date().toISOString(), checks }, null, 2));
  process.exit(passed === checks.length ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(2); });
