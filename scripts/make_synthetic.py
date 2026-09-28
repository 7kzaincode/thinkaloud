"""Generate a synthetic recorder-0.2 session so the processor and viewer work without recording.

Scenario: find and select the cheapest *nonstop* YYZ -> SFO flight on a fake travel
site. It deliberately contains things the pipeline must handle: a typed email
(QC flags + redacts it), a password typed into a password field (masked at capture
time, as the real recorder does via UI Automation), a scroll that pauses for 1.8 s
and changes direction (one step, two runs), typing followed 20 ms later by Tab
(no after-state can exist), a click with no narration after a long silent pause.

Stills, the 4 fps screen video and their capture timestamps are produced with the
same rules the recorder uses (see recorder/record.py), so the processor treats this
exactly like a real 0.2 session. Narration audio is synthesized with Windows
speech (SAPI) when available, otherwise the session has no audio.

Writes samples/synthetic-flight/{meta.json, events.jsonl, transcript.json,
frames/*.png, frames/stills.jsonl, screen.mkv, video_frames.jsonl, audio.wav?}
Usage: python scripts/make_synthetic.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
FPS = 4.0
SETTLE = 0.6
CANDIDATE_GAP = 1.0
DURATION = 71.5
OUT = Path(__file__).resolve().parent.parent / "samples" / "synthetic-flight"
HWND = 0x2A10E
PROCESS = "chrome.exe"


def font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"),
                 ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F = {"s": font(18), "m": font(22), "b": font(22, True), "l": font(34, True), "url": font(17)}
INK, MUTED, LINE, ACCENT, BG = (24, 26, 31), (110, 114, 122), (218, 220, 225), (22, 92, 210), (247, 247, 245)

# ---------------------------------------------------------------- page state over time
FLIGHTS = [
    ("United", "6:05 → 11:40", "1 stop · ORD", "$268"),
    ("Air Canada", "8:30 → 11:02", "Nonstop", "$312"),
    ("WestJet", "10:15 → 12:50", "Nonstop", "$329"),
    ("Delta", "7:00 → 13:25", "1 stop · MSP", "$274"),
    ("Alaska", "12:40 → 15:10", "Nonstop", "$341"),
]
EMAIL = "zain.demo@example.com"
PASSWORD_LEN = 12

# (time the page changed, page, extra) - the screen shows the last entry <= t
PAGES = [
    (0.0, "signin", {}), (10.0, "search", {}), (19.6, "results", {"sort": "Recommended"}),
    (25.15, "results", {"sort": "Recommended", "menu": True}),
    (26.4, "results", {"sort": "Price: low to high"}),
    (27.45, "results", {"sort": "Price: low to high", "scroll": 60}),
    (27.95, "results", {"sort": "Price: low to high", "scroll": 150}),
    (29.75, "results", {"sort": "Price: low to high", "scroll": 120}),
    (35.5, "results", {"sort": "Price: low to high", "nonstop": True}),
    (38.6, "results", {"sort": "Price: low to high", "nonstop": True, "selected": 0}),
    (62.3, "results", {"sort": "Price: low to high", "nonstop": True, "selected": 0, "details": True}),
    (66.0, "checkout", {}),
]
URLS = {"signin": "https://farefinder.example/signin", "search": "https://farefinder.example/",
        "results": "https://farefinder.example/flights?from=YYZ&to=SFO&date=2026-10-17",
        "checkout": "https://farefinder.example/checkout"}
TITLES = {"signin": "Sign in - Farefinder", "search": "Farefinder", "results": "YYZ to SFO - Farefinder",
          "checkout": "Review your trip - Farefinder"}


def typed_at(t: float, start: float, n: int, dt: float = 0.09) -> int:
    return 0 if t < start else min(n, int((t - start) / dt) + 1)


def page_at(t: float) -> tuple[str, dict]:
    cur = PAGES[0]
    for p in PAGES:
        if p[0] <= t:
            cur = p
    return cur[1], cur[2]


def chrome(title_url: str, heading: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 86], fill=(232, 233, 236))
    for i, c in enumerate([(236, 95, 88), (245, 190, 80), (98, 197, 84)]):
        d.ellipse([18 + i * 24, 16, 32 + i * 24, 30], fill=c)
    d.rounded_rectangle([110, 44, W - 110, 76], 8, fill="white")
    d.text((128, 49), title_url, font=F["url"], fill=MUTED)
    d.text((80, 120), "farefinder", font=F["l"], fill=ACCENT)
    d.text((80, 175), heading, font=F["b"], fill=INK)
    return img, d


def field(d, x, y, label, value="", w=420, focus=False, secret=False):
    d.text((x, y), label, font=F["s"], fill=MUTED)
    d.rounded_rectangle([x, y + 28, x + w, y + 78], 6, fill="white",
                        outline=ACCENT if focus else LINE, width=2 if focus else 1)
    d.text((x + 14, y + 40), "•" * len(value) if secret else value, font=F["m"], fill=INK)


def button(d, x, y, text, w=200, primary=True):
    d.rounded_rectangle([x, y, x + w, y + 50], 6, fill=ACCENT if primary else "white", outline=ACCENT)
    d.text((x + 20, y + 12), text, font=F["b"], fill="white" if primary else ACCENT)


def render(t: float) -> Image.Image:
    page, x = page_at(t)
    if page == "signin":
        img, d = chrome(URLS[page], "Sign in to see member fares")
        n_email = typed_at(t, 4.6, len(EMAIL))
        n_pw = typed_at(t, 7.8, PASSWORD_LEN)
        field(d, 80, 240, "Email", EMAIL[:n_email], focus=4.1 <= t < 7.4)
        field(d, 80, 340, "Password", "x" * n_pw, focus=t >= 7.4, secret=True)
        button(d, 80, 450, "Sign in")
        return img
    if page == "search":
        img, d = chrome(URLS[page], "Where to?")
        frm = "YYZ"[:typed_at(t, 14.0, 3)]
        to = "SFO"[:typed_at(t, 15.8, 3)]
        field(d, 80, 240, "From", (frm + (" Toronto Pearson" if frm == "YYZ" and t > 14.6 else "")),
              w=300, focus=13.6 <= t < 14.2)
        field(d, 400, 240, "To", (to + (" San Francisco" if to == "SFO" and t > 16.4 else "")),
              w=300, focus=14.2 <= t < 17.3)
        field(d, 720, 240, "Depart", "Fri Oct 17" if t >= 17.6 else "", w=260, focus=t >= 17.3)
        button(d, 1000, 268, "Search", w=160)
        return img
    if page == "results":
        rows = sorted(FLIGHTS, key=lambda f: f[3]) if x.get("sort") != "Recommended" else \
            [FLIGHTS[3], FLIGHTS[4], FLIGHTS[0], FLIGHTS[2], FLIGHTS[1]]
        if x.get("nonstop"):
            rows = [f for f in rows if f[2] == "Nonstop"]
        img, d = chrome(URLS[page], f"{19 if x.get('nonstop') else 48} {'nonstop ' if x.get('nonstop') else ''}flights")
        d.text((80, 215), "YYZ → SFO · Fri Oct 17 · 1 adult", font=F["s"], fill=MUTED)
        d.rounded_rectangle([W - 420, 170, W - 80, 214], 6, fill="white", outline=LINE)
        d.text((W - 404, 180), f"Sort: {x.get('sort')}", font=F["s"], fill=INK)
        on = x.get("nonstop")
        d.rounded_rectangle([80, 250, 330, 290], 20, fill=ACCENT if on else "white", outline=ACCENT)
        d.text((104, 258), ("On: " if on else "") + "Nonstop only", font=F["s"], fill="white" if on else ACCENT)
        y = 320 - x.get("scroll", 0)
        for i, (air, times, stops, price) in enumerate(rows):
            sel = x.get("selected") == i
            d.rounded_rectangle([80, y, W - 80, y + 96], 10, fill=(236, 243, 255) if sel else "white",
                                outline=ACCENT if sel else LINE, width=2 if sel else 1)
            d.text((110, y + 18), air, font=F["b"], fill=INK)
            d.text((110, y + 54), times, font=F["s"], fill=MUTED)
            d.text((560, y + 34), stops, font=F["m"], fill=(40, 130, 70) if stops == "Nonstop" else MUTED)
            d.text((W - 240, y + 30), price, font=F["l"], fill=INK)
            if sel:
                button(d, 1040, y + 23, "Select", w=140)
                d.text((1240, y + 36), "Details", font=F["s"], fill=ACCENT)
                if x.get("details"):
                    d.rounded_rectangle([900, y + 100, W - 80, y + 190], 8, fill="white", outline=LINE)
                    d.text((920, y + 115), "Economy Standard · 1 carry-on · seat selection included",
                           font=F["s"], fill=INK)
            y += 112
        if x.get("menu"):
            d.rounded_rectangle([W - 420, 216, W - 80, 346], 6, fill="white", outline=LINE)
            for i, o in enumerate(["Recommended", "Price: low to high", "Duration"]):
                d.text((W - 404, 226 + i * 38), o, font=F["s"], fill=INK)
        return img
    img, d = chrome(URLS["checkout"], "Review your trip")
    d.rounded_rectangle([80, 230, 900, 420], 10, fill="white", outline=LINE)
    d.text((110, 255), "Air Canada · AC 759 · Nonstop", font=F["b"], fill=INK)
    d.text((110, 300), "Fri Oct 17 · 8:30 YYZ → 11:02 SFO · 5h 32m", font=F["m"], fill=MUTED)
    d.text((110, 355), "Total  $312.00 CAD", font=F["l"], fill=INK)
    button(d, 80, 450, "Continue to payment", w=300)
    return img


# ------------------------------------------------------------------ input events
def target(role, name, rect, t, page, latency=14.0):
    return {"status": "ok", "role": role, "control_type_id": None, "name": name, "automation_id": "",
            "class_name": "", "framework": "Chrome", "rect": rect, "is_password": False,
            "pid": 4242, "hit": None, "url_status": "ok", "url": URLS[page], "source": "uia",
            "lookup_started_t": round(t + 0.002, 4), "lookup_done_t": round(t + latency / 1000, 4),
            "latency_ms": latency}


def keys(t0, text, masked=False, dt=0.09):
    out = []
    for i, ch in enumerate(text):
        ev = {"t": round(t0 + i * dt, 4), "type": "key", "key": "•" if masked else ch}
        if masked:
            ev["masked"] = True
            ev["mask_reason"] = "password field (UI Automation IsPassword)"
            ev["focus"] = {"role": "edit", "name": "Password", "is_password": True, "status": "ok"}
        else:
            ev["focus"] = {"role": "edit", "name": "", "is_password": False, "status": "ok"}
        out.append(ev)
    return out


def click(t, x, y, role, name, rect, page):
    return {"t": t, "type": "click", "x": x, "y": y, "button": "left",
            "target": target(role, name, rect, t, page)}


def build_events() -> list[dict]:
    ev = [{"t": 0.0, "type": "marker", "name": "start"}]
    ev.append(click(4.1, 290, 293, "edit", "Email", [80, 268, 420, 50], "signin"))
    ev += keys(4.6, EMAIL)
    ev.append(click(7.4, 290, 393, "edit", "Password", [80, 368, 420, 50], "signin"))
    ev[-1]["target"]["is_password"] = True
    ev += keys(7.8, "x" * PASSWORD_LEN, masked=True)
    ev.append({"t": 9.6, "type": "key", "key": "enter"})
    ev.append(click(13.6, 230, 293, "combobox", "From", [80, 268, 300, 50], "search"))
    ev += keys(14.0, "YYZ")
    ev.append({"t": 14.2, "type": "key", "key": "tab"})       # 20 ms after typing: no after-state possible
    ev += keys(15.8, "SFO")
    ev.append(click(17.3, 850, 293, "combobox", "Depart", [720, 268, 260, 50], "search"))
    ev.append(click(19.2, 1080, 293, "button", "Search", [1000, 268, 160, 50], "search"))
    ev.append(click(24.9, 1350, 192, "combobox", "Sort: Recommended", [1180, 170, 340, 44], "results"))
    ev.append(click(26.1, 1300, 283, "list item", "Price: low to high", [1180, 256, 340, 38], "results"))
    for t, dy in ((27.4, -2.0), (27.9, -3.0), (29.7, 1.0)):   # 1.8 s pause, then back up: still one step
        ev.append({"t": t, "type": "scroll", "x": 800, "y": 600, "raw": int(dy * 120), "dx": 0.0, "dy": dy,
                   "unit": "notch"})
    ev.append(click(35.2, 200, 270, "checkbox", "Nonstop only", [80, 250, 250, 40], "results"))
    ev.append(click(38.4, 700, 368, "list item", "Air Canada 8:30 to 11:02, nonstop, $312", [80, 320, 1440, 96],
                    "results"))
    ev.append(click(62.0, 1270, 368, "link", "Details", [1240, 356, 64, 22], "results"))  # silent detour
    ev.append(click(65.6, 1110, 368, "button", "Select", [1040, 343, 140, 50], "results"))
    ev.sort(key=lambda e: e["t"])
    for i, e in enumerate(ev):
        e["seq"] = i
        page, _ = page_at(e["t"])
        if e["type"] != "marker":
            e["injected"] = False
            e["window"] = {"hwnd": HWND, "title": f"{TITLES[page]} - Google Chrome", "process": PROCESS,
                           "pid": 4242}
    ev.append({"t": DURATION, "type": "marker", "name": "end", "seq": len(ev)})
    return ev


TRANSCRIPT = [
    (0.6, 3.6, "First I'm signing in, because this site only shows member fares when you're logged in."),
    (10.1, 13.2, "Now the search. I use airport codes, not city names, since Toronto has two airports and I want Pearson."),
    (16.4, 17.1, "Then the date, the seventeenth."),
    (19.6, 23.9, "Results default to recommended, which is mostly sponsored placements. So I sort by price."),
    (29.6, 34.8, "The cheapest one has a stop in Chicago. The task says direct, so I filter to nonstop instead of just grabbing the top result."),
    (35.9, 38.0, "Air Canada, three twelve, nonstop. That's the one."),
    (66.4, 70.2, "Okay, it's in checkout at three hundred twelve, nonstop, on the seventeenth. That matches what I was asked for."),
]


# ------------------------------------------------------------------ captures (recorder rules)
def capture_schedule() -> list[tuple[int, float, float]]:
    frames, i, t = [], 0, 0.1
    while t < DURATION:
        frames.append((i, round(t, 4), round(t + 0.03, 4)))
        i, t = i + 1, t + 1 / FPS
    frames.append((i, round(DURATION + 0.02, 4), round(DURATION + 0.05, 4)))  # "end": grabbed after stop
    return frames


def choose_stills(events, frames):
    """Replicates recorder/record.py: before-stills on candidate events, settled stills, start, end."""
    reqs = [(frames[0], "start", frames[0][1])]
    last_cls, last_t, settle_pending = None, -1e9, False
    inputs = [e for e in events if e["type"] != "marker"]
    fi = 0
    for e in inputs:
        # settled stills for frames captured before this event
        while fi < len(frames) - 1 and frames[fi][1] < e["t"]:
            f = frames[fi]
            if settle_pending and f[1] - last_t >= SETTLE and f[2] <= e["t"]:
                reqs.append((f, "settled", last_t))
                settle_pending = False
            fi += 1
        cls = "click" if e["type"] == "click" else e["type"]
        force = e.get("key") in ("enter", "tab")
        if force or cls == "click" or cls != last_cls or e["t"] - last_t > CANDIDATE_GAP:
            before = [f for f in frames[:-1] if f[2] <= e["t"]]
            reqs.append((before[-1], "before", e["t"]))
            e["before_seq"] = before[-1][0]
        last_cls, last_t, settle_pending = cls, e["t"], True
    for f in frames[fi:-1]:
        if settle_pending and f[1] - last_t >= SETTLE:
            reqs.append((f, "settled", last_t))
            settle_pending = False
            break
    reqs.append((frames[-1], "end", frames[-1][1]))
    return reqs


def synthesize_audio(out: Path) -> bool:
    """Narration via Windows SAPI, placed at each segment's start time. Returns False elsewhere."""
    if sys.platform != "win32":
        return False
    total = np.zeros(int((DURATION + 1) * 16000), np.int16)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (a, _b, text) in enumerate(TRANSCRIPT):
            wav = Path(tmp) / f"s{i}.wav"
            ps = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
                  "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono); "
                  f"$s.Rate=2; $s.SetOutputToWaveFile('{wav}',$f); $s.Speak(@'\n{text}\n'@); $s.Dispose()")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
            if r.returncode != 0 or not wav.exists():
                return False
            with wave.open(str(wav)) as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
            s0 = int(a * 16000)
            n = min(len(pcm), len(total) - s0)
            total[s0:s0 + n] = pcm[:n]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(total.tobytes())
    return True


