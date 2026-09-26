// thinkaloud desktop shell.
//
// Three moving parts, all local:
//   viewer  - the Next.js app (review UI + the Record screen), served on 127.0.0.1
//   engine  - Python: `engine record` (the recorder) and `engine process` (the processor)
//   pill    - a small always-on-top window with the timer and Stop button while recording.
//             It is excluded from screen capture and its clicks are not recorded.
const { app, BrowserWindow, ipcMain, screen, shell, dialog, safeStorage } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const readline = require("readline");

// A separate profile (single-instance lock, stored API key) for tests or side-by-side runs.
// Must run before anything reads app.getPath("userData").
if (process.env.THINKALOUD_USER_DATA) app.setPath("userData", process.env.THINKALOUD_USER_DATA);

const DEV = !app.isPackaged;
const REPO = path.resolve(__dirname, "..");
const RES = DEV ? REPO : process.resourcesPath;
const DATA = DEV ? REPO : path.join(app.getPath("documents"), "thinkaloud");
const SESSIONS = path.join(DATA, "sessions");
const SAMPLES = path.join(RES, "samples");

let win = null; // main window
let pill = null; // recording pill
let viewer = null; // Next.js server process
let recorder = null; // engine record process
let meter = null; // engine record --meter process
let baseUrl = null;
let quitting = false;

// ---------- engine (Python) ----------------------------------------------------------
function engineCommand(args) {
  if (DEV) {
    const py = process.platform === "win32"
      ? path.join(REPO, ".venv", "Scripts", "python.exe")
      : path.join(REPO, ".venv", "bin", "python");
    return [py, [path.join(REPO, "engine", "engine.py"), ...args]];
  }
  return [path.join(RES, "engine", "thinkaloud-engine.exe"), args];
}

function runEngine(args, onEvent) {
  const [cmd, argv] = engineCommand(args);
  const env = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" };
  if (!DEV) env.HF_HOME = path.join(DATA, "models"); // whisper model cache
  const child = spawn(cmd, argv, { cwd: DATA, env, windowsHide: true });
  child.on("error", (e) => onEvent({ event: "error", message: `could not start the engine: ${e.message}` }));
  let stderr = "";
  child.stderr.on("data", (d) => (stderr = (stderr + d).slice(-4000)));
  readline.createInterface({ input: child.stdout }).on("line", (line) => {
    try {
      onEvent(JSON.parse(line));
    } catch {
      /* ignore non-JSON output such as library warnings */
    }
  });
  child.stderrTail = () => stderr;
  return child;
}

function engineJson(args) {
  return new Promise((resolve, reject) => {
    const [cmd, argv] = engineCommand(args);
    const child = spawn(cmd, argv, { cwd: DATA, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => {
      try {
        resolve(JSON.parse(out));
      } catch {
        reject(new Error(`engine exited ${code}: ${err.slice(-500)}`));
      }
    });
  });
}

// ---------- viewer server ------------------------------------------------------------
function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

function waitForHttp(url, timeoutMs = 90000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      http
        .get(url, (res) => {
          res.resume();
          resolve();
        })
        .on("error", () => {
          if (Date.now() - started > timeoutMs) reject(new Error(`viewer did not start at ${url}`));
          else setTimeout(tick, 300);
        });
    };
    tick();
  });
}

// ---------- Anthropic API key (optional; for AI-assisted review) -----------------------
// Stored encrypted with the OS (DPAPI on Windows) via safeStorage. It only ever goes to the
// local viewer server's environment; it is never sent back to a page or over HTTP.
const KEY_FILE = path.join(app.getPath("userData"), "anthropic-key.bin");

function storedApiKey() {
  try {
    if (fs.existsSync(KEY_FILE) && safeStorage.isEncryptionAvailable()) {
      return safeStorage.decryptString(fs.readFileSync(KEY_FILE));
    }
  } catch {
    /* unreadable: treat as absent */
  }
  return null;
}

async function startViewer() {
  const port = await freePort();
  const [engineCmd, engineArgs] = engineCommand([]);
  const env = {
    ...process.env,
    ELECTRON_RUN_AS_NODE: "1",
    PORT: String(port),
    HOSTNAME: "127.0.0.1",
    THINKALOUD_DIRS: [SESSIONS, SAMPLES].join(";"),
    THINKALOUD_DATA: DATA,
    THINKALOUD_ENGINE: JSON.stringify([engineCmd, ...engineArgs]),
    THINKALOUD_DESKTOP: "1",
  };
  const key = storedApiKey();
  if (key) env.ANTHROPIC_API_KEY = key; // an ANTHROPIC_API_KEY already in the environment also works
  if (!DEV) env.HF_HOME = path.join(DATA, "models");
  if (DEV) {
    const viewerDir = path.join(REPO, "viewer");
    const nextBin = path.join(viewerDir, "node_modules", "next", "dist", "bin", "next");
    viewer = spawn(process.execPath, [nextBin, "dev", "-p", String(port), "-H", "127.0.0.1"], { cwd: viewerDir, env, windowsHide: true });
  } else {
    const dir = path.join(RES, "viewer");
    viewer = spawn(process.execPath, [path.join(dir, "server.js")], { cwd: dir, env, windowsHide: true });
  }
  viewer.stdout.on("data", (d) => DEV && process.stdout.write(`[viewer] ${d}`));
  viewer.stderr.on("data", (d) => process.stderr.write(`[viewer] ${d}`));
  baseUrl = `http://127.0.0.1:${port}`;
  await waitForHttp(baseUrl);
  return baseUrl;
}

