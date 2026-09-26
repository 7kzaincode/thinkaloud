"""Native Windows end-to-end check: real input -> recorder -> processor -> assertions.

Drives the controlled test bench (scripts/testbench, a Chromium window) with injected
mouse/keyboard input while the real recorder runs, then processes the session and checks:

  * steps and UI Automation targets (button, text field, password field, link, URL)
  * the password typed into a password field never reaches disk
  * one scroll step for scroll-pause-scroll-reverse over a list, with both direction runs
  * before/after images show the right state (pixel colour of a state swatch)
  * video timestamps vs input events, using a pad that flips black/white on each click
  * playback.mp4 keeps the capture timestamps (stream copy)

It moves the real mouse and types into the test window for the duration of the run.
Safety: the test window is always-on-top, and before EVERY click, scroll and keystroke the
driver checks that the foreground window and the window under the cursor are the test
window. If another window comes to the front it aborts without sending more input and
deletes the partial session (which could contain screenshots of other windows).

    python scripts/e2e_capture.py                      # ~30 s functional run
    python scripts/e2e_capture.py --flash-seconds 180  # + multi-minute sync measurement
Writes a JSON report next to the session (e2e_report.json) and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ELECTRON = ROOT / "desktop" / "node_modules" / "electron" / "dist" / "electron.exe"
PASSWORD = "Hunter2!secret"
PASSWORD2 = "Tabbed#Secret9!"    # typed after Tab moves focus into the password field (no click)
SEARCH = "noise cancelling headphones"


def center(r):
    return (r[0] + r[2] // 2, r[1] + r[3] // 2)


class NotInFront(RuntimeError):
    """The test window lost the foreground; stop injecting input immediately."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "thinkaloud-e2e")
    ap.add_argument("--flash-seconds", type=float, default=12.0)
    ap.add_argument("--flash-interval", type=float, default=2.0)
    ap.add_argument("--device", type=int, default=None, help="audio input device for the recorder")
    ap.add_argument("--report", type=Path, default=ROOT / "docs" / "evidence" / "e2e_report.json")
    ap.add_argument("--keep", action="store_true",
                    help="keep the recorded session (it captures the whole monitor, not just the test window)")
    ap.add_argument("--inputs-only", action="store_true",
                    help="only perform the guarded input script on an already running test bench (--layout); "
                         "used by scripts/app_journey.mjs while the desktop app records")
    ap.add_argument("--layout", type=Path, help="layout.json of a running test bench (with --inputs-only)")
    a = ap.parse_args()

    from pynput.keyboard import Controller as K, Key
    from pynput.mouse import Button, Controller as M
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # same pixel space as the recorder

    a.out.mkdir(parents=True, exist_ok=True)
    if a.inputs_only:
        layout_path, tb = a.layout, None
    else:
        layout_path = a.out / "layout.json"
        layout_path.unlink(missing_ok=True)
        tb = subprocess.Popen([str(ELECTRON), str(ROOT / "scripts" / "testbench")],
                              env={**os.environ, "THINKALOUD_TB_LAYOUT": str(layout_path)})
    for _ in range(100):
        if layout_path.exists():
            break
        time.sleep(0.2)
    layout = json.loads(layout_path.read_text())
    rects, tb_hwnd = layout["rects"], layout["hwnd"]
    if layout.get("always_on_top") is False:
        if tb:
            tb.terminate()
        raise SystemExit("ABORTED before any recorded input: the test window could not be made always-on-top")
    time.sleep(1.0)
    sys.path.insert(0, str(ROOT / "recorder"))
    import winctx

    def guard(point=None, need_focus=True):
        """Abort unless the test window is in front (and is the window under `point`)."""
        fg = winctx.foreground_window() or {}
        if need_focus and fg.get("hwnd") != tb_hwnd:
            raise NotInFront(f"foreground is {fg.get('process')!r}, not the test window")
        if point is not None:
            under = winctx.window_at(*point) or {}
            if under.get("hwnd") != tb_hwnd:
                raise NotInFront(f"window under {point} is {under.get('process')!r}, not the test window")

    # Activate the test window with one click on its own blank area, only if that is what's under it.
    m, k = M(), K()
    blank = (rects["list"][0], rects["list"][1] + rects["list"][3] + 40)
    try:
        guard(blank, need_focus=False)
        m.position = blank
        time.sleep(0.2)
        guard(blank, need_focus=False)
        m.click(Button.left)
        time.sleep(0.8)
        guard(blank)
    except NotInFront as e:
        if tb:
            tb.terminate()
        raise SystemExit(f"ABORTED before any recorded input: {e}")

    def click(name, settle=1.0):
        p = center(rects[name])
        guard(p)
        m.position = p
        time.sleep(0.25)
        guard(p)
        m.click(Button.left)
        time.sleep(settle)

    def type_text(text):
        for ch in text:
            guard()                      # every keystroke: only if the test window has focus
            k.type(ch)
            time.sleep(0.05)

    def scroll(dy):
        guard(m.position)
        m.scroll(0, dy)

    def drive():
        time.sleep(1.5)
        click("cart", 1.5)
        click("search", 0.4)
        type_text(SEARCH)
        time.sleep(0.3)
        guard()
        k.press(Key.enter)
        k.release(Key.enter)
        time.sleep(1.0)
        click("password", 0.5)
        type_text(PASSWORD)
        time.sleep(1.2)
        # Focus reaches the password field by keyboard: masking must come from the focus check.
        click("search", 0.6)
        guard()
        k.press(Key.tab)
        k.release(Key.tab)
        time.sleep(0.8)
        type_text(PASSWORD2)
        time.sleep(1.2)
        p = center(rects["list"])
        guard(p)
        m.position = p
        time.sleep(0.3)
        for _ in range(4):
            scroll(-1)
            time.sleep(0.25)
        time.sleep(1.8)                     # longer than the old 1.5 s split timer
        for _ in range(4):
            scroll(-1)
            time.sleep(0.25)
        time.sleep(0.5)
        for _ in range(3):
            scroll(1)
            time.sleep(0.25)
        time.sleep(1.2)
        n_flash = max(1, int(a.flash_seconds / a.flash_interval))
        for _ in range(n_flash):
            click("flash", a.flash_interval - 0.25)
        click("page2", 2.5)

    if a.inputs_only:
        try:
            drive()
        except NotInFront as e:
            print(f"ABORTED; no further input sent: {e}")
            return 2
        print("inputs done")
        return 0

    cmd = [sys.executable, str(ROOT / "engine" / "engine.py"), "record", "--json", "--countdown", "0",
           "--task", "Add headphones to the cart and open page 2", "--criteria", "Page 2 is open",
           "--out", str(a.out)]
    if a.device is not None:
        cmd += ["--device", str(a.device)]
    rec = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    session = None
    while session is None:
        line = rec.stdout.readline()
        if not line:
            raise SystemExit("recorder exited before starting")
        ev = json.loads(line)
        if ev.get("event") == "started":
            session = Path(ev["dir"])

    try:
        drive()
    except NotInFront as e:
        rec.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
        rec.stdin.flush()
        rec.wait(timeout=120)
        tb.terminate()
        import shutil
        shutil.rmtree(session, ignore_errors=True)  # may contain screenshots of other windows
        raise SystemExit(f"ABORTED mid-run; no further input sent; session deleted: {e}")

    rec.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
    rec.stdin.flush()
    saved = None
    for line in rec.stdout:
        ev = json.loads(line)
        if ev.get("event") == "saved":
            saved = ev
    rc = rec.wait(timeout=120)
    tb.terminate()

    proc = subprocess.run([sys.executable, "-m", "thinkaloud", "--json", str(session)], cwd=ROOT / "processor",
                          capture_output=True, text=True, encoding="utf-8")
    report = check(session, rects, saved, rc, proc)
    report["run"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "flash_seconds": a.flash_seconds,
                     "flash_interval": a.flash_interval, "session_kept": a.keep}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps({k: v for k, v in report.items() if k != "session"}, indent=2), encoding="utf-8")
    fails = [c for c in report["checks"] if not c["ok"]]
    for c in report["checks"]:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + (f"  ({c['detail']})" if c.get("detail") else ""))
    if report.get("sync"):
        print("sync:", json.dumps(report["sync"]))
    if a.keep:
        print(f"session kept: {session}")
    else:
        import shutil
        shutil.rmtree(session, ignore_errors=True)
        print("session deleted (use --keep to keep it)")
    print(f"report: {a.report}")
    return 1 if fails else 0