def main() -> None:
    import av

    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.iterdir():  # clear contents, not the folder (Windows may hold a handle on it)
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    (OUT / "frames").mkdir()
    events = build_events()
    frames = capture_schedule()
    reqs = choose_stills(events, frames)

    # stills
    saved, index = set(), []
    for (seq, t0, t1), kind, ref in reqs:
        rel = f"frames/f{seq:06d}.png"
        rec = {"seq": seq, "file": rel, "kind": kind, "ref_t": round(ref, 4), "t_capture_start": t0,
               "t_capture_end": t1}
        if seq in saved:
            rec["status"] = "ok_duplicate"
        else:
            render(t0).save(OUT / rel, optimize=True)
            saved.add(seq)
            rec.update(status="ok", w=W, h=H)
        index.append(rec)
    (OUT / "frames" / "stills.jsonl").write_text("".join(json.dumps(r) + "\n" for r in index), encoding="utf-8")

    # video: one frame per capture, PTS = capture midpoint (ms), same as the recorder
    container = av.open(str(OUT / "screen.mkv"), "w")
    stream = container.add_stream("libx264", rate=4)
    stream.width, stream.height, stream.pix_fmt = W, H, "yuv420p"
    stream.codec_context.time_base = Fraction(1, 1000)
    stream.options = {"preset": "veryfast", "crf": "24", "bf": "0", "g": "8"}
    vindex = []
    for seq, t0, t1 in frames:
        vf = av.VideoFrame.from_image(render(t0))
        vf.pts, vf.time_base = int(round((t0 + t1) / 2 * 1000)), Fraction(1, 1000)
        for p in stream.encode(vf):
            container.mux(p)
        vindex.append({"seq": seq, "pts_ms": vf.pts, "t_capture_start": t0, "t_capture_end": t1})
    for p in stream.encode():
        container.mux(p)
    container.close()
    (OUT / "video_frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in vindex), encoding="utf-8")

    has_audio = synthesize_audio(OUT / "audio.wav")
    (OUT / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (OUT / "transcript.json").write_text(json.dumps(
        [{"id": i, "t_start": a, "t_end": b, "text": x} for i, (a, b, x) in enumerate(TRANSCRIPT)],
        indent=2), encoding="utf-8")
    # a scripted transcript, not speech-to-text: never re-transcribe it (the audio is synthetic)
    (OUT / "transcript.meta.json").write_text(json.dumps({"source": "file", "file": "make_synthetic.py"}, indent=2),
                                              encoding="utf-8")
    samples = int((DURATION + 1) * 16000)
    meta = {
        "schema": "thinkaloud.session/0.2",
        "session_id": "synthetic-flight",
        "task": "Find and select the cheapest nonstop flight from Toronto (YYZ) to San Francisco (SFO) on Oct 17.",
        "success_criteria": "Checkout page shows the cheapest nonstop YYZ-SFO flight on Oct 17.",
        "started_at": "2026-09-26T10:00:00-04:00",
        "duration_s": DURATION,
        "clock": {"source": "synthetic", "unit": "s", "origin": "recording start (t0)"},
        "screen": {"w": W, "h": H, "left": 0, "top": 0},
        "coordinate_space": {"space": "synthetic frame pixels", "frame_origin": [0, 0], "frame_size": [W, H],
                             "monitor_index": 1, "monitor_dpi_scale": 1.0, "process_dpi_awareness": "per_monitor"},
        "capture": {"fps": FPS, "frames": len(frames), "grab_errors": 0, "settle_s": SETTLE,
                    "candidate_gap_s": CANDIDATE_GAP, "ring_size": 6, "double_click_time_s": 0.5},
        "stills": {"index": "frames/stills.jsonl", "saved": len(saved), "failed": 0, "dropped": 0},
        "video": {"file": "screen.mkv", "codec": "h264", "frames": len(frames), "dropped": 0,
                  "index": "video_frames.jsonl", "pts": "capture midpoint, ms on recording clock", "error": None},
        "audio": ({"file": "audio.wav", "sample_rate": 16000, "samples": samples, "offset_s": 0.0,
                   "offset_method": "synthetic (placed exactly)", "clock_drift_ppm": 0.0, "overflows": 0,
                   "error": None} if has_audio else
                  {"file": None, "sample_rate": 16000, "offset_s": None, "error": "no speech synthesizer"}),
        "uia": {"enabled": True, "available": True, "error": None},
        "input": {"events": len(events), "masked_keys": PASSWORD_LEN, "pauses": [],
                  "hotkeys": {"stop": "F9", "pause": "F8"}},
        "errors": [],
        "platform": "synthetic",
        "recorder_version": "synthetic-0.2",
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(events)} events, {len(saved)} stills, {len(frames)} video frames, "
          f"audio={'yes' if has_audio else 'no'})")


if __name__ == "__main__":
    main()