// ---------- windows ------------------------------------------------------------------
const secure = {
  preload: path.join(__dirname, "preload.js"),
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
};

function createMainWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 640,
    title: "thinkaloud",
    backgroundColor: "#f3f0e8",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: secure,
  });
  win.loadFile(path.join(__dirname, "loading.html"));
  // Only our local server may be shown inside the app; everything else opens in the browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (baseUrl && !url.startsWith(baseUrl) && !url.startsWith("file:")) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });
  win.on("close", (e) => {
    if (recorder && !quitting) {
      e.preventDefault();
      dialog.showMessageBox(win, { type: "info", message: "A recording is in progress. Stop it first (F9 or the Stop button)." });
    }
  });
}

function createPill() {
  const { workArea } = screen.getPrimaryDisplay();
  const w = 430;
  const h = 64;
  pill = new BrowserWindow({
    width: w,
    height: h,
    x: Math.round(workArea.x + (workArea.width - w) / 2),
    y: workArea.y + workArea.height - h - 24,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    show: false,
    hasShadow: false,
    webPreferences: secure,
  });
  pill.setAlwaysOnTop(true, "screen-saver");
  pill.setContentProtection(true); // keep the pill out of the recorded screenshots
  pill.loadFile(path.join(__dirname, "pill.html"));
  return pill;
}

function send(channel, payload) {
  for (const w of [win, pill]) if (w && !w.isDestroyed()) w.webContents.send(channel, payload);
}

// ---------- recording flow -------------------------------------------------------------
function stopMeter() {
  if (meter) {
    try {
      meter.stdin.write("stop\n");
      meter.stdin.end();
    } catch {}
    meter = null;
  }
}

ipcMain.handle("devices", () => engineJson(["record", "--list-devices"]));

ipcMain.handle("meter:start", (_e, device) => {
  stopMeter();
  const args = ["record", "--meter"];
  if (Number.isInteger(device)) args.push("--device", String(device));
  meter = runEngine(args, (ev) => send("meter", ev));
});
ipcMain.handle("meter:stop", () => stopMeter());

ipcMain.handle("record:start", async (_e, opts) => {
  if (recorder) throw new Error("already recording");
  stopMeter();
  fs.mkdirSync(SESSIONS, { recursive: true });
  const args = ["record", "--json", "--out", SESSIONS, "--countdown", "3",
    "--task", String(opts.task ?? ""), "--criteria", String(opts.criteria ?? "")];
  if (Number.isInteger(opts.device)) args.push("--device", String(opts.device));

  if (!pill || pill.isDestroyed()) createPill();
  pill.showInactive();
  win.minimize(); // get out of the expert's way

  recorder = runEngine(args, (ev) => {
    send("recorder", ev);
    if (ev.event === "started") {
      // Tell the recorder where the pill is (physical pixels) so its clicks are ignored.
      const r = screen.dipToScreenRect(pill, pill.getBounds());
      recorder.stdin.write(JSON.stringify({ cmd: "exclude", rect: [r.x, r.y, r.width, r.height] }) + "\n");
    }
    if (ev.event === "saved") afterRecording(ev);
  });
  recorder.on("close", (code) => {
    const failed = recorder && code !== 0;
    const tail = recorder?.stderrTail?.() ?? "";
    recorder = null;
    if (pill && !pill.isDestroyed()) pill.hide();
    if (failed) {
      restoreMain();
      send("recorder", { event: "error", message: `Recorder exited (${code}). ${tail.slice(-600)}` });
    }
  });
  return true;
});

ipcMain.handle("record:stop", () => {
  if (recorder) recorder.stdin.write(JSON.stringify({ cmd: "stop" }) + "\n");
});

ipcMain.handle("record:pause", (_e, paused) => {
  if (recorder) recorder.stdin.write(JSON.stringify({ cmd: paused ? "pause" : "resume" }) + "\n");
});

ipcMain.handle("settings:apiKeyStatus", () => ({
  stored: fs.existsSync(KEY_FILE),
  fromEnvironment: !!process.env.ANTHROPIC_API_KEY,
  encryption: safeStorage.isEncryptionAvailable(),
}));

