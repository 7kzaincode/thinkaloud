"""Think-aloud trajectory recorder.

Captures what an expert does (clicks, keys, scrolls), what the screen looked
like before and after each action, a low-frame-rate screen video, and their
narration, while they do a real task and talk through it. F9 stops, F8 pauses.

Output: sessions/<timestamp>/  (schema thinkaloud.session/0.2)
    events.jsonl        input events in order; times are seconds on the recording clock
    frames/stills.jsonl which screen captures were kept as PNG stills, with capture times
    frames/fNNNNNN.png  the stills (full resolution, lossless)
    screen.mkv          H.264 screen video, one frame per capture, PTS = capture time
    video_frames.jsonl  per video frame: capture seq, pts, capture start/end
    audio.wav           16 kHz mono int16 narration (missing if no mic / --no-audio)
    meta.json           task, "done when", clocks, coordinate space, media stats, errors

Clock: every timestamp is time.perf_counter() minus the value at recording start
(t0). Screen captures record when the grab started and finished. Audio sample 0
is placed on the same clock from callback arrival times (see AudioCapture).

How screenshots are chosen (the "before"/"after" contract):
    A capture thread grabs the screen every 1/fps seconds into a small ring.
    When an input event starts a potential new action, the most recent frame whose
    grab FINISHED before the event is saved as its "before" still, so a before
    image can never contain the action's own effect. When input goes quiet for
    `settle` seconds, the next frame is saved as a "settled" still (the resulting
    screen, e.g. after a scroll or page load). A final "end" frame is grabbed
    after stop. The processor pairs steps with these stills by timestamp.

App mode (used by the desktop app, see desktop/):
    --json          status as JSON lines on stdout; reads commands from stdin:
                    {"cmd": "stop"}, {"cmd": "pause"}, {"cmd": "resume"},
                    {"cmd": "exclude", "rect": [x, y, w, h]} (clicks and scrolls inside an
                    excluded rect, e.g. the app's own Stop pill, are not recorded)
    --list-devices  print input devices as JSON and exit
    --meter         print mic levels as JSON until stdin closes (mic check)
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
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import mss
import mss.tools
import numpy as np
from pynput import keyboard, mouse

import winctx

RECORDER_VERSION = "0.2.0"
SESSION_SCHEMA = "thinkaloud.session/0.2"
SAMPLE_RATE = 16_000
STOP_KEY = keyboard.Key.f9
PAUSE_KEY = keyboard.Key.f8
JSON_MODE = False

DEFAULT_FPS = 4.0
DEFAULT_SETTLE_S = 0.6       # quiet time before a "settled" still is taken
CANDIDATE_GAP_S = 1.0        # input after this much quiet may start a new action
RING_SIZE = 6                # frames kept in memory for "before" lookups
STILL_QUEUE_MAX = 6          # PNG encodes waiting; bounds memory (~15 MB per 1440p frame)
VIDEO_QUEUE_MAX = 8          # frames waiting for the encoder; overflow drops frames, not sync
UIA_WAIT_S = 1.5             # how long the writer waits for a UI Automation answer
UIA_WAIT_STOPPING_S = 0.2    # ... once stop was requested (don't hang the shutdown)
DRAG_MIN_PX = 5              # press-to-release distance that makes a click a drag
WHEEL_DELTA = 120
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
LLMHF_INJECTED_ANY = 0x1 | 0x2

MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt", keyboard.Key.cmd: "cmd", keyboard.Key.cmd_l: "cmd",
    keyboard.Key.cmd_r: "cmd",
}
SHIFTS = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}


def say(event: str, text: str = "", **fields) -> None:
    """One status message: a JSON line in --json mode, plain text otherwise."""
    try:
        if JSON_MODE:
            print(json.dumps({"event": event, **fields}), flush=True)
        elif text:
            print(text, flush=True)
    except OSError:
        pass  # whoever was reading our output went away; keep recording safely


def rms_level(chunk: np.ndarray) -> float:
    """0..1 loudness for a level meter (-60 dBFS -> 0, 0 dBFS -> 1)."""
    if chunk.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float32) / 32768.0))))
    db = 20 * np.log10(max(rms, 1e-6))
    return round(min(1.0, max(0.0, (db + 60) / 60)), 3)


def list_devices() -> list[dict]:
    """Input devices on the default host API. MME truncates names to 31 chars on
    Windows, so borrow the full name from another host API when one matches."""
    import sounddevice as sd

    default_in = sd.default.device[0]
    devices = sd.query_devices()
    host = devices[default_in]["hostapi"] if default_in is not None and default_in >= 0 else 0
    full_names = [d["name"] for d in devices if d["hostapi"] != host]
    out = []
    for i, d in enumerate(devices):
        if d["hostapi"] != host or d["max_input_channels"] < 1 or "Sound Mapper" in d["name"]:
            continue
        name = next((n for n in full_names
                     if n.startswith(d["name"]) and len(n) > len(d["name"])), d["name"])
        out.append({"index": i, "name": name, "default": i == default_in})
    return out


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


def _mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


@dataclass(frozen=True)
class Frame:
    seq: int
    t0: float          # grab started (recording clock)
    t1: float          # grab finished
    bgra: bytes
    w: int
    h: int


class StillWriter(threading.Thread):
    """Encodes selected frames to PNG off the input path and indexes every request."""

    def __init__(self, out: Path, clock):
        super().__init__(daemon=True, name="stills")
        self.out, self.clock = out, clock
        self.q: queue.Queue = queue.Queue(maxsize=STILL_QUEUE_MAX)
        self.index = open(out / "frames" / "stills.jsonl", "w", encoding="utf-8")
        self.lock = threading.Lock()
        self.files: dict[int, str] = {}      # frame seq -> file, once queued
        self.saved = 0
        self.failed = 0
        self.dropped = 0

    def request(self, frame: Frame | None, kind: str, ref_t: float) -> int | None:
        """Never blocks. Returns the frame seq that will hold the still, or None."""
        if frame is None:
            self._write_index({"kind": kind, "ref_t": round(ref_t, 4), "status": "missing",
                               "reason": "no completed capture before this time"})
            return None
        with self.lock:
            existing = self.files.get(frame.seq)
            if existing is None:
                self.files[frame.seq] = f"frames/f{frame.seq:06d}.png"
        try:
            self.q.put_nowait((None if existing else frame, frame.seq, frame.t0, frame.t1, kind, ref_t))
        except queue.Full:
            if existing is None:
                with self.lock:
                    self.files.pop(frame.seq, None)
            self.dropped += 1
            self._write_index({"kind": kind, "ref_t": round(ref_t, 4), "seq": frame.seq,
                               "status": "dropped", "reason": "still encoder backlog"})
            return None
        return frame.seq

    def _write_index(self, rec: dict) -> None:
        with self.lock:
            try:
                self.index.write(json.dumps(rec) + "\n")
                self.index.flush()
            except (OSError, ValueError):  # disk full / closed: keep capturing, count it
                self.failed += 1

    def run(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                break
            frame, seq, t0, t1, kind, ref_t = item
            rel = self.files.get(seq, f"frames/f{seq:06d}.png")
            rec = {"seq": seq, "file": rel, "kind": kind, "ref_t": round(ref_t, 4),
                   "t_capture_start": round(t0, 4), "t_capture_end": round(t1, 4)}
            if frame is not None:
                try:
                    arr = np.frombuffer(frame.bgra, np.uint8).reshape(frame.h, frame.w, 4)
                    rgb = np.ascontiguousarray(arr[:, :, 2::-1])
                    mss.tools.to_png(rgb.tobytes(), (frame.w, frame.h), level=2,
                                     output=str(self.out / rel))
                    self.saved += 1
                    rec.update(status="ok", w=frame.w, h=frame.h)
                except Exception as e:
                    self.failed += 1
                    rec.update(status="error", reason=f"{type(e).__name__}: {e}")
            else:
                rec["status"] = "ok_duplicate"  # same frame already stored for another request
            self._write_index(rec)
        self.index.close()


class VideoWriter(threading.Thread):
    """Encodes every captured frame to H.264 in Matroska (survives crashes better than
    MP4). Presentation time = capture midpoint in ms, so dropped or late frames leave
    a gap instead of shifting everything after them."""

    def __init__(self, out: Path, fps: float):
        super().__init__(daemon=True, name="video")
        self.out, self.fps = out, fps
        self.q: queue.Queue = queue.Queue(maxsize=VIDEO_QUEUE_MAX)
        self.frames = 0
        self.dropped = 0
        self.error: str | None = None
        self.codec: str | None = None
        self.size: tuple[int, int] | None = None

    def offer(self, frame: Frame) -> None:
        if self.error:
            return
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            self.dropped += 1

    def run(self) -> None:
        container = stream = None
        index = open(self.out / "video_frames.jsonl", "w", encoding="utf-8")
        last_pts = -1
        try:
            import av
        except Exception as e:
            self.error = f"PyAV unavailable: {e}"
        while True:
            f = self.q.get()
            if f is None:
                break
            if self.error:
                continue  # keep draining so memory is released
            try:
                if container is None:
                    w, h = f.w - f.w % 2, f.h - f.h % 2   # yuv420p needs even sizes
                    container = av.open(str(self.out / "screen.mkv"), "w")
                    stream = container.add_stream("libx264", rate=max(1, round(self.fps)))
                    stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
                    stream.codec_context.time_base = Fraction(1, 1000)
                    # no B-frames: packets stay in presentation order, so Matroska keeps a
                    # usable dts on every packet and the remux to MP4 needs no reordering
                    stream.options = {"preset": "veryfast", "crf": "24", "bf": "0",
                                      "g": str(max(1, round(self.fps * 2)))}
                    self.codec, self.size = "h264", (w, h)
                w, h = self.size
                arr = np.frombuffer(f.bgra, np.uint8).reshape(f.h, f.w, 4)[:h, :w]
                vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="bgra")
                pts = max(int(round((f.t0 + f.t1) / 2 * 1000)), last_pts + 1)
                last_pts = pts
                vf.pts, vf.time_base = pts, Fraction(1, 1000)
                for p in stream.encode(vf):
                    container.mux(p)
                index.write(json.dumps({"seq": f.seq, "pts_ms": pts, "t_capture_start": round(f.t0, 4),
                                        "t_capture_end": round(f.t1, 4)}) + "\n")
                self.frames += 1
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
        try:
            if container is not None:
                if not self.error:
                    for p in stream.encode():
                        container.mux(p)
                container.close()
        except Exception as e:
            self.error = self.error or f"{type(e).__name__}: {e}"
        index.close()


class AudioCapture:
    """Microphone to audio.wav, streamed to disk (bounded memory).

    Placing sample 0 on the recording clock: PortAudio's stream clock is the same
    QueryPerformanceCounter clock as perf_counter, but MME reports no ADC timestamps
    in callbacks. Each callback arriving at t_cb after n samples in total proves
    sample 0 was captured no later than t_cb - (n - 1)/sr. The tightest such bound
    (the least-delayed callback) minus the stream's reported input latency is the
    offset. Anchors (t_cb, n) are kept so the drift of the sound card's clock
    against perf_counter can be measured afterwards.
    """

    def __init__(self, out: Path, clock, device: int | None, is_paused):
        self.out, self.clock, self.device, self.is_paused = out, clock, device, is_paused
        self.q: queue.Queue = queue.Queue()
        self.samples = 0
        self.level = 0.0
        self.overflows = 0
        self.bound = None          # min(t_cb - (n-1)/sr)
        self.anchors: list[tuple[float, int]] = []
        self.error: str | None = None
        self.latency = 0.0
        self.stream = None
        self.thread = None
        self.started_t = None
        self.padded_s = 0.0

    def start(self) -> bool:
        try:
            import sounddevice as sd

            def callback(indata, frames, time_info, status):
                t_cb = self.clock()
                # paused is decided here, when the audio was captured, not when it is written
                self.q.put((indata.copy(), t_cb, bool(status.input_overflow), bool(self.is_paused())))

            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=self.device,
                                         dtype="int16", callback=callback)
            self.stream.start()
            self.started_t = self.clock()
            lat = self.stream.latency
            self.latency = float(lat[0] if isinstance(lat, (tuple, list)) else lat)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            say("warning", f"[warn] audio disabled: {self.error}", message=self.error)
            self.stream = None
            return False
        self.thread = threading.Thread(target=self._write, daemon=True, name="audio")
        self.thread.start()
        return True

    def _write(self) -> None:
        wav = wave.open(str(self.out / "audio.wav"), "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        last_anchor = -1.0
        while True:
            item = self.q.get()
            if item is None:
                break
            data, t_cb, overflow, paused = item
            if paused:
                data = np.zeros_like(data)  # keep the sample clock, drop the content
            if overflow and self.bound is not None:
                # samples were lost before this chunk: pad with silence so later audio doesn't shift
                lost = t_cb - (self.bound + (self.samples + len(data)) / SAMPLE_RATE)
                if lost > 0.02:
                    pad = int(lost * SAMPLE_RATE)
                    wav.writeframes(np.zeros(pad, np.int16).tobytes())
                    self.samples += pad
                    self.padded_s += pad / SAMPLE_RATE
            wav.writeframes(data.tobytes())
            self.samples += len(data)
            self.level = rms_level(data)
            self.overflows += int(overflow)
            bound = t_cb - (self.samples - 1) / SAMPLE_RATE
            self.bound = bound if self.bound is None else min(self.bound, bound)
            if t_cb - last_anchor >= 1.0:
                self.anchors.append((round(t_cb, 5), self.samples))
                last_anchor = t_cb
        wav.close()

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                self.error = self.error or f"{type(e).__name__}: {e}"
        if self.thread is not None:
            self.q.put(None)
            self.thread.join(timeout=10)

    def meta(self) -> dict:
        has = self.samples > 0
        offset = None if self.bound is None else round(self.bound - self.latency, 4)
        drift_ppm = None
        if len(self.anchors) >= 3:
            ts = np.array([a[0] for a in self.anchors])
            ns = np.array([a[1] for a in self.anchors], dtype=np.float64)
            slope = np.polyfit(ns, ts, 1)[0]            # seconds of perf_counter per sample
            drift_ppm = round((slope * SAMPLE_RATE - 1.0) * 1e6, 1)
        return {"file": "audio.wav" if has else None, "sample_rate": SAMPLE_RATE,
                "samples": self.samples, "offset_s": offset,
                "offset_method": "min callback bound minus reported input latency",
                "reported_input_latency_s": round(self.latency, 4),
                "clock_drift_ppm": drift_ppm, "overflows": self.overflows,
                "overflow_padding_s": round(self.padded_s, 3),
                "anchors": self.anchors[:: max(1, len(self.anchors) // 400)],
                "error": self.error}


class Recorder:
    def __init__(self, out_dir: Path, task: str, criteria: str, audio: bool = True, monitor: int = 1,
                 device: int | None = None, fps: float = DEFAULT_FPS, video: bool = True,
                 use_uia: bool = True, settle_s: float = DEFAULT_SETTLE_S):
        self.out = out_dir
        (out_dir / "frames").mkdir(parents=True, exist_ok=True)
        self.task, self.criteria = task, criteria
        self.want_audio, self.monitor_idx, self.device = audio, monitor, device
        self.fps, self.want_video, self.use_uia, self.settle_s = fps, video, use_uia, settle_s

        self.t0 = 0.0
        self.stop_event = threading.Event()
        self.capture_stop = threading.Event()
        self.paused = False
        self.pauses: list[list[float]] = []
        self.excluded: list[tuple[int, int, int, int]] = []
        self.inbox: queue.Queue = queue.Queue()
        self.ring: deque[Frame] = deque(maxlen=RING_SIZE)
        self.ring_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.last_class: str | None = None
        self.last_input_t = -1e9
        self.settle_pending = False
        self.held: set[str] = set()
        self.shift = False
        self.burst_focus_rid: int | None = None
        self.focus_may_have_moved = False  # set by Tab/Enter/shortcuts: re-check focus on the next key
        self.focus_epoch = 0                # bumped by every input that can move focus (see UIAWorker)
        self.masking_unavailable = False    # UI Automation failed to start: password fields can't be detected
        self.mod_alone = None               # a modifier pressed with nothing else (e.g. Win opens Start)
        self.pressed: dict[str, tuple[int, int]] = {}  # button -> press position (drag detection)
        self.n_mask_unknown = 0
        self.n_events = 0
        self.n_masked = 0
        self.grab_errors = 0
        self.frames_captured = 0
        self.errors: list[str] = []
        self.screen = {"w": 0, "h": 0, "left": 0, "top": 0}
        self.end_frame_seq: int | None = None
        self.stills = StillWriter(out_dir, self.now)
        self.video = VideoWriter(out_dir, fps) if video else None
        self.audio = AudioCapture(out_dir, self.now, device, lambda: self.paused) if audio else None
        self.uia = None

    # ---- clock & helpers --------------------------------------------------
    def now(self) -> float:
        return time.perf_counter() - self.t0

    def is_excluded(self, x: int, y: int) -> bool:
        return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in self.excluded)

    def latest_frame_before(self, t: float) -> Frame | None:
        with self.ring_lock:
            for f in reversed(self.ring):
                if f.t1 <= t:
                    return f
        return None

    # ---- commands ------------------------------------------------------------
    def set_paused(self, value: bool) -> None:
        if value == self.paused or self.stop_event.is_set():
            return
        t = round(self.now(), 4)
        self.paused = value
        if value:
            self.pauses.append([t, None])
        elif self.pauses:
            self.pauses[-1][1] = t
        self.inbox.put({"t": t, "type": "marker", "name": "pause" if value else "resume"})
        say("paused" if value else "resumed", "Paused (F8 to resume)." if value else "Resumed.", t=t)

    def read_commands(self) -> None:
        """--json mode: the desktop app controls us over stdin."""
        for line in sys.stdin:
            line = line.strip()
            try:
                cmd = json.loads(line) if line.startswith("{") else {"cmd": line}
            except json.JSONDecodeError:
                continue
            c = cmd.get("cmd")
            if c == "stop":
                self.stop_event.set()
            elif c in ("pause", "resume"):
                self.set_paused(c == "pause")
            elif c == "exclude" and len(cmd.get("rect", [])) == 4:
                self.excluded.append(tuple(int(v) for v in cmd["rect"]))
        self.stop_event.set()  # app went away: stop cleanly rather than record forever

    # ---- input hooks (keep these cheap: timestamp, pick a still, enqueue) --------
    def _accept(self) -> bool:
        return not (self.stop_event.is_set() or self.paused)

    def _input(self, ev: dict, cls: str, force_candidate: bool = False) -> None:
        t = self.now()
        ev["t"] = round(t, 4)
        with self.state_lock:
            candidate = (force_candidate or cls == "click" or cls != self.last_class
                         or t - self.last_input_t > CANDIDATE_GAP_S)
            burst_start = cls == "key" and (cls != self.last_class or t - self.last_input_t > CANDIDATE_GAP_S
                                            or self.focus_may_have_moved)
            if cls == "key":
                # Tab, Enter, arrows and shortcuts can move keyboard focus to another field
                self.focus_may_have_moved = not ev.pop("_text", False)
            if cls == "click" or (cls == "key" and self.focus_may_have_moved):
                self.focus_epoch += 1
            epoch = self.focus_epoch
            if cls != "release":
                self.last_class = cls
            # a release (end of a drag) also restarts settling, so the after-state is taken after the drop
            self.last_input_t = t
            self.settle_pending = True
        if cls in ("click", "key", "release"):
            ev["_fg"] = winctx.foreground_hwnd()  # at input time; described later by the writer
        elif cls == "scroll":
            ev["_under"] = winctx.hwnd_at(ev["x"], ev["y"])
        if candidate and cls != "release":
            ev["before_seq"] = self.stills.request(self.latest_frame_before(t), "before", t)
        if self.uia is not None:
            if cls == "click":
                ev["_uia"] = self.uia.submit("point", t, ev["x"], ev["y"])
                self.burst_focus_rid = None  # a click may move focus; re-check on next typing
            elif burst_start or self.burst_focus_rid is None and cls == "key":
                self.burst_focus_rid = self.uia.submit("focus", t, epoch=epoch)
            if cls == "key":
                ev["_focus"] = self.burst_focus_rid
        ev["_epoch"] = epoch
        self.inbox.put(ev)

    def mouse_filter(self, msg, data):
        """Raw wheel deltas (pynput floor-divides them by 120, losing touchpad scrolls)."""
        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            if self._accept() and not self.is_excluded(data.pt.x, data.pt.y):
                raw = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                horizontal = msg == WM_MOUSEHWHEEL
                ev = {"type": "scroll", "x": int(data.pt.x), "y": int(data.pt.y), "raw": raw,
                      "dx": raw / WHEEL_DELTA if horizontal else 0.0,
                      "dy": 0.0 if horizontal else raw / WHEEL_DELTA,
                      "unit": "notch", "injected": bool(data.flags & LLMHF_INJECTED_ANY)}
                if self.held or self.shift:   # Shift+wheel scrolls sideways in most apps
                    ev["mods"] = sorted(self.held | ({"shift"} if self.shift else set()))
                self.mod_alone = None
                self._input(ev, "scroll")
            return False  # handled here; skip pynput's own on_scroll
        return True

    def on_scroll(self, x, y, dx, dy, injected=False):  # non-Windows fallback
        if self._accept() and not self.is_excluded(x, y):
            self._input({"type": "scroll", "x": int(x), "y": int(y), "dx": float(dx), "dy": float(dy),
                         "unit": "notch", "injected": bool(injected)}, "scroll")

    def on_click(self, x, y, button, pressed, injected=False):
        self.mod_alone = None
        if not pressed:
            start = self.pressed.pop(button.name, None)
            if (start and self._accept() and max(abs(x - start[0]), abs(y - start[1])) >= DRAG_MIN_PX):
                self._input({"type": "release", "x": int(x), "y": int(y), "button": button.name,
                             "injected": bool(injected)}, "release")
            return
        if not self._accept() or self.is_excluded(x, y):
            return
        self.pressed[button.name] = (x, y)
        ev = {"type": "click", "x": int(x), "y": int(y), "button": button.name, "injected": bool(injected)}
        if self.held or self.shift:
            ev["mods"] = sorted(self.held | ({"shift"} if self.shift else set()))
        self._input(ev, "click")

    @staticmethod
    def key_name(key) -> tuple[str, str]:
        """(name, kind) where kind is "char" (a typed character), "vk" (a letter/digit
        recovered from the key code, e.g. Ctrl+C reports '\x03') or "special"."""
        if isinstance(key, keyboard.KeyCode):
            if key.char and key.char.isprintable():
                return key.char, "char"
            if key.vk is not None and 0x30 <= key.vk <= 0x5A:
                return chr(key.vk).lower(), "vk"
            return (f"vk{key.vk}" if key.vk is not None else "?"), "special"
        if key == keyboard.Key.space:
            return " ", "char"
        return key.name, "special"

    def on_press(self, key, injected=False):
        if key == STOP_KEY:
            self.stop_event.set()
            return False  # stops the keyboard listener
        if key == PAUSE_KEY:
            self.set_paused(not self.paused)
            return
        if key in MODIFIERS:
            if MODIFIERS[key] in self.held:
                return  # auto-repeat while held
            self.held.add(MODIFIERS[key])
            self.mod_alone = key if self.mod_alone is None and len(self.held) == 1 and not self.shift else False
            return
        if key in SHIFTS:
            self.shift = True
            return  # shift is reflected in the character, or added to special keys below
        self.mod_alone = False
        if not self._accept():
            return
        name, kind = self.key_name(key)
        mods = set(self.held)
        altgr = kind == "char" and {"ctrl", "alt"} <= mods  # AltGr = Ctrl+Alt on Windows: it typed a character
        if altgr:
            mods = set()
        elif self.shift and (kind != "char" or mods):
            mods.add("shift")  # Ctrl+Shift+T is not Ctrl+T; plain Shift is already in the character
        ev = {"type": "key", "key": name, "injected": bool(injected)}
        if mods:
            ev["mods"] = sorted(mods)
        if altgr:
            ev["altgr"] = True
        ev["_text"] = not mods and (len(name) == 1 or name == "backspace")
        self._input(ev, "key", force_candidate=bool(mods) or name in ("enter", "tab"))

    def on_release(self, key, injected=False):
        if key in MODIFIERS:
            if self.mod_alone == key and MODIFIERS[key] in ("cmd", "alt") and self._accept():
                # Win or Alt pressed and released on its own (opens Start / the menu bar)
                self._input({"type": "key", "key": MODIFIERS[key], "injected": bool(injected)}, "key",
                            force_candidate=True)
            self.mod_alone = None
            self.held.discard(MODIFIERS[key])
        elif key in SHIFTS:
            self.shift = False

    # ---- writer: context, UIA answers, masking, ordering ------------------------
    def writer_loop(self) -> None:
        events = open(self.out / "events.jsonl", "w", encoding="utf-8")
        focus_cache: dict[int, dict] = {}
        password_field = False  # last click landed in a password field
        password_epoch = None   # ... and no focus-moving input (click, Tab, Enter, shortcut) happened since
        seq = 0
        while True:
            ev = self.inbox.get()
            if ev is None:
                break
            try:
                ev["seq"] = seq
                seq += 1
                kind = ev["type"]
                fg = ev.pop("_fg", None)
                if kind in ("click", "key", "release"):
                    win = winctx.describe_hwnd(fg) if fg else winctx.foreground_window()
                elif kind == "scroll":
                    under = ev.pop("_under", None)
                    win = winctx.describe_hwnd(under) if under else winctx.window_at(ev["x"], ev["y"])
                else:
                    win = None
                if win:
                    ev["window"] = win
                wait = UIA_WAIT_STOPPING_S if self.stop_event.is_set() else UIA_WAIT_S
                rid = ev.pop("_uia", None)
                epoch = ev.pop("_epoch", None)
                if rid is not None:
                    ev["target"] = self.uia.result(rid, wait)
                    password_field = bool(ev["target"].get("is_password"))
                    password_epoch = epoch if password_field else None
                frid = ev.pop("_focus", None)
                if kind == "key":
                    focus = None
                    if frid is not None:
                        if frid not in focus_cache:
                            focus_cache[frid] = self.uia.result(frid, wait)
                            if len(focus_cache) > 64:
                                focus_cache.pop(next(iter(focus_cache)))
                        focus = focus_cache[frid]
                    # UI Automation answers can only ADD masking. A focus answer is never retried (a
                    # later answer may describe where the page moved focus to, e.g. auto-advancing PIN
                    # boxes), and typing right after clicking a password field stays masked until the
                    # user moves focus themselves, whatever a focus lookup says.
                    reason = None
                    clicked_password = password_epoch is not None and epoch == password_epoch
                    if focus and focus.get("status") == "ok":
                        ev["focus"] = {k: focus.get(k) for k in ("role", "name", "is_password", "status")}
                        if focus.get("is_password"):
                            reason = "password field (UI Automation IsPassword)"
                        elif clicked_password:
                            reason = "typed after clicking a password field"
                    elif focus is not None and focus.get("status") == "unavailable":
                        # UI Automation never started: same as --no-uia (no field-based masking; QC flags it)
                        self.masking_unavailable = True
                        if password_field:
                            reason = "typed after clicking a password field"
                    elif focus is not None:
                        # UI Automation is running but couldn't say where focus is: fail closed
                        ev["focus"] = {"status": focus.get("status")}
                        reason = "focused field could not be checked"
                    elif password_field:
                        reason = "typed after clicking a password field"
                    if reason and len(ev["key"]) == 1:
                        ev["key"] = "•"
                        ev["masked"] = True
                        ev["mask_reason"] = reason
                        self.n_masked += 1
                        if reason == "focused field could not be checked":
                            self.n_mask_unknown += 1
                ev.pop("_text", None)
                events.write(json.dumps(ev) + "\n")
                events.flush()
                self.n_events += 1
            except Exception as e:  # never lose the rest of the stream over one event
                self.errors.append(f"writer: {type(e).__name__}: {e}")
        events.close()

    # ---- screen capture ------------------------------------------------------------
    def capture_loop(self) -> None:
        period = 1.0 / self.fps
        consecutive_errors = 0
        try:
            sct = _mss()
        except Exception as e:
            self.errors.append(f"capture init: {e}")
            return
        with sct:
            mon = sct.monitors[self.monitor_idx]
            self.screen = {"w": mon["width"], "h": mon["height"], "left": mon["left"], "top": mon["top"]}
            seq = 0

            def grab() -> Frame | None:
                nonlocal seq, consecutive_errors
                t0 = self.now()
                try:
                    img = sct.grab(mon)
                except Exception as e:
                    self.grab_errors += 1
                    consecutive_errors += 1
                    if consecutive_errors in (1, 20):
                        self.errors.append(f"grab: {type(e).__name__}: {e}")
                    return None
                consecutive_errors = 0
                f = Frame(seq, t0, self.now(), img.bgra, img.width, img.height)
                seq += 1
                self.frames_captured += 1
                with self.ring_lock:
                    self.ring.append(f)
                if self.video is not None:
                    self.video.offer(f)
                return f

            first = grab()
            if first is not None:
                self.stills.request(first, "start", first.t0)
            next_t = self.now() + period
            while not self.capture_stop.is_set():
                delay = next_t - self.now()
                if delay > 0 and self.capture_stop.wait(delay):
                    break
                next_t += period
                if next_t < self.now():
                    next_t = self.now() + period  # fell behind: skip ticks, don't burst
                if self.paused:
                    continue
                f = grab()
                if f is None:
                    continue
                with self.state_lock:
                    settle_ref = None
                    if self.settle_pending and f.t0 - self.last_input_t >= self.settle_s:
                        self.settle_pending = False
                        settle_ref = self.last_input_t
                if settle_ref is not None:
                    self.stills.request(f, "settled", settle_ref)
            # Final state after the last action: grabbed after stop, never before it.
            end = grab()
            if end is not None:
                self.stills.request(end, "end", end.t0)
                self.end_frame_seq = end.seq

    # ---- main --------------------------------------------------------------------------
    def run(self) -> None:
        self.t0 = time.perf_counter()
        started_wall = datetime.now().astimezone()
        if self.use_uia and sys.platform == "win32":
            from uia import UIAWorker

            self.uia = UIAWorker(self.now, focus_epoch=lambda: self.focus_epoch)
            self.uia.start()
            self.uia.ready.wait(5)
            if self.uia.available is False:
                say("warning", f"[warn] UI Automation unavailable ({self.uia.error}): password fields can't be "
                    "detected, so typed passwords are recorded as typed", short="passwords not masked",
                    message="UI Automation did not start: password fields can't be detected. Don't type passwords "
                            "while recording (use Pause).")
        else:
            say("warning", "[warn] --no-uia: password fields can't be detected; typed passwords are recorded as typed",
                short="passwords not masked",
                message="UI Automation is off: password fields can't be detected. Don't type passwords while recording.")
        self.stills.start()
        if self.video is not None:
            self.video.start()
        writer = threading.Thread(target=self.writer_loop, daemon=True, name="writer")
        writer.start()
        capture = threading.Thread(target=self.capture_loop, daemon=True, name="capture")
        capture.start()
        has_audio = self.audio.start() if self.audio is not None else False
        self.inbox.put({"t": 0.0, "type": "marker", "name": "start"})

        m = mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll,
                           win32_event_filter=self.mouse_filter)
        k = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        m.start()
        k.start()
        if JSON_MODE:
            threading.Thread(target=self.read_commands, daemon=True).start()
        say("started", "Recording. Narrate what you're doing and why. F8 pauses, F9 stops.",
            dir=str(self.out.resolve()), session_id=self.out.name)
        try:
            while not self.stop_event.wait(0.25):
                say("status", t=round(self.now(), 2), events=self.n_events, paused=self.paused,
                    level=self.audio.level if self.audio else 0.0,
                    video_dropped=self.video.dropped if self.video else 0)
        except KeyboardInterrupt:
            self.stop_event.set()

        t_end = round(self.now(), 4)
        if self.paused and self.pauses:
            self.pauses[-1][1] = t_end
        say("stopping", t=t_end)
        m.stop()
        k.stop()
        self.capture_stop.set()
        capture.join(timeout=10)
        self.inbox.put({"t": t_end, "type": "marker", "name": "end", "end_seq": self.end_frame_seq})
        self.inbox.put(None)
        writer.join(timeout=30)
        if self.uia is not None:
            self.uia.stop()
        for worker in (w for w in (self.video, self.stills) if w is not None):
            try:
                worker.q.put(None, timeout=20)
            except queue.Full:
                self.errors.append(f"{worker.name}: worker did not drain its queue before stop")
            worker.join(timeout=60)
        if self.audio is not None:
            self.audio.stop()

        dpi = winctx.monitor_scale(self.screen["left"] + 1, self.screen["top"] + 1)
        meta = {
            "schema": SESSION_SCHEMA,
            "session_id": self.out.name,
            "task": self.task,
            "success_criteria": self.criteria,
            "started_at": started_wall.isoformat(timespec="seconds"),
            "duration_s": t_end,
            "clock": {"source": "time.perf_counter", "unit": "s",
                      "origin": "recording start (t0)", "t0_perf_counter": round(self.t0, 6)},
            "screen": self.screen,
            "coordinate_space": {
                "space": "physical screen pixels (per-monitor DPI aware)",
                "frame_origin": [self.screen["left"], self.screen["top"]],
                "frame_size": [self.screen["w"], self.screen["h"]],
                "monitor_index": self.monitor_idx,
                "monitor_dpi_scale": dpi,
                "process_dpi_awareness": winctx.process_dpi_awareness(),
                "note": "event x/y are screen coordinates; subtract frame_origin for frame pixels",
            },
            "capture": {"fps": self.fps, "frames": self.frames_captured, "grab_errors": self.grab_errors,
                        "settle_s": self.settle_s, "candidate_gap_s": CANDIDATE_GAP_S,
                        "ring_size": RING_SIZE, "double_click_time_s": winctx.double_click_time_s(),
                        "double_click_size_px": winctx.double_click_size_px(), "drag_min_px": DRAG_MIN_PX},
            "stills": {"index": "frames/stills.jsonl", "saved": self.stills.saved,
                       "failed": self.stills.failed, "dropped": self.stills.dropped},
            "video": ({"file": "screen.mkv" if self.video.frames else None, "codec": self.video.codec,
                       "frames": self.video.frames, "dropped": self.video.dropped,
                       "index": "video_frames.jsonl", "pts": "capture midpoint, ms on recording clock",
                       "error": self.video.error} if self.video else {"file": None, "error": "disabled"}),
            "audio": (self.audio.meta() if self.audio else
                      {"file": None, "sample_rate": SAMPLE_RATE, "offset_s": None, "error": "disabled"}),
            "uia": {"enabled": self.uia is not None,
                    "available": getattr(self.uia, "available", None),
                    "error": getattr(self.uia, "error", None)},
            "input": {"events": self.n_events, "masked_keys": self.n_masked,
                      "masked_focus_unknown": self.n_mask_unknown, "pauses": self.pauses,
                      "password_masking": ("off" if self.uia is None else
                                           "unavailable" if self.masking_unavailable or
                                           getattr(self.uia, "available", None) is False else "on"),
                      "hotkeys": {"stop": "F9", "pause": "F8"}},
            "errors": self.errors,
            "platform": platform.platform(),
            "recorder_version": RECORDER_VERSION,
        }
        (self.out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        say("saved", f"Saved {self.out}  ({t_end:.1f}s, {self.n_events} events, "
            f"{self.stills.saved} stills, {self.video.frames if self.video else 0} video frames, "
            f"audio={'yes' if has_audio else 'no'})",
            dir=str(self.out.resolve()), session_id=self.out.name, duration_s=t_end,
            events=self.n_events, frames=self.stills.saved,
            video_frames=self.video.frames if self.video else 0, audio=has_audio,
            errors=self.errors)


def run_meter(device: int | None) -> None:
    """Print mic levels ~10x/second until stdin closes or says stop."""
    import sounddevice as sd

    stop = threading.Event()

    def wait_stdin():
        for line in sys.stdin:
            if "stop" in line:
                break
        stop.set()

    threading.Thread(target=wait_stdin, daemon=True).start()
    level = [0.0]

    def callback(indata, frames, time_info, status):
        level[0] = rms_level(indata)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=device,
                            dtype="int16", callback=callback):
            while not stop.wait(0.1):
                say("level", level=level[0])
    except Exception as e:
        say("error", message=f"{type(e).__name__}: {e}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Record a think-aloud computer-use session.")
    p.add_argument("--task", help="What the expert is about to do (asked if omitted)")
    p.add_argument("--criteria", help="What 'done' looks like (asked if omitted)")
    p.add_argument("--out", default="sessions", help="Parent folder for sessions")
    p.add_argument("--no-audio", action="store_true", help="Skip microphone capture")
    p.add_argument("--no-video", action="store_true", help="Skip the screen video")
    p.add_argument("--no-uia", action="store_true", help="Skip UI Automation target lookups")
    p.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Screen capture rate (default 4)")
    p.add_argument("--settle", type=float, default=DEFAULT_SETTLE_S,
                   help="Quiet seconds before a 'settled' screenshot (default 0.6)")
    p.add_argument("--monitor", type=int, default=1,
                   help="mss monitor index (1 = primary, 0 = all monitors)")
    p.add_argument("--countdown", type=int, default=3, help="Seconds before recording starts")
    p.add_argument("--device", type=int, help="Input device index (see --list-devices)")
    p.add_argument("--json", action="store_true", help="App mode: JSON status out, commands in")
    p.add_argument("--list-devices", action="store_true", help="Print input devices as JSON")
    p.add_argument("--meter", action="store_true", help="Print mic levels as JSON (mic check)")
    args = p.parse_args(argv)

    global JSON_MODE
    JSON_MODE = args.json or args.meter
    if args.list_devices:
        print(json.dumps(list_devices()))
        return
    if args.meter:
        run_meter(args.device)
        return
    if JSON_MODE and (args.task is None or args.criteria is None):
        p.error("--json needs --task and --criteria")
    if not 0.5 <= args.fps <= 30:
        p.error("--fps must be between 0.5 and 30")
    task = args.task if args.task is not None else input("Task: ").strip()
    criteria = (args.criteria if args.criteria is not None
                else input("Done when: ").strip())

    make_dpi_aware()
    out = Path(args.out) / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    for i in range(args.countdown, 0, -1):
        say("countdown", f"Starting in {i}...", n=i)
        time.sleep(1)
    Recorder(out, task, criteria, audio=not args.no_audio, monitor=args.monitor,
             device=args.device, fps=args.fps, video=not args.no_video,
             use_uia=not args.no_uia, settle_s=args.settle).run()


if __name__ == "__main__":
    main()
