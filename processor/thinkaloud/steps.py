"""Merge raw recorder events into human-level steps.

Rules (all times in seconds since recording start):
  * Printable keys (and backspace) typed with gaps <= TYPE_GAP form one "type" step.
    Backspace edits the buffer, so the step holds the text as it ended up.
  * Enter / Tab end the current typing burst and become their own "key" step.
  * Keys pressed with Ctrl/Alt/Win held become "key" steps like "ctrl+c".
  * Other special keys (arrows, esc, ...) become "key" steps; repeats of the
    same key within REPEAT_GAP merge with a "repeat" count.
  * Consecutive scrolls with gaps <= SCROLL_GAP merge into one "scroll" step
    with summed dx/dy.
  * A click is one "click" step. Two clicks at the same spot within
    DOUBLE_CLICK_GAP become one click with "count": 2.

Each step's screenshot is the most recent frame captured at or before the
step started, i.e. what the expert was looking at when they acted.
"""
from __future__ import annotations

from bisect import bisect_right

TYPE_GAP = 2.0
SCROLL_GAP = 1.5
REPEAT_GAP = 1.0
DOUBLE_CLICK_GAP = 0.4
DOUBLE_CLICK_DIST = 6


def _is_text_key(e: dict) -> bool:
    return e["type"] == "key" and not e.get("mods") and (
        len(e["key"]) == 1 or e["key"] == "backspace")


def merge_events(events: list[dict], origin: tuple[int, int] = (0, 0)) -> list[dict]:
    """Return steps: {id, t_start, t_end, action, screenshot}."""
    events = sorted(events, key=lambda e: e["t"])
    frames = sorted((e["t"], e["frame"]) for e in events if e.get("frame"))
    frame_times = [t for t, _ in frames]
    ox, oy = origin

    steps: list[dict] = []
    cur: dict | None = None  # step being built (type / scroll / repeated key)

    def flush():
        nonlocal cur
        if cur is not None:
            if cur["action"]["type"] != "type" or cur["action"]["text"]:
                steps.append(cur)
            cur = None

    def new(t: float, action: dict) -> dict:
        return {"t_start": t, "t_end": t, "action": action}

    for e in events:
        t, kind = e["t"], e["type"]
        if kind == "marker":
            continue

        if _is_text_key(e):
            if cur and cur["action"]["type"] == "type" and t - cur["t_end"] <= TYPE_GAP:
                cur["t_end"] = t
            else:
                flush()
                cur = new(t, {"type": "type", "text": ""})
            text = cur["action"]["text"]
            cur["action"]["text"] = text[:-1] if e["key"] == "backspace" else text + e["key"]
            continue

        if kind == "scroll":
            if cur and cur["action"]["type"] == "scroll" and t - cur["t_end"] <= SCROLL_GAP:
                cur["t_end"] = t
                cur["action"]["dx"] += e["dx"]
                cur["action"]["dy"] += e["dy"]
            else:
                flush()
                cur = new(t, {"type": "scroll", "x": e["x"] - ox, "y": e["y"] - oy,
                              "dx": e["dx"], "dy": e["dy"]})
            continue

        if kind == "key":
            name = "+".join(e.get("mods", []) + [e["key"]])
            if (cur and cur["action"]["type"] == "key" and cur["action"]["key"] == name
                    and t - cur["t_end"] <= REPEAT_GAP and name not in ("enter", "tab")):
                cur["t_end"] = t
                cur["action"]["repeat"] = cur["action"].get("repeat", 1) + 1
                continue
            flush()
            cur = new(t, {"type": "key", "key": name})
            if name in ("enter", "tab"):
                flush()  # never merge submits
            continue

        if kind == "click":
            flush()
            prev = steps[-1] if steps else None
            x, y = e["x"] - ox, e["y"] - oy
            if (prev and prev["action"]["type"] == "click"
                    and t - prev["t_end"] <= DOUBLE_CLICK_GAP
                    and abs(prev["action"]["x"] - x) <= DOUBLE_CLICK_DIST
                    and abs(prev["action"]["y"] - y) <= DOUBLE_CLICK_DIST):
                prev["t_end"] = t
                prev["action"]["count"] = prev["action"].get("count", 1) + 1
                continue
            steps.append(new(t, {"type": "click", "x": x, "y": y,
                                 "button": e.get("button", "left")}))
            continue

    flush()

    for i, s in enumerate(steps):
        s["id"] = i
        s["t_start"], s["t_end"] = round(s["t_start"], 3), round(s["t_end"], 3)
        k = bisect_right(frame_times, s["t_start"] + 1e-6) - 1
        s["screenshot"] = frames[k][1] if k >= 0 else None
    return [{"id": s["id"], "t_start": s["t_start"], "t_end": s["t_end"],
             "action": s["action"], "screenshot": s["screenshot"]} for s in steps]