async function restartViewer() {
  if (viewer) viewer.kill();
  const url = await startViewer();
  if (win && !win.isDestroyed()) win.loadURL(url);
}

ipcMain.handle("settings:setApiKey", async (_e, key) => {
  if (typeof key !== "string" || !/^[\x21-\x7e]{20,300}$/.test(key.trim())) throw new Error("that doesn't look like an API key");
  if (!safeStorage.isEncryptionAvailable()) throw new Error("OS encryption is unavailable; set ANTHROPIC_API_KEY in the environment instead");
  fs.mkdirSync(path.dirname(KEY_FILE), { recursive: true });
  fs.writeFileSync(KEY_FILE, safeStorage.encryptString(key.trim()));
  await restartViewer();
  return true;
});

ipcMain.handle("settings:clearApiKey", async () => {
  fs.rmSync(KEY_FILE, { force: true });
  await restartViewer();
  return true;
});

function restoreMain() {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

/** processing.json is what the batch view reads (same format as the batch processor). */
function writeStatus(dir, fields) {
  const p = path.join(dir, "processing.json");
  let cur = {};
  try { cur = JSON.parse(fs.readFileSync(p, "utf-8")); } catch { /* none yet */ }
  const next = { ...cur, ...fields, job_id: "app", heartbeat_at: new Date().toISOString() };
  fs.writeFileSync(`${p}.tmp`, JSON.stringify(next, null, 2));
  fs.renameSync(`${p}.tmp`, p);
}

function afterRecording(saved) {
  if (pill && !pill.isDestroyed()) pill.hide();
  restoreMain();
  send("process", { event: "progress", message: "Transcribing and checking your recording…", session_id: saved.session_id });
  writeStatus(saved.dir, { state: "running", attempts: 1, error: null, started_at: new Date().toISOString() });
  const beat = setInterval(() => writeStatus(saved.dir, {}), 2000);
  const child = runEngine(["process", "--json", saved.dir], (ev) => send("process", { ...ev, session_id: saved.session_id }));
  child.on("close", (code) => {
    clearInterval(beat);
    const tail = child.stderrTail().slice(-800);
    writeStatus(saved.dir, code === 0
      ? { state: "done", error: null, finished_at: new Date().toISOString() }
      : { state: "failed", error: `exit ${code}: ${tail.slice(-300)}`, finished_at: new Date().toISOString() });
    if (code !== 0) send("process", { event: "error", session_id: saved.session_id, message: `Processing failed (${code}). ${tail}` });
  });
}

ipcMain.handle("open:sessions", () => {
  fs.mkdirSync(SESSIONS, { recursive: true });
  shell.openPath(SESSIONS);
});
ipcMain.handle("info", () => ({ sessions: SESSIONS, dev: DEV, version: app.getVersion() }));

// ---------- dev-only debug hook ------------------------------------------------------
// THINKALOUD_DEBUG_CMDS=<file>: append lines "snap <png>", "snap pill:<png>" or "eval <js>".
// (Electron on Windows doesn't get a usable stdin, so we watch a file instead.)
if (DEV && process.env.THINKALOUD_DEBUG_CMDS) {
  const cmdFile = process.env.THINKALOUD_DEBUG_CMDS;
  let done = 0;
  fs.writeFileSync(cmdFile, "");
  fs.watchFile(cmdFile, { interval: 300 }, async () => {
    const lines = fs.readFileSync(cmdFile, "utf-8").split(/\r?\n/).filter(Boolean);
    for (const line of lines.slice(done)) {
      done++;
      const [cmd, ...rest] = line.split(" ");
      const arg = rest.join(" ");
      try {
        if (cmd === "snap") {
          const target = arg.startsWith("pill:") ? pill : win;
          const img = await target.webContents.capturePage();
          fs.writeFileSync(arg.replace(/^pill:/, ""), img.toPNG());
          console.log(`[debug] wrote ${arg}`);
        } else if (cmd === "eval") {
          console.log("[debug]", JSON.stringify(await win.webContents.executeJavaScript(arg)));
        }
      } catch (e) {
        console.log("[debug] error", String(e));
      }
    }
  });
}

// ---------- lifecycle ------------------------------------------------------------------
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", restoreMain);

app.whenReady().then(async () => {
  // First run: the engine runs with cwd = DATA, and spawn fails if the folder doesn't exist yet.
  fs.mkdirSync(SESSIONS, { recursive: true });
  createMainWindow();
  try {
    const url = await startViewer();
    win.loadURL(url);
  } catch (e) {
    dialog.showErrorBox("thinkaloud could not start", String(e));
    app.quit();
  }
});

app.on("before-quit", () => {
  quitting = true;
  stopMeter();
  if (recorder) recorder.stdin.write(JSON.stringify({ cmd: "stop" }) + "\n");
  if (viewer) viewer.kill();
});
app.on("window-all-closed", () => app.quit());
