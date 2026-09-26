"""Generate a synthetic session so the processor and viewer work without recording.

Scenario: find and select the cheapest *nonstop* YYZ -> SFO flight on a fake
travel site. It deliberately contains things QC should catch: a typed email,
a typed password, a click with no narration, and a long silent pause.

Writes samples/synthetic-flight/{meta.json, events.jsonl, transcript.json, frames/*.png}
Usage: python scripts/make_synthetic.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
OUT = Path(__file__).resolve().parent.parent / "samples" / "synthetic-flight"


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


def page(url: str, title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 86], fill=(232, 233, 236))
    for i, c in enumerate([(236, 95, 88), (245, 190, 80), (98, 197, 84)]):
        d.ellipse([18 + i * 24, 16, 32 + i * 24, 30], fill=c)
    d.rounded_rectangle([110, 44, W - 110, 76], 8, fill="white")
    d.text((128, 49), url, font=F["url"], fill=MUTED)
    d.text((80, 120), "farefinder", font=F["l"], fill=ACCENT)
    d.text((80, 175), title, font=F["b"], fill=INK)
    return img, d


def field(d, x, y, label, value="", w=420, focus=False, secret=False):
    d.text((x, y), label, font=F["s"], fill=MUTED)
    d.rounded_rectangle([x, y + 28, x + w, y + 78], 6, fill="white",
                        outline=ACCENT if focus else LINE, width=2 if focus else 1)
    d.text((x + 14, y + 40), "•" * len(value) if secret else value, font=F["m"], fill=INK)


def button(d, x, y, text, w=200, primary=True):
    d.rounded_rectangle([x, y, x + w, y + 50], 6, fill=ACCENT if primary else "white",
                        outline=ACCENT)
    d.text((x + 20, y + 12), text, font=F["b"], fill="white" if primary else ACCENT)


FLIGHTS = [
    ("United", "6:05 → 11:40", "1 stop · ORD", "$268"),
    ("Air Canada", "8:30 → 11:02", "Nonstop", "$312"),
    ("WestJet", "10:15 → 12:50", "Nonstop", "$329"),
    ("Delta", "7:00 → 13:25", "1 stop · MSP", "$274"),
    ("Alaska", "12:40 → 15:10", "Nonstop", "$341"),
]


def results(title, flights, sort="Recommended", nonstop=False, selected=None, scrolled=False):
    img, d = page("https://farefinder.example/flights?from=YYZ&to=SFO&date=2026-10-17", title)
    d.text((80, 215), "YYZ → SFO · Fri Oct 17 · 1 adult", font=F["s"], fill=MUTED)
    d.rounded_rectangle([W - 420, 170, W - 80, 214], 6, fill="white", outline=LINE)
    d.text((W - 404, 180), f"Sort: {sort}", font=F["s"], fill=INK)
    d.rounded_rectangle([80, 250, 330, 290], 20, fill=ACCENT if nonstop else "white", outline=ACCENT)
    d.text((104, 258), ("On: " if nonstop else "") + "Nonstop only", font=F["s"],
           fill="white" if nonstop else ACCENT)
    y = 320 - (60 if scrolled else 0)
    for i, (air, times, stops, price) in enumerate(flights):
        sel = selected == i
        d.rounded_rectangle([80, y, W - 80, y + 96], 10, fill=(236, 243, 255) if sel else "white",
                            outline=ACCENT if sel else LINE, width=2 if sel else 1)
        d.text((110, y + 18), air, font=F["b"], fill=INK)
        d.text((110, y + 54), times, font=F["s"], fill=MUTED)
        d.text((560, y + 34), stops, font=F["m"], fill=(40, 130, 70) if stops == "Nonstop" else MUTED)
        d.text((W - 240, y + 30), price, font=F["l"], fill=INK)
        y += 112
    return img


def screens() -> dict[str, Image.Image]:
    s = {}
    img, d = page("https://farefinder.example/signin", "Sign in to see member fares")
    field(d, 80, 240, "Email"); field(d, 80, 340, "Password"); button(d, 80, 450, "Sign in")
    s["signin"] = img
    img, d = page("https://farefinder.example/signin", "Sign in to see member fares")
    field(d, 80, 240, "Email", "zain.demo@example.com")
    field(d, 80, 340, "Password", "", focus=True); button(d, 80, 450, "Sign in")
    s["signin_pw"] = img
    img, d = page("https://farefinder.example/", "Where to?")
    field(d, 80, 240, "From", w=300); field(d, 400, 240, "To", w=300)
    field(d, 720, 240, "Depart", w=260); button(d, 1000, 268, "Search", w=160)
    s["search"] = img
    img, d = page("https://farefinder.example/", "Where to?")
    field(d, 80, 240, "From", "YYZ Toronto Pearson", w=300); field(d, 400, 240, "To", "", w=300, focus=True)
    field(d, 720, 240, "Depart", w=260); button(d, 1000, 268, "Search", w=160)
    s["search_from"] = img
    img, d = page("https://farefinder.example/", "Where to?")
    field(d, 80, 240, "From", "YYZ Toronto Pearson", w=300); field(d, 400, 240, "To", "SFO San Francisco", w=300)
    field(d, 720, 240, "Depart", "Fri Oct 17", w=260, focus=True); button(d, 1000, 268, "Search", w=160)
    s["search_full"] = img
    rec = [FLIGHTS[3], FLIGHTS[4], FLIGHTS[0], FLIGHTS[2], FLIGHTS[1]]
    s["results"] = results("48 flights", rec)
    img = results("48 flights", rec)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W - 420, 216, W - 80, 346], 6, fill="white", outline=LINE)
    for i, o in enumerate(["Recommended", "Price: low to high", "Duration"]):
        d.text((W - 404, 226 + i * 38), o, font=F["s"], fill=INK)
    s["sort_open"] = img
    by_price = sorted(FLIGHTS, key=lambda f: f[3])
    s["sorted"] = results("48 flights", by_price, sort="Price: low to high")
    s["sorted_scrolled"] = results("48 flights", by_price, sort="Price: low to high", scrolled=True)
    nonstop = [f for f in by_price if f[2] == "Nonstop"]
    s["nonstop"] = results("19 nonstop flights", nonstop, sort="Price: low to high", nonstop=True)
    s["picked"] = results("19 nonstop flights", nonstop, sort="Price: low to high", nonstop=True, selected=0)
    img, d = page("https://farefinder.example/checkout", "Review your trip")
    d.rounded_rectangle([80, 230, 900, 420], 10, fill="white", outline=LINE)
    d.text((110, 255), "Air Canada · AC 759 · Nonstop", font=F["b"], fill=INK)
    d.text((110, 300), "Fri Oct 17 · 8:30 YYZ → 11:02 SFO · 5h 32m", font=F["m"], fill=MUTED)
    d.text((110, 355), "Total  $312.00 CAD", font=F["l"], fill=INK)
    button(d, 80, 450, "Continue to payment", w=300)
    s["checkout"] = img
    return s


# (time, event, screen shown at that moment if the event captures a frame)
EVENTS = [
    (0.0, {"type": "marker", "name": "start"}, "signin"),
    (4.1, {"type": "click", "x": 290, "y": 293}, "signin"),
    ("type", 4.6, "zain.demo@example.com"),
    (7.4, {"type": "click", "x": 290, "y": 393}, "signin_pw"),
    ("type", 7.8, "Tr4vel!2026x"),
    (9.6, {"type": "key", "key": "enter"}, "search"),
    (13.6, {"type": "click", "x": 230, "y": 293}, "search"),
    ("type", 14.0, "YYZ"),
    (15.4, {"type": "click", "x": 550, "y": 293}, "search_from"),
    ("type", 15.8, "SFO"),
    (17.3, {"type": "click", "x": 850, "y": 293}, "search_full"),
    (19.2, {"type": "click", "x": 1080, "y": 293}, "search_full"),
    (24.9, {"type": "click", "x": 1350, "y": 192}, "results"),
    (26.1, {"type": "click", "x": 1300, "y": 283}, "sort_open"),
    (27.4, {"type": "scroll", "x": 800, "y": 600, "dx": 0, "dy": -2}, None),
    (27.9, {"type": "scroll", "x": 800, "y": 600, "dx": 0, "dy": -3}, None),
    (28.5, {"type": "scroll", "x": 800, "y": 600, "dx": 0, "dy": 2}, None),
    (35.2, {"type": "click", "x": 200, "y": 270}, "sorted_scrolled"),
    (38.4, {"type": "click", "x": 700, "y": 368}, "nonstop"),
    (62.0, {"type": "click", "x": 1320, "y": 368}, "picked"),  # silent detour: no narration
    (65.6, {"type": "click", "x": 1100, "y": 368}, "picked"),
    (71.5, {"type": "marker", "name": "end"}, "checkout"),
]

TRANSCRIPT = [
    (0.6, 3.6, "First I'm signing in, because this site only shows member fares when you're logged in."),
    (10.1, 13.2, "Now the search. I use airport codes, not city names, since Toronto has two airports and I want Pearson."),
    (16.4, 17.1, "Then the date, the seventeenth."),
    (19.6, 23.9, "Results default to recommended, which is mostly sponsored placements. So I sort by price."),
    (29.6, 34.8, "The cheapest one has a stop in Chicago. The task says direct, so I filter to nonstop instead of just grabbing the top result."),
    (35.9, 38.0, "Air Canada, three twelve, nonstop. That's the one."),
    (66.4, 70.2, "Okay, it's in checkout at three hundred twelve, nonstop, on the seventeenth. That matches what I was asked for."),
]


def main() -> None:
    # Clear contents rather than the folder itself (Windows may hold a handle on it).
    if OUT.exists():
        for p in OUT.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    imgs = screens()
    events = []
    for item in EVENTS:
        if item[0] == "type":
            _, t0, text = item
            for i, ch in enumerate(text):
                events.append({"t": round(t0 + i * 0.09, 3), "type": "key", "key": ch})
            continue
        t, ev, screen = item
        ev = {"t": t, **ev}
        if ev["type"] == "click":
            ev["button"] = "left"
        if screen:
            ev["frame"] = f"frames/{int(t * 1000):09d}.png"
            imgs[screen].save(OUT / ev["frame"], optimize=True)
        events.append(ev)
    events.sort(key=lambda e: e["t"])
    (OUT / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (OUT / "transcript.json").write_text(json.dumps(
        [{"id": i, "t_start": a, "t_end": b, "text": x} for i, (a, b, x) in enumerate(TRANSCRIPT)],
        indent=2), encoding="utf-8")
    meta = {
        "session_id": "synthetic-flight",
        "task": "Find and select the cheapest nonstop flight from Toronto (YYZ) to San Francisco (SFO) on Oct 17.",
        "success_criteria": "Checkout page shows the cheapest nonstop YYZ-SFO flight on Oct 17.",
        "started_at": "2026-09-26T10:00:00-04:00",
        "duration_s": 71.5,
        "screen": {"w": W, "h": H, "left": 0, "top": 0},
        "audio": {"file": None, "sample_rate": 16000, "offset_s": 0.0, "error": None},
        "platform": "synthetic",
        "recorder_version": "synthetic",
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(events)} events, {len(list((OUT / 'frames').glob('*.png')))} frames)")


if __name__ == "__main__":
    main()
