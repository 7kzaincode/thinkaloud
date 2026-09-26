// Controlled test application for end-to-end capture checks (scripts/e2e_capture.py).
// A Chromium window at a fixed position with renderer accessibility forced on, so UI
// Automation sees real controls (button, edit, password edit, link, list). Writes the
// physical-pixel screen rectangles of its elements to THINKALOUD_TB_LAYOUT.
const { app, BrowserWindow, screen } = require("electron");
const fs = require("fs");
const path = require("path");

app.commandLine.appendSwitch("force-renderer-accessibility");
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

app.whenReady().then(async () => {
  const win = new BrowserWindow({ x: 60, y: 60, width: 1100, height: 760, title: "Testbench Store",
    alwaysOnTop: true, autoHideMenuBar: true, webPreferences: { contextIsolation: true } });
  await win.loadFile(path.join(__dirname, "page.html"));
  // Stay above every other window so injected input can only land here; the driver still
  // verifies the foreground window and the window under the cursor before every action.
  win.show();
  win.setAlwaysOnTop(true, "screen-saver");
  win.moveTop();
  win.focus();
  const ids = ["cart", "flash", "search", "password", "list", "page2", "swatch", "flashpad"];
  const rects = await win.webContents.executeJavaScript(`(${JSON.stringify(ids)}).reduce((o, id) => {
    const r = document.getElementById(id).getBoundingClientRect();
    o[id] = [r.left, r.top, r.width, r.height]; return o; }, {})`);
  const content = win.getContentBounds();              // DIP
  const out = {};
  for (const [id, [x, y, w, h]] of Object.entries(rects)) {
    // DIP -> physical screen pixels (the recorder and pynput use physical pixels)
    const r = screen.dipToScreenRect(win, { x: content.x + x, y: content.y + y, width: w, height: h });
    out[id] = [r.x, r.y, r.width, r.height];
  }
  const layout = process.env.THINKALOUD_TB_LAYOUT;
  const hwnd = Number(win.getNativeWindowHandle().readBigUInt64LE(0));
  if (layout) fs.writeFileSync(layout, JSON.stringify({ rects: out, pid: process.pid, hwnd, always_on_top: win.isAlwaysOnTop() }, null, 2));
});
app.on("window-all-closed", () => app.quit());
