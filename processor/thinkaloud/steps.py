"""Merge raw recorder events into human-level steps.

Boundaries are driven by events, not timers, except where a pause is the signal:

  * scroll: consecutive scroll events form ONE step until another kind of input
    (click, typing, a key), a change of the window under the pointer, a change of
    held modifiers, a pause longer than `scroll_pause_s` (default 5 s, configurable),
    a pause/stop marker, or the end of the recording. Every raw scroll event is
    kept (`action.events`) and consecutive same-direction events are summarised as
    `action.runs`, so down-then-up is two runs, never a misleading net amount.
  * type: printable keys (and backspace, which edits the text) typed with gaps of
    at most `type_gap_s` in the same window form one step. Keys the recorder
    masked (password fields) stay masked. A step with corrections keeps its raw
    `keystrokes` ("helo⌫lo"). A Backspace that would delete text that was in the
    field before this step (nothing typed left to delete) is a separate
    "key backspace" step, so deleting existing text is never lost or turned into typing.
  * key: Enter/Tab and keys with Ctrl/Alt/Win/Shift held (e.g. "ctrl+c") are their
    own steps. Other special keys repeat-merge within `repeat_gap_s` ("repeat": n).
  * click: one step per press. Presses of the same button within the system
    double-click time and double-click rectangle merge ("count": 2 or 3), never across
    a pause. A press followed by a release elsewhere (recorder "release" event) is a
    "drag" step with start (x, y) and end (x2, y2). A release is paired with its own
    press whatever came in between; if other input happened during the drag, or the
    press was the second click of a double-click, the step keeps its click type with
    `release` (end point) and `drag_problem` set, so exports can report it instead of
    silently presenting a click.

Coordinates in actions are frame pixels (screen minus the captured monitor's
origin); the original screen coordinates are kept as screen_x / screen_y.
Each step has a stable `uid` derived from the recorder sequence number of its
first event, so reprocessing the same recording yields the same uids.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SegmentConfig:
    type_gap_s: float = 2.0
    scroll_pause_s: float = 5.0
    repeat_gap_s: float = 1.0
    double_click_s: float = 0.5
    double_click_px: int = 2  # half the system double-click rectangle (SM_CXDOUBLECLK 4 px)


SUBMIT_KEYS = ("enter", "tab")


def _is_text_key(e: dict) -> bool:
    return e["type"] == "key" and not e.get("mods") and (len(e["key"]) == 1 or e["key"] == "backspace")


def _window_key(e: dict):
    w = e.get("window")
    return w.get("hwnd") if isinstance(w, dict) else None


def _context(e: dict) -> dict | None:
    w = e.get("window")
    if not isinstance(w, dict):
        return None
    return {"window_title": w.get("title"), "process": w.get("process"), "hwnd": w.get("hwnd")}


def _direction(dx: float, dy: float) -> str:
    if dy > 0:
        return "up"
    if dy < 0:
        return "down"
    if dx > 0:
        return "right"
    if dx < 0:
        return "left"
    return "none"


def scroll_runs(events: list[dict]) -> list[dict]:
    runs: list[dict] = []
    for e in events:
        d = _direction(e["dx"], e["dy"])
        amount = abs(e["dy"]) if d in ("up", "down") else abs(e["dx"])
        if runs and runs[-1]["direction"] == d:
            r = runs[-1]
            r["amount"] = round(r["amount"] + amount, 4)
            r["t_end"] = e["t"]
            r["n_events"] += 1
        else:
            runs.append({"direction": d, "amount": round(amount, 4), "t_start": e["t"], "t_end": e["t"],
                         "n_events": 1})
    return runs


def merge_events(events: list[dict], origin: tuple[int, int] = (0, 0),
                 config: SegmentConfig | None = None) -> list[dict]:
    """Return ordered steps. Events may be recorder v0.1 (no seq) or v0.2."""
    cfg = config or SegmentConfig()
    ox, oy = origin
    events = sorted(enumerate(events), key=lambda p: (p[1]["t"], p[1].get("seq", p[0])))
    steps: list[dict] = []
    cur: dict | None = None
    barrier = False  # a pause/stop happened since the last step: no double-click merging across it
    open_press: dict[str, dict] = {}  # button -> the step its latest press belongs to

    def flush():
        nonlocal cur
        if cur is not None:
            a = cur["action"]
            if a["type"] == "type" and a.get("backspaces"):
                a["keystrokes"] = a["_keys"]          # the whole sequence, including what came after the last Backspace
            if a["type"] == "scroll":
                a["runs"] = scroll_runs(a["events"])
                a["net_dx"] = round(sum(ev["dx"] for ev in a["events"]), 4)
                a["net_dy"] = round(sum(ev["dy"] for ev in a["events"]), 4)
            if a["type"] != "type" or a["text"] or a.get("keystrokes"):
                steps.append(cur)
            cur = None

    def new(e: dict, idx: int, action: dict) -> dict:
        return {"t_start": e["t"], "t_end": e["t"], "action": action, "first_seq": e.get("seq", idx),
                "last_seq": e.get("seq", idx), "n_events": 1, "context": _context(e),
                "wkey": _window_key(e)}

    def extend(e: dict, idx: int) -> None:
        cur["t_end"] = e["t"]
        cur["last_seq"] = e.get("seq", idx)
        cur["n_events"] += 1

    for idx, e in events:
        t, kind = e["t"], e["type"]
        if kind == "marker":
            if e.get("name") in ("end", "pause", "resume"):
                flush()
                barrier = True
            continue

        if kind == "release":
            press = open_press.pop(e.get("button", "left"), None)
            if press is None or press["action"]["type"] != "click":
                continue
            a = press["action"]
            nothing_between = cur is None and steps and steps[-1] is press
            if nothing_between and a.get("count", 1) == 1:
                a.update(type="drag", x2=e["x"] - ox, y2=e["y"] - oy, screen_x2=e["x"], screen_y2=e["y"])
                press["t_end"] = t
                press["last_seq"] = e.get("seq", idx)
                press["n_events"] += 1
            else:
                # keep the step where it happened (so steps stay in time order) but never lose the drag
                a["release"] = {"x": e["x"] - ox, "y": e["y"] - oy, "t": round(t, 4)}
                a["drag_problem"] = ("double-click drag" if a.get("count", 1) > 1
                                     else "other input happened during the drag")
            continue

        typing = cur is not None and cur["action"]["type"] == "type"
        leading_backspace = (e["type"] == "key" and e["key"] == "backspace" and not e.get("mods")
                             and not (typing and cur["action"]["text"]
                                      and t - cur["t_end"] <= cfg.type_gap_s
                                      and cur["wkey"] == _window_key(e)))
        if kind in ("scroll",) or (kind == "key" and _is_text_key(e)):
            barrier = False
        if _is_text_key(e) and not leading_backspace:
            if (cur and cur["action"]["type"] == "type" and t - cur["t_end"] <= cfg.type_gap_s
                    and cur["wkey"] == _window_key(e)):
                extend(e, idx)
            else:
                flush()
                cur = new(e, idx, {"type": "type", "text": ""})
            a = cur["action"]
            a["_keys"] = a.get("_keys", "") + ("⌫" if e["key"] == "backspace" else e["key"])
            if e["key"] == "backspace":
                a["text"] = a["text"][:-1]
                a["backspaces"] = a.get("backspaces", 0) + 1
            else:
                a["text"] += e["key"]
                if e.get("masked"):
                    a["masked_chars"] = a.get("masked_chars", 0) + 1
            continue

        if kind == "scroll":
            sev = {"t": t, "x": e["x"] - ox, "y": e["y"] - oy, "dx": float(e.get("dx", 0.0)),
                   "dy": float(e.get("dy", 0.0))}
            if "raw" in e:
                sev["raw"] = e["raw"]
            same = (cur and cur["action"]["type"] == "scroll"
                    and t - cur["t_end"] <= cfg.scroll_pause_s
                    and cur["wkey"] == _window_key(e)
                    and cur["action"].get("mods") == e.get("mods"))
            if same:
                extend(e, idx)
                cur["action"]["events"].append(sev)
            else:
                flush()
                unit = "notch" if "raw" in e else "notch (recorder 0.1: fractional wheel deltas lost)"
                action = {"type": "scroll", "x": sev["x"], "y": sev["y"], "screen_x": e["x"],
                          "screen_y": e["y"], "unit": unit, "events": [sev]}
                if e.get("mods"):
                    action["mods"] = e["mods"]
                cur = new(e, idx, action)
            continue

        if kind == "key":
            barrier = False
            name = "+".join(e.get("mods", []) + [e["key"]])
            if (cur and cur["action"]["type"] == "key" and cur["action"]["key"] == name
                    and t - cur["t_end"] <= cfg.repeat_gap_s and name not in SUBMIT_KEYS
                    and not e.get("mods")):
                extend(e, idx)
                cur["action"]["repeat"] = cur["action"].get("repeat", 1) + 1
                continue
            flush()
            cur = new(e, idx, {"type": "key", "key": name})
            if name in SUBMIT_KEYS or e.get("mods"):
                flush()  # never merge submits or shortcuts
            continue

        if kind == "click":
            flush()
            prev = steps[-1] if steps and not barrier else None
            barrier = False
            x, y = e["x"] - ox, e["y"] - oy
            button = e.get("button", "left")
            if (prev and prev["action"]["type"] == "click" and prev["action"]["button"] == button
                    and t - prev["t_end"] <= cfg.double_click_s and prev["action"].get("count", 1) < 3
                    and abs(prev["action"]["x"] - x) <= cfg.double_click_px
                    and abs(prev["action"]["y"] - y) <= cfg.double_click_px):
                prev["t_end"] = t
                prev["last_seq"] = e.get("seq", idx)
                prev["n_events"] += 1
                prev["action"]["count"] = prev["action"].get("count", 1) + 1
                open_press[button] = prev
                continue
            action = {"type": "click", "x": x, "y": y, "button": button, "screen_x": e["x"],
                      "screen_y": e["y"]}
            if e.get("mods"):
                action["mods"] = e["mods"]
            step = new(e, idx, action)
            if "target" in e:
                step["target"] = e["target"]
            steps.append(step)
            open_press[button] = step
            continue

    flush()

    for st in steps:
        st["action"].pop("_keys", None)
    out = []
    for i, s in enumerate(steps):
        rec = {"id": i, "uid": f"s{s['first_seq']:06d}", "t_start": round(s["t_start"], 4),
               "t_end": round(s["t_end"], 4), "action": s["action"],
               "event_range": [s["first_seq"], s["last_seq"]], "n_events": s["n_events"],
               "context": s["context"]}
        if "target" in s:
            rec["target"] = s["target"]
        out.append(rec)
    return out
