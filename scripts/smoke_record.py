"""End-to-end smoke test for recorder/record.py on a real desktop.

Starts the recorder in a subprocess, opens a throwaway Tk window, clicks and
types *inside that window only*, then presses F9 and checks the session output.
Usage: python scripts/smoke_record.py
"""
import json
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

from pynput.keyboard import Controller as Kb, Key
from pynput.mouse import Button, Controller as Mouse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sessions" / "_smoke"

proc = subprocess.Popen([sys.executable, str(ROOT / "recorder" / "record.py"),
                         "--task", "smoke test", "--criteria", "window closed",
                         "--out", str(OUT), "--countdown", "0"])
time.sleep(2.5)

win = tk.Tk()
win.title("thinkaloud smoke test")
win.geometry("500x300+200+200")
entry = tk.Entry(win, width=40)
entry.pack(pady=40)
win.update()
win.lift()
win.attributes("-topmost", True)
win.focus_force()
win.update()

mouse, kb = Mouse(), Kb()
ex, ey = entry.winfo_rootx() + 20, entry.winfo_rooty() + 8
mouse.position = (ex, ey)
mouse.click(Button.left)
win.update()
time.sleep(0.3)
for ch in "hello world":
    kb.type(ch)
    win.update()
    time.sleep(0.05)
kb.press(Key.enter); kb.release(Key.enter)
win.update()
mouse.position = (ex, ey + 80)
mouse.scroll(0, -2)
win.update()
time.sleep(1)
kb.press(Key.f9); kb.release(Key.f9)
win.update()
proc.wait(timeout=20)
win.destroy()

session = sorted(p for p in OUT.iterdir() if p.is_dir())[-1]
events = [json.loads(l) for l in (session / "events.jsonl").read_text().splitlines()]
meta = json.loads((session / "meta.json").read_text())
frames = sorted((session / "frames").glob("*.png"))
typed = "".join(e["key"] for e in events if e["type"] == "key" and len(e["key"]) == 1)
print("types:", [e["type"] for e in events])
print("typed:", repr(typed), "| frames:", len(frames), "| screen:", meta["screen"],
      "| audio:", meta["audio"])
# endswith: anything you type yourself during the test is recorded too
assert typed.endswith("hello world"), typed
assert any(e["type"] == "click" for e in events)
assert any(e["type"] == "scroll" for e in events)
assert all((session / e["frame"]).exists() for e in events if "frame" in e)
print("OK", session)