def check(session: Path, rects: dict, saved: dict, rc: int, proc) -> dict:
    import av
    import numpy as np
    from PIL import Image

    checks = []

    def ok(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": detail})

    meta = json.loads((session / "meta.json").read_text())
    origin = meta["screen"]["left"], meta["screen"]["top"]
    ok("recorder exited cleanly", rc == 0 and saved is not None, f"exit {rc}")
    ok("processor succeeded", proc.returncode == 0, proc.stdout[-300:] + proc.stderr[-300:])
    t = json.loads((session / "trajectory.json").read_text(encoding="utf-8"))
    steps = t["steps"]

    def frame_rect(name):
        r = rects[name]
        return (r[0] - origin[0], r[1] - origin[1], r[2], r[3])

    def pixel(img_path, name):
        x, y, w, h = frame_rect(name)
        im = Image.open(session / img_path).convert("RGB")
        return im.getpixel((x + w // 2, y + h // 2))

    def near(px, rgb, tol=40):
        return all(abs(p - q) <= tol for p, q in zip(px, rgb))

    def click_on(name):
        x, y, w, h = frame_rect(name)
        return [s for s in steps if s["action"]["type"] == "click" and x <= s["action"]["x"] < x + w and y <= s["action"]["y"] < y + h]

    # --- targets -------------------------------------------------------------------------
    cart = click_on("cart")
    ok("one click step on Add to Cart", len(cart) == 1, str(len(cart)))
    if cart:
        tg = cart[0].get("target") or {}
        ok("UIA: Add to Cart is a button", tg.get("role") == "button" and tg.get("name") == "Add to Cart",
           f"{tg.get('status')} {tg.get('role')} {tg.get('name')!r} {tg.get('latency_ms')} ms")
        ok("description names the button", cart[0]["description"] == "Clicked the Add to Cart button", cart[0]["description"])
        ok("UIA: document URL recorded", str(tg.get("url", "")).startswith("file:///"), f"{tg.get('url_status')} {tg.get('url')}")
        b, af = cart[0]["observations"]["before"], cart[0]["observations"]["after"]
        ok("before image finished before the click", b["file"] and b["t_capture_end"] <= cart[0]["t_start"], json.dumps(b)[:160])
        ok("before image shows the un-clicked state (gray swatch)", b["file"] and near(pixel(b["file"], "swatch"), (128, 128, 128)),
           str(pixel(b["file"], "swatch")) if b["file"] else "")
        ok("after image shows the result (green swatch)", af["file"] and near(pixel(af["file"], "swatch"), (0, 170, 0)),
           str(pixel(af["file"], "swatch")) if af["file"] else af.get("reason", ""))
    srch = click_on("search")
    ok("UIA: Search is a text field", srch and (srch[0].get("target") or {}).get("role") == "edit"
       and (srch[0].get("target") or {}).get("name") == "Search", str(srch[0].get("target") if srch else None)[:160])
    typed = [s for s in steps if s["action"]["type"] == "type"]
    ok("search text typed as one step", any(s["action"]["text"] == SEARCH for s in typed), str([s["action"]["text"] for s in typed]))
    ok("Enter is its own step", any(s["action"] == {"type": "key", "key": "enter"} for s in steps))
    pw = click_on("password")
    ok("UIA: password field reported as password", pw and (pw[0].get("target") or {}).get("is_password") is True,
       str((pw[0].get("target") or {}) if pw else None)[:160])
    masked = [s for s in typed if s["action"].get("masked_chars")]
    ok("password typing masked at capture", masked and masked[0]["action"]["masked_chars"] == len(PASSWORD),
       str([s["action"] for s in masked]))
    tab = [k for k, s in enumerate(steps) if s["action"] == {"type": "key", "key": "tab"}]
    ok("Tab into the password field is its own step", tab)
    after_tab = [s["action"] for s in steps[tab[0] + 1:] if s["action"]["type"] == "type"][:1] if tab else []
    ok("password typed after Tab (no click) is masked by the focus check",
       after_tab and after_tab[0].get("masked_chars") == len(PASSWORD2) == len(after_tab[0]["text"])
       and set(after_tab[0]["text"]) == {"•"}, str(after_tab))
    leaked = [p.name for p in session.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl") and
              any(pw in p.read_text(encoding="utf-8", errors="ignore") for pw in (PASSWORD, PASSWORD2))]
    evs = [json.loads(l) for l in (session / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    keys = "".join(e["key"] if e["type"] == "key" and len(e["key"]) == 1 else "|" for e in evs)
    ok("passwords never written to disk (files, and keystrokes reassembled)",
       not leaked and PASSWORD[:6] not in keys and PASSWORD2[:6] not in keys, f"files: {leaked}")

    # --- scroll ----------------------------------------------------------------------------
    sc = [s for s in steps if s["action"]["type"] == "scroll"]
    ok("scroll-pause-scroll-reverse is one step", len(sc) == 1, str(len(sc)))
    if sc:
        runs = [(r["direction"], r["amount"]) for r in sc[0]["action"]["runs"]]
        ok("scroll runs keep direction change", runs == [("down", 8.0), ("up", 3.0)], str(runs))
        ok("raw wheel events kept", len(sc[0]["action"]["events"]) == 11, str(len(sc[0]["action"]["events"])))
        ok("scroll has a settled after-state", sc[0]["observations"]["after"]["status"] == "settled", sc[0]["observations"]["after"]["status"])
    link = click_on("page2")
    ok("UIA: page 2 link", link and (link[0].get("target") or {}).get("role") == "link", str(link[0].get("target") if link else None)[:160])
    if link:
        af = link[0]["observations"]["after"]
        ok("after navigation shows page 2 (blue swatch)", af["file"] and near(pixel(af["file"], "swatch"), (0, 0, 200)),
           str(pixel(af["file"], "swatch")) if af["file"] else af.get("reason", ""))

    # --- video vs events ----------------------------------------------------------------------
    flashes = [s["t_start"] for s in click_on("flash")]
    x, y, w, h = frame_rect("flashpad")
    frames = []
    with av.open(str(session / "screen.mkv")) as c:
        vs = c.streams.video[0]
        for f in c.decode(vs):
            arr = f.to_ndarray(format="rgb24")
            frames.append((float(f.pts * f.time_base), int(arr[y + h // 2, x + w // 2].mean())))
    idx = [json.loads(l) for l in (session / "video_frames.jsonl").read_text().splitlines() if l.strip()]
    cap = {r["pts_ms"]: (r["t_capture_start"], r["t_capture_end"]) for r in idx}
    delays, leads, lags = [], 0, 0
    state = 0  # pad starts black
    for i, tf in enumerate(flashes):
        state ^= 1
        want = 255 if state else 0
        seen = None
        for pts, v in frames:
            c0, c1 = cap.get(round(pts * 1000), (pts, pts))
            if c1 <= tf and abs(v - want) < 60:
                pass  # an old frame may coincidentally match only after an even number of flips; ignore
            if c0 >= tf and abs(v - want) < 60:
                seen = (pts, c0)
                break
        if seen is None:
            lags += 1
            continue
        delays.append(seen[0] - tf)
        before = [fr for fr in frames if cap.get(round(fr[0] * 1000), (fr[0], fr[0]))[1] <= tf]
        if before and abs(before[-1][1] - want) < 60 and i > 0:
            leads += 1  # the state showed up in a frame captured before the click: a timestamp error
    period = 1.0 / meta["capture"]["fps"]
    sync = None
    if delays:
        sync = {"flashes": len(flashes), "measured": len(delays), "not_found": lags, "shown_before_click": leads,
                "delay_s": {"min": round(min(delays), 3), "median": round(statistics.median(delays), 3),
                            "max": round(max(delays), 3)},
                "first_half_median_s": round(statistics.median(delays[: max(1, len(delays) // 2)]), 3),
                "second_half_median_s": round(statistics.median(delays[len(delays) // 2:]), 3),
                "frame_period_s": period, "duration_s": meta["duration_s"],
                "video_dropped": meta["video"]["dropped"], "audio_drift_ppm": meta["audio"].get("clock_drift_ppm")}
        ok("every flash appears in the first frame captured after it (delay <= 1 frame + 80 ms render)",
           lags == 0 and max(delays) <= period + 0.08 + 0.05, json.dumps(sync["delay_s"]))
        ok("no state change appears in a frame captured before its click", leads == 0, str(leads))
        ok("no drift: late-run delays match early-run delays (±1 frame)",
           abs(sync["second_half_median_s"] - sync["first_half_median_s"]) <= period, json.dumps(sync))
    with av.open(str(session / "playback.mp4")) as c:
        mp4_pts = [round(float(p.pts * p.time_base), 3) for p in c.demux(c.streams.video[0]) if p.pts is not None][:20]
    mkv_pts = [round(r["pts_ms"] / 1000, 3) for r in idx][:20]
    ok("playback.mp4 keeps capture timestamps", mp4_pts == mkv_pts, f"{mp4_pts[:4]} vs {mkv_pts[:4]}")
    return {"session": str(session), "checks": checks, "sync": sync, "steps": [s["description"] for s in steps]}


if __name__ == "__main__":
    sys.exit(main())
