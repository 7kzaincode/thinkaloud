"""Think-aloud trajectory recorder.

Captures mouse clicks, keystrokes, scrolls, screenshots and microphone audio
while an expert does a task and narrates it. Press F9 to stop.

Output: sessions/<timestamp>/
    events.jsonl   one JSON object per line, times in seconds since start
    audio.wav      16 kHz mono int16 (missing if no mic / --no-audio)
    frames/*.png   screenshots named by millisecond offset
    meta.json      task, success criteria, screen size, duration, ...

Screenshots are taken at start, at end, on every mouse click, and on Enter/Tab.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import platform
import queue
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import mss
import mss.tools
import numpy as np
from pynput import keyboard, mouse

RECORDER_VERSION = "0.1.0"
SAMPLE_RATE = 16_000
STOP_KEY = keyboard.Key.f9
MODIFIERS = {
    keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
    keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr,
    keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
}
SHIFTS = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}


def make_dpi_aware() -> None:
    """On Windows with display scaling, pynput reports physical pixels only if the
    process is DPI aware. Without this, click coordinates and screenshots disagree."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class Recorder:
    def __init__(self, out_dir: Path, task: str, criteria: str, audio: bool, monitor: int):
        self.out = out_dir
        self.frames_dir = out_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.task = task
        self.criteria = criteria
        self.want_audio = audio
        self.monitor_idx = monitor

        self.t0 = 0.0
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.events_file = open(out_dir / "events.jsonl", "w", encoding="utf-8")
        self.shot_queue: queue.Queue[tuple[float, str] | None] = queue.Queue()
        self.held_mods: set[str] = set()
        self.audio_chunks: list[np.ndarray] = []
        self.audio_error: str | None = None
        self.audio_offset = 0.0  # seconds between t0 and first audio sample
        self.screen = {"w": 0, "h": 0, "left": 0, "top": 0}
        self.n_events = 0

    # ---- helpers -------------------------------------------------------
    def now(self) -> float:
        return round(time.perf_counter() - self.t0, 3)

    def emit(self, event: dict) -> None:
        with self.lock:
            self.events_file.write(json.dumps(event) + "\n")
            self.events_file.flush()
            self.n_events += 1

    def request_shot(self, t: float) -> str:
        name = f"frames/{int(t * 1000):09d}.png"
        self.shot_queue.put((t, name))
        return name

    # ---- screenshot worker (mss handles are per-thread) -----------------
    def screenshot_worker(self) -> None:
        with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
            mon = sct.monitors[self.monitor_idx]
            self.screen = {"w": mon["width"], "h": mon["height"],
                           "left": mon["left"], "top": mon["top"]}
            while True:
                item = self.shot_queue.get()
                if item is None:
                    return
                _, name = item
                path = self.out / name
                if path.exists():  # two triggers in the same millisecond
                    continue
                img = sct.grab(mon)
                mss.tools.to_png(img.rgb, img.size, output=str(path))

    # ---- audio ------------------------------------------------------------
    def start_audio(self):
        if not self.want_audio:
            return None
        try:
            import sounddevice as sd

            def callback(indata, frames, time_info, status):
                self.audio_chunks.append(indata.copy())

            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                    dtype="int16", callback=callback)
            stream.start()
            self.audio_offset = round(time.perf_counter() - self.t0, 3)
            return stream
        except Exception as e:  # no mic, driver issue, missing PortAudio
            self.audio_error = f"{type(e).__name__}: {e}"
            print(f"[warn] audio disabled: {self.audio_error}")
            return None

    def save_audio(self) -> bool:
        if not self.audio_chunks:
            return False
        data = np.concatenate(self.audio_chunks)
        with wave.open(str(self.out / "audio.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(data.tobytes())
        return True

    # ---- input callbacks --------------------------------------------------
    def on_click(self, x, y, button, pressed):
        if not pressed or self.stop_event.is_set():
            return
        t = self.now()
        frame = self.request_shot(t)
        self.emit({"t": t, "type": "click", "x": int(x), "y": int(y),
                   "button": button.name, "frame": frame})

    def on_scroll(self, x, y, dx, dy):
        if self.stop_event.is_set():
            return
        self.emit({"t": self.now(), "type": "scroll", "x": int(x), "y": int(y),
                   "dx": int(dx), "dy": int(dy)})

    def key_name(self, key) -> str:
        if isinstance(key, keyboard.KeyCode):
            if key.char and key.char.isprintable():
                return key.char
            # With Ctrl held, Windows reports control chars (ctrl+c -> '\x03').
            if key.vk is not None and 0x30 <= key.vk <= 0x5A:
                return chr(key.vk).lower()
            return f"vk{key.vk}" if key.vk is not None else "?"
        if key == keyboard.Key.space:
            return " "  # so typed text reads naturally downstream
        return key.name  # e.g. 'enter', 'tab', 'backspace'

    def on_press(self, key):
        if key == STOP_KEY:
            self.stop_event.set()
            return False  # stops the keyboard listener
        if self.stop_event.is_set():
            return
        if key in MODIFIERS:
            self.held_mods.add(key.name.split("_")[0])
            return
        if key in SHIFTS:
            return  # shift is already reflected in the character
        t = self.now()
        name = self.key_name(key)
        event = {"t": t, "type": "key", "key": name}
        if self.held_mods:
            event["mods"] = sorted(self.held_mods)
        if name in ("enter", "tab"):
            event["frame"] = self.request_shot(t)
        self.emit(event)

    def on_release(self, key):
        if key in MODIFIERS:
            self.held_mods.discard(key.name.split("_")[0])

    # ---- main --------------------------------------------------------------
    def run(self) -> None:
        self.t0 = time.perf_counter()
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")

        shooter = threading.Thread(target=self.screenshot_worker, daemon=True)
        shooter.start()
        stream = self.start_audio()
        self.emit({"t": 0.0, "type": "marker", "name": "start",
                   "frame": self.request_shot(0.0)})

        m = mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll)
        k = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        m.start()
        k.start()
        print("Recording. Narrate what you're doing and why. Press F9 to stop.")
        try:
            while not self.stop_event.wait(0.2):
                pass
        except KeyboardInterrupt:
            self.stop_event.set()

        t_end = self.now()
        self.emit({"t": t_end, "type": "marker", "name": "end",
                   "frame": self.request_shot(t_end)})
        m.stop()
        k.stop()
        if stream is not None:
            stream.stop()
            stream.close()
        self.shot_queue.put(None)
        shooter.join(timeout=10)
        self.events_file.close()
        has_audio = self.save_audio()

        meta = {
            "session_id": self.out.name,
            "task": self.task,
            "success_criteria": self.criteria,
            "started_at": started_at,
            "duration_s": t_end,
            "screen": self.screen,
            "audio": {"file": "audio.wav" if has_audio else None,
                      "sample_rate": SAMPLE_RATE, "offset_s": self.audio_offset,
                      "error": self.audio_error},
            "platform": platform.platform(),
            "recorder_version": RECORDER_VERSION,
        }
        (self.out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        n_frames = len(list(self.frames_dir.glob("*.png")))
        print(f"Saved {self.out}  ({t_end:.1f}s, {self.n_events} events, "
              f"{n_frames} frames, audio={'yes' if has_audio else 'no'})")


def main() -> None:
    p = argparse.ArgumentParser(description="Record a think-aloud computer-use session.")
    p.add_argument("--task", help="What the expert is about to do (asked if omitted)")
    p.add_argument("--criteria", help="What 'done' looks like (asked if omitted)")
    p.add_argument("--out", default="sessions", help="Parent folder for sessions")
    p.add_argument("--no-audio", action="store_true", help="Skip microphone capture")
    p.add_argument("--monitor", type=int, default=1,
                   help="mss monitor index (1 = primary, 0 = all monitors)")
    p.add_argument("--countdown", type=int, default=3, help="Seconds before recording starts")
    args = p.parse_args()

    task = args.task if args.task is not None else input("Task: ").strip()
    criteria = (args.criteria if args.criteria is not None
                else input("Done when: ").strip())

    make_dpi_aware()
    out = Path(args.out) / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    for i in range(args.countdown, 0, -1):
        print(f"Starting in {i}...", flush=True)
        time.sleep(1)
    Recorder(out, task, criteria, audio=not args.no_audio, monitor=args.monitor).run()


if __name__ == "__main__":
    main()
