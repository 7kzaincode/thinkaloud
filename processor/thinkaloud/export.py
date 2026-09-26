"""Export reviewed recordings as a self-contained, validated bundle.

bundle/
  manifest.json                   format, versions, recordings, every asset with sha256
  README.md                       what is in the bundle and how to load it
  thinkaloud_dataset.py           standalone loader/validator (stdlib only)
  recordings/<id>/trajectory.json thinkaloud.dataset/1.0 (vendor-neutral)
  recordings/<id>/assets/*.png    before/after/final screens (full resolution, lossless)
  recordings/<id>/assets/playback.mp4   (optional) replay video + narration audio
  claude/<id>.json                thinkaloud.claude_computer_use/1.0 (optional)
  claude/assets/<id>/*.png        screenshots scaled for the computer-use tool

Two formats, kept apart on purpose:
  * dataset: everything we know, provider-neutral. Human narration (speech-to-text)
    is kept verbatim with its timing relative to the action; reviewer edits and AI
    suggestions are separate fields with provenance; missing data stays missing.
  * claude: the recording expressed as the Anthropic computer-use tool conversation
    (tool type computer_toolset_20260801, checked against the official docs on
    2026-09-26: member tools are tool_use names with toolset_name "computer";
    coordinates are in the pixel space of the screenshots returned). Only the
    actions and screens go in `messages`; narration/review/QC metadata live in
    `annotations`, never in Claude's content. Narration is NOT turned into model
    reasoning. Anything that can't be expressed faithfully is an error or warning.

Images in claude/<id>.json reference bundle files with a thinkaloud-specific
source ({"type": "thinkaloud_asset", "path": ...}); thinkaloud_dataset.py's
materialize_claude_messages() turns them into API-ready base64 image blocks.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import dataset as ds

EXPORT_VERSION = "1.0"
GENERATOR = {"name": "thinkaloud", "version": "0.2.0"}
CLAUDE_TOOL = {"type": "computer_toolset_20260801"}
CLAUDE_DOCS = {
    "url": "https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool",
    "checked": "2026-09-26",
    "summary": "member tool names as tool_use.name, toolset_name 'computer' on every tool_use and tool_result, "
               "coordinates in screenshot pixel space; screenshots must fit the model's image limits "
               "(long edge <= 2576 px, <= 4784 visual tokens); 1080p recommended",
}
SCREENSHOT_LONG_EDGE = 1920
XDOTOOL = {
    "enter": "Return", "tab": "Tab", "esc": "Escape", "backspace": "BackSpace", "delete": "Delete",
    "insert": "Insert", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home",
    "end": "End", "page_up": "Page_Up", "page_down": "Page_Down", "space": "space", " ": "space",
    "caps_lock": "Caps_Lock", "print_screen": "Print", "menu": "Menu", "num_lock": "Num_Lock",
    "scroll_lock": "Scroll_Lock", "pause": "Pause",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}
MOD = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "cmd": "super"}
PUNCT = {"/": "slash", "\\": "backslash", ".": "period", ",": "comma", ";": "semicolon", "'": "apostrophe",
         "[": "bracketleft", "]": "bracketright", "-": "minus", "=": "equal", "`": "grave", "+": "plus"}
FRAME_RE = re.compile(r"frames/[\w.-]+\.png")
REVIEW_STEP_FIELDS = ("reasoning", "reasoning_source", "carried_from", "reasoning_original", "flags",
                      "dismissed_flags", "edited")
SEVERITIES = {"high", "warn", "info"}


def trajectory_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _step_key(s: dict) -> str:
    return s.get("uid") or f"t{s.get('t_start')}|{(s.get('action') or {}).get('type')}"


def _clean_flag(f) -> dict | None:
    if not isinstance(f, dict) or not isinstance(f.get("code"), str):
        return None
    out = {"code": f["code"][:64], "severity": f.get("severity") if f.get("severity") in SEVERITIES else "warn",
           "detail": str(f.get("detail", ""))[:1000], "source": "reviewer" if f.get("source") == "reviewer" else "qc"}
    if isinstance(f.get("subject"), str):
        out["subject"] = f["subject"][:32]
    prov = f.get("provenance")
    if isinstance(prov, dict):
        out["provenance"] = {"ai_run_id": str(prov.get("ai_run_id", ""))[:80], "suggestion": str(prov.get("suggestion", ""))[:120]}
    return out


def overlay_review(orig: dict, rev: dict) -> dict:
    """The processor's trajectory with ONLY review-owned fields taken from the reviewer's file.
    The reviewer's file is written by the browser, so nothing else in it (actions, image paths,
    ids) is trusted."""
    t = json.loads(json.dumps(orig))
    by_key = {_step_key(s): s for s in rev.get("steps", []) if isinstance(s, dict)}
    for s in t["steps"]:
        r = by_key.get(_step_key(s))
        if not r:
            continue
        if isinstance(r.get("reasoning"), str):
            s["reasoning"] = r["reasoning"][:5000]
        if r.get("reasoning_source") in ("narrated", "carried", "reviewer", None):
            s["reasoning_source"] = r.get("reasoning_source")
        cf = r.get("carried_from")
        s["carried_from"] = cf if isinstance(cf, int) and 0 <= cf < len(t["steps"]) else None
        ro = r.get("reasoning_original")
        if isinstance(ro, dict):
            s["reasoning_original"] = {"text": str(ro.get("text", ""))[:5000], "source": ro.get("source"),
                                       "carried_from": ro.get("carried_from")}
        # QC flags always come from the processor; the reviewer's file only says which were dismissed
        # (a file that simply omits a flag does not close it) and adds reviewer flags
        dismissed = [f for f in map(_clean_flag, r.get("dismissed_flags") or []) if f]
        qc_dismissed = [d for d in dismissed if d["source"] != "reviewer"]
        flags, closed = [], []
        for f in s.get("flags", []):
            (closed if any(dismissal_applies(d, f) for d in qc_dismissed) else flags).append(f)
        if s.get("reasoning_source") == "reviewer" and (s.get("reasoning") or "").strip():
            closed += [f for f in flags if f["code"] == "missing_reasoning"]
            flags = [f for f in flags if f["code"] != "missing_reasoning"]
        flags += [f for f in map(_clean_flag, r.get("flags") or []) if f and f["source"] == "reviewer"]
        closed += [d for d in dismissed if d["source"] == "reviewer"]
        s["flags"], s["dismissed_flags"] = flags, closed
        s["edited"] = bool(r.get("edited"))
    t["review"] = rev.get("review") if isinstance(rev.get("review"), dict) else t.get("review", {})
    return t


def dismissal_applies(dismissed: dict, fresh: dict) -> bool:
    """A reviewer's dismissal closes a processor flag with the same code; for privacy (high)
    flags only when it is about the same content (same subject hash), so dismissing one typed
    password never silently closes a different one after reprocessing. Same rule as the
    viewer's rebaseReview."""
    if dismissed.get("code") != fresh.get("code"):
        return False
    if fresh.get("severity") != "high":
        return True
    return dismissed.get("subject") == fresh.get("subject")


def split_combo(combo: str) -> tuple[list[str], str]:
    """"ctrl+shift+t" -> (["ctrl", "shift"], "t"); "ctrl++" -> (["ctrl"], "+")."""
    if combo.endswith("+") and (combo == "+" or combo.endswith("++")):
        head = combo[:-1].rstrip("+") if combo != "+" else ""
        return (head.split("+") if head else []), "+"
    parts = combo.split("+")
    return parts[:-1], parts[-1]


def normalize(t: dict) -> dict:
    """Schema 0.1 trajectories have no observations: derive the same honest statuses the viewer shows."""
    for s in t.get("steps", []):
        if not s.get("observations"):
            shot = s.get("screenshot")
            s["observations"] = {
                "before": ({"status": "at_action", "file": shot, "source": "still",
                            "reason": "schema 0.1: captured when the action happened"} if shot else
                           {"status": "missing", "file": None, "reason": "schema 0.1: no screenshot"}),
                "after": {"status": "missing", "file": None, "reason": "schema 0.1: after-states were not captured"}}
    if not t.get("final_observation"):
        f = t.get("final_screenshot")
        t["final_observation"] = {"status": "ok" if f else "missing", "file": f, "source": "still"}
    return t


def effective_trajectory(session: Path) -> tuple[dict, list[str]]:
    """The processor's trajectory with the human review overlaid if the review matches it.
    A review made against an older processor output is an error: its edits would be lost."""
    orig = session / "trajectory.json"
    rev = session / "trajectory.reviewed.json"
    if not orig.exists():
        raise ds.ExportError(f"{session.name}: not processed (no trajectory.json)")
    t = normalize(json.loads(orig.read_text(encoding="utf-8")))
    notes: list[str] = []
    if rev.exists():
        r = json.loads(rev.read_text(encoding="utf-8"))
        if (r.get("review") or {}).get("base_hash") == trajectory_hash(orig):
            return overlay_review(t, r), notes
        raise ds.ExportError(f"{session.name}: the review was made against an older processing run; "
                             "open it in the viewer once so the edits are carried over, then export again")
    notes.append(f"{session.name}: not reviewed; exported without human decisions")
    return t, notes


def open_privacy_flags(t: dict) -> list[str]:
    out = [f"{f['code']}" for f in t.get("session_flags", []) if f.get("severity") == "high"]
    for s in t["steps"]:
        out += [f"step {s.get('uid', s['id'])}: {f['code']}" for f in s.get("flags", []) if f.get("severity") == "high"]
    return out


# ------------------------------------------------------------------ dataset record
def _action(a: dict) -> dict:
    t = a["type"]
    if t == "click":
        out = {"type": "click", "button": a.get("button", "left"), "count": a.get("count", 1),
               "x": a["x"], "y": a["y"], "modifiers": a.get("mods", [])}
        if a.get("drag_problem"):  # a drag that couldn't become a clean drag step: keep its end point
            out["release"] = a.get("release")
            out["drag_problem"] = a["drag_problem"]
        return out
    if t == "drag":
        return {"type": "drag", "button": a.get("button", "left"), "x": a["x"], "y": a["y"],
                "x2": a["x2"], "y2": a["y2"], "modifiers": a.get("mods", [])}
    if t == "type":
        out = {"type": "type", "text": a["text"], "redacted": bool(a.get("redacted")),
               "masked_chars": a.get("masked_chars", 0), "backspaces": a.get("backspaces", 0)}
        if a.get("keystrokes") and not (a.get("redacted") or a.get("masked_chars")):
            out["keystrokes"] = a["keystrokes"]
        return out
    if t == "key":
        mods, key = split_combo(a["key"])
        return {"type": "key", "key": key, "modifiers": mods, "repeat": a.get("repeat", 1)}
    if t == "scroll":
        runs = a.get("runs")
        if runs is None:  # schema 0.1: only a net amount survived
            dy, dx = a.get("dy", 0), a.get("dx", 0)
            d = "down" if dy < 0 else "up" if dy > 0 else "left" if dx < 0 else "right" if dx > 0 else "none"
            runs = [{"direction": d, "amount": abs(dy or dx), "t_start": None, "t_end": None, "n_events": None}]
        return {"type": "scroll", "x": a["x"], "y": a["y"], "unit": a.get("unit", "notch"),
                "modifiers": a.get("mods", []), "runs": runs, "events": a.get("events")}
    raise ds.ExportError(f"unknown action type {t!r}")


def _obs(o: dict | None, assets: dict[str, str]) -> dict:
    o = o or {"status": "missing", "file": None}
    return {"status": o.get("status"), "image": assets.get(o.get("file")) if o.get("file") else None,
            "t_capture_start": o.get("t_capture_start"), "t_capture_end": o.get("t_capture_end"),
            "source": o.get("source"), "reason": o.get("reason")}


def dataset_record(t: dict, assets: dict[str, str], media_asset: str | None) -> dict:
    segs = {g["id"]: g for g in t.get("transcript", [])}
    by_id = {s["id"]: s for s in t["steps"]}
    review = t.get("review") or {}
    ai = review.get("ai") or {}
    assess = {a["uid"]: a for a in ai.get("step_assessments", [])}
    steps = []
    for s in t["steps"]:
        uid = s.get("uid") or f"t{s['t_start']}|{s['action']['type']}"
        timing = {n["segment_id"]: n.get("timing") for n in s.get("narration", [])}
        narration = [{"segment_id": i, "text": segs[i]["text"], "t_start": segs[i]["t_start"],
                      "t_end": segs[i]["t_end"], "timing": timing.get(i),
                      "source": "human narration (speech-to-text)"} for i in s.get("transcript_ids", []) if i in segs]
        orig = s.get("reasoning_original")
        cf = s.get("carried_from")
        tgt = s.get("target")
        a = assess.get(uid)
        steps.append({
            "index": s["id"], "uid": uid, "t_start": s["t_start"], "t_end": s["t_end"],
            "action": _action(s["action"]),
            "description": s.get("description"),
            "context": {"window_title": (s.get("context") or {}).get("window_title"),
                        "process": (s.get("context") or {}).get("process")} if s.get("context") else None,
            "target": None if tgt is None else {k: tgt.get(k) for k in (
                "status", "reliable", "role", "name", "automation_id", "class_name", "framework",
                "frame_rect", "url", "url_status", "latency_ms", "is_password")},
            "observations": {"before": _obs((s.get("observations") or {}).get("before"), assets),
                             "after": _obs((s.get("observations") or {}).get("after"), assets)},
            "narration": narration,
            "reasoning": {
                "text": s.get("reasoning") or "",
                "source": s.get("reasoning_source"),
                "carried_from_uid": (by_id[cf].get("uid") if cf is not None and cf in by_id else None),
                "edited_by_reviewer": s.get("reasoning_source") == "reviewer",
                "original": None if not orig else {"text": orig.get("text"), "source": orig.get("source")},
            },
            "flags": s.get("flags", []),
            "dismissed_flags": s.get("dismissed_flags", []),
            "ai_assessment": None if not a else {**{k: a.get(k) for k in ("verdict", "explanation", "run_id", "input_hash")},
                                                "human_decision": a.get("decision"), "suggestion_only": True},
        })
    cs = t.get("coordinate_space") or {}
    size = cs.get("frame_size") or [t["screen"]["w"], t["screen"]["h"]]
    return {
        "format": f"thinkaloud.dataset/{EXPORT_VERSION}",
        "recording": {"id": t["session_id"], "task": t.get("task", ""), "success_criteria": t.get("success_criteria", ""),
                      "recorded_at": t.get("recorded_at"), "duration_s": t.get("duration_s"),
                      "trajectory_schema": t.get("schema_version"),
                      "session_schema": (t.get("source") or {}).get("session_schema"),
                      "recorder_version": (t.get("source") or {}).get("recorder_version"),
                      "legacy": bool((t.get("source") or {}).get("legacy", t.get("schema_version") == "0.1"))},
        "coordinate_space": {"frame_width": size[0], "frame_height": size[1],
                             "frame_origin_on_screen": cs.get("frame_origin_on_screen", [0, 0]),
                             "monitor_dpi_scale": cs.get("monitor_dpi_scale"),
                             "units": "physical pixels; origin top-left of the captured frame"},
        "timeline": {"unit": "s", "origin": "recording start", "clock": "recorder monotonic clock",
                     "audio_offset_s": (t.get("timeline") or {}).get("audio_offset_s"),
                     "pauses": (t.get("timeline") or {}).get("pauses", []),
                     "media": media_asset},
        "steps": steps,
        "final_observation": _obs(t.get("final_observation") or
                                  {"status": "ok" if t.get("final_screenshot") else "missing",
                                   "file": t.get("final_screenshot")}, assets),
        "transcript": [{"id": g["id"], "text": g["text"], "t_start": g["t_start"], "t_end": g["t_end"],
                        "step_uid": (by_id[g["step_id"]].get("uid") if g.get("step_id") in by_id else None),
                        "timing": g.get("timing")} for g in t.get("transcript", [])],
        "review": {
            "outcome": review.get("outcome"), "outcome_source": "human" if review.get("outcome") else None,
            "notes": review.get("notes", ""), "reviewed_at": review.get("reviewed_at"),
            "edited": bool(review.get("edited")),
            "checklist": [{"id": c["id"], "text": c["text"], "origin": c.get("origin"),
                           "human_verdict": c.get("human_verdict"),
                           "human_verdict_source": c.get("human_verdict_source"),
                           # which AI check the human adopted (if any); the latest check may differ
                           "accepted_from": c.get("accepted_from"),
                           # the wording the verdict was given for; outdated = the item was reworded since
                           "verdict_given_for": c.get("verdict_text"),
                           "verdict_outdated": bool(c.get("human_verdict") and c.get("verdict_text") is not None
                                                    and c.get("verdict_text") != c["text"]),
                           "ai_check": c.get("ai_check")} for c in review.get("checklist", [])],
            "ai_runs": ai.get("runs", []),
            "ai_checklist_drafts": ai.get("checklist_drafts", []),
            # reprocessing history: which human edits could not be carried to the new steps
            "rebase_history": review.get("rebase_history", []),
        },
        "qc": {"summary": t.get("qc", {}).get("summary"), "counts": t.get("qc", {}).get("counts", {})},
        "session_flags": t.get("session_flags", []),
    }


# ------------------------------------------------------------------ claude computer use
def screenshot_scale(w: int, h: int, long_edge: int = SCREENSHOT_LONG_EDGE) -> float:
    return min(1.0, long_edge / max(w, h))


LONE_MODIFIER = {"cmd": "super", "alt": "alt", "ctrl": "ctrl", "shift": "shift"}  # pressed on its own


def _key_text(key: str, mods: list[str], problems: list) -> str | None:
    k = XDOTOOL.get(key.lower()) or (LONE_MODIFIER.get(key.lower()) if not mods else None)
    if k is None and len(key) == 1:
        k = key if key.isalnum() else PUNCT.get(key)
    if k is None:
        problems.append(("error", f"key {key!r} has no computer-use key name"))
        return None
    bad = [m for m in mods if m not in MOD]
    if bad:
        problems.append(("error", f"modifier(s) {bad} not supported"))
        return None
    text = "+".join([MOD[m] for m in mods] + [k])
    if not ds.KEY_RE.match(text):  # same rule the validator applies
        problems.append(("error", f"key combination {text!r} is not a valid key name"))
        return None
    return text


def claude_calls(step: dict, scale: float, frame: tuple[int, int]) -> tuple[list[tuple[str, dict]], list]:
    """Map one dataset step to computer-toolset member calls. Returns (calls, problems)."""
    a = step["action"]
    problems: list = []
    sw, sh = round(frame[0] * scale), round(frame[1] * scale)

    def coord(x, y):
        # the pixel's centre, scaled; always inside the screenshot for a pixel inside the frame
        c = [min(math.floor((x + 0.5) * scale), sw - 1), min(math.floor((y + 0.5) * scale), sh - 1)]
        if not (0 <= x < frame[0] and 0 <= y < frame[1]):
            c = [math.floor((x + 0.5) * scale), math.floor((y + 0.5) * scale)]
            problems.append(("error", f"coordinate {c} outside the {sw}x{sh} screenshot "
                                      "(action on another monitor or outside the captured frame)"))
        return c

    t = a["type"]
    if t == "click" and a.get("drag_problem"):
        problems.append(("error", f"a drag that can't be represented ({a['drag_problem']}); "
                                  "exporting it as a click would change its meaning, so it is omitted"))
        return [], problems
    if t == "click":
        mods = [MOD[m] for m in a.get("modifiers", []) if m in MOD]
        if len(mods) != len(a.get("modifiers", [])):
            problems.append(("error", f"unsupported click modifiers {a.get('modifiers')}"))
        button, count = a.get("button", "left"), a.get("count", 1)
        if button == "left":
            name = {1: "left_click", 2: "double_click", 3: "triple_click"}.get(count)
        elif button in ("right", "middle") and count == 1:
            name = f"{button}_click"
        else:
            name = None
        if name is None:
            problems.append(("error", f"{button} button x{count} has no computer-use equivalent"))
            return [], problems
        inp = {"coordinate": coord(a["x"], a["y"])}
        if mods:
            inp["text"] = "+".join(mods)
        return [(name, inp)], problems
    if t == "drag":
        if a.get("button", "left") != "left":
            problems.append(("error", f"{a.get('button')} button drag has no computer-use equivalent"))
            return [], problems
        inp = {"start_coordinate": coord(a["x"], a["y"]), "coordinate": coord(a["x2"], a["y2"])}
        mods = [MOD[m] for m in a.get("modifiers", []) if m in MOD]
        if mods:
            inp["text"] = "+".join(mods)
        return [("left_click_drag", inp)], problems
    if t == "type":
        if a.get("redacted"):
            problems.append(("error", "typed text was redacted by QC (email/secret); it cannot be represented "
                                      "without changing meaning, so this action is omitted"))
            return [], problems
        if a.get("masked_chars"):
            problems.append(("error", "password characters were masked at capture; the typed text is unknown, "
                                      "so this action is omitted"))
            return [], problems
        if not a["text"]:
            problems.append(("warning", "text was typed and deleted again; nothing to type (step omitted)"))
            return [], problems
        # backspaces inside a typing step only corrected characters typed in the same step
        # (deleting pre-existing text is its own "key backspace" step), so the final text is faithful
        return [("type", {"text": a["text"]})], problems
    if t == "key":
        text = _key_text(a["key"], a.get("modifiers", []), problems)
        if text is None:
            return [], problems
        rep = a.get("repeat", 1) or 1
        calls = []
        while rep > 0:
            n = min(rep, 100)
            calls.append(("key", {"text": text, **({"repeat": n} if n > 1 else {})}))
            rep -= n
        return calls, problems
    if t == "scroll":
        calls = []
        mods = [MOD[m] for m in a.get("modifiers", []) if m in MOD]
        events = a.get("events") or []
        for r in a["runs"]:
            if r["direction"] == "none":
                problems.append(("warning", "a zero-delta scroll event was skipped"))
                continue
            amt = r["amount"]
            if amt < 0.5:
                problems.append(("error", f"scroll {r['direction']} {amt:g} notch is below the tool's smallest "
                                          "amount (1 wheel click); not representable"))
                continue
            n = int(amt + 0.5)  # half up (Python's round() is half-to-even: round(0.5) == 0)
            if abs(n - amt) > 1e-9:
                problems.append(("warning", f"scroll {r['direction']} {amt:g} notches rounded to {n} "
                                            "(the tool takes whole wheel clicks)"))
            first = next((e for e in events if r.get("t_start") is not None and abs(e["t"] - r["t_start"]) < 1e-6), None)
            x, y = (first["x"], first["y"]) if first else (a["x"], a["y"])
            inp = {"scroll_direction": r["direction"], "scroll_amount": n, "coordinate": coord(x, y)}
            if mods:
                inp["text"] = "+".join(mods)
            calls.append(("scroll", inp))
        if not calls and not any(lvl == "error" for lvl, _ in problems):
            problems.append(("error", "scroll step has no scroll direction"))
        return calls, problems
    problems.append(("error", f"unknown action type {t}"))
    return [], problems


def claude_record(rec: dict, claude_assets: dict[str, str], scale: float) -> dict:
    frame = (rec["coordinate_space"]["frame_width"], rec["coordinate_space"]["frame_height"])
    sw, sh = round(frame[0] * scale), round(frame[1] * scale)
    rid = rec["recording"]["id"]
    errors: list[str] = []
    warnings: list[str] = []

    def img(obs: dict | None):
        if obs and obs.get("image") and obs["image"] in claude_assets:
            return {"type": "image", "source": {"type": "thinkaloud_asset", "path": claude_assets[obs["image"]],
                                                "media_type": "image/png"}}
        return None

    first_before = img(rec["steps"][0]["observations"]["before"]) if rec["steps"] else img(rec["final_observation"])
    intro = [{"type": "text", "text": f"Task: {rec['recording']['task']}\nDone when: {rec['recording']['success_criteria']}"}]
    if first_before:
        intro.append(first_before)
    else:
        warnings.append("no initial screenshot: the first step has no before-state")
    messages = [{"role": "user", "content": intro}]
    step_map = []
    annotations = {}
    safe = "".join(c if c.isalnum() else "_" for c in rid)[:24]
    pauses = [(p0, p1) for p0, p1 in (rec.get("timeline") or {}).get("pauses", []) if p0 is not None]
    last_seen_ok = True       # the last screen the conversation showed is the current screen
    last_end = None
    for s in rec["steps"]:
        calls, problems = claude_calls(s, scale, frame)
        for lvl, msg in problems:
            (errors if lvl == "error" else warnings).append(f"step {s['uid']}: {msg}")
        annotations[s["uid"]] = {"description": s["description"], "t_start": s["t_start"], "t_end": s["t_end"],
                                 "narration": s["narration"], "reasoning": s["reasoning"],
                                 "flags": s["flags"], "ai_assessment": s["ai_assessment"]}
        if not calls:
            step_map.append({"uid": s["uid"], "message_index": None, "tool_use_ids": [], "omitted": True})
            last_seen_ok = False  # the omitted action may have changed the screen
            continue
        paused = last_end is not None and any(p0 < s["t_start"] and (p1 is None or p1 > last_end) for p0, p1 in pauses)
        if messages[1:] and (paused or not last_seen_ok):
            # the screen the model saw last is not the screen this action was taken on: look again
            before = img(s["observations"]["before"])
            why = "after a pause" if paused else "after an omitted step or a missing after-state"
            if before:
                look = f"toolu_{safe}_{re.sub(r'[^A-Za-z0-9_-]', '_', s['uid'])}_look"
                messages.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": look, "name": "screenshot", "toolset_name": "computer", "input": {}}]})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": look, "toolset_name": "computer", "content": [before]}]})
                warnings.append(f"step {s['uid']}: extra screenshot turn {why} (the screen may have changed)")
            else:
                warnings.append(f"step {s['uid']}: the screen before this action is unknown ({why})")
        uses, results = [], []
        for n, (name, inp) in enumerate(calls):
            tid = f"toolu_{safe}_{re.sub(r'[^A-Za-z0-9_-]', '_', s['uid'])}_{n}"
            uses.append({"type": "tool_use", "id": tid, "name": name, "toolset_name": "computer", "input": inp})
            results.append({"type": "tool_result", "tool_use_id": tid, "toolset_name": "computer",
                            "content": [{"type": "text", "text": "OK"}]})
        shot_id = f"toolu_{safe}_{re.sub(r'[^A-Za-z0-9_-]', '_', s['uid'])}_shot"
        uses.append({"type": "tool_use", "id": shot_id, "name": "screenshot", "toolset_name": "computer", "input": {}})
        after = img(s["observations"]["after"])
        last_seen_ok, last_end = bool(after), s["t_end"]
        if after:
            results.append({"type": "tool_result", "tool_use_id": shot_id, "toolset_name": "computer", "content": [after]})
        else:
            warnings.append(f"step {s['uid']}: no after-state screenshot ({s['observations']['after'].get('reason') or 'not captured'}); "
                            "the screenshot result is an error")
            results.append({"type": "tool_result", "tool_use_id": shot_id, "toolset_name": "computer", "is_error": True,
                            "content": [{"type": "text", "text": "Screenshot unavailable: the after-state was not captured."}]})
        messages.append({"role": "assistant", "content": uses})
        step_map.append({"uid": s["uid"], "message_index": len(messages) - 1,
                         "tool_use_ids": [u["id"] for u in uses], "omitted": False})
        messages.append({"role": "user", "content": results})
    return {
        "format": f"thinkaloud.claude_computer_use/{EXPORT_VERSION}",
        "tool": CLAUDE_TOOL,
        "tool_reference": CLAUDE_DOCS,
        "recording_id": rid,
        "screenshot": {"frame_size": list(frame), "screenshot_size": [sw, sh], "scale": scale,
                       "rule": f"long edge <= {SCREENSHOT_LONG_EDGE}px, aspect kept; x_shot = floor((x_frame + 0.5) * scale), clamped to the screenshot"},
        "messages": messages,
        "step_map": step_map,
        "annotations": {"note": "Dataset metadata, not part of the Claude conversation. Narration is human "
                                "speech-to-text, not model reasoning.",
                        "steps": annotations,
                        "review": {k: rec["review"][k] for k in ("outcome", "outcome_source", "checklist", "notes")}},
        "valid_for_training": not errors,
        "errors": errors,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ bundle
def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_frame(session: Path, rel) -> Path | None:
    """Only frames/<name>.png inside this recording, however the path was written."""
    if not isinstance(rel, str) or not FRAME_RE.fullmatch(rel):
        return None
    p = (session / rel).resolve()
    return p if p.parent == (session / "frames").resolve() and p.is_file() else None


def export_bundle(sessions: list[Path], out_root: Path, formats=("dataset", "claude"),
                  include_media: bool = False, zip_bundle: bool = True, allow_privacy_flags: bool = False) -> dict:
    formats = [f for f in formats if f in ("dataset", "claude")]
    if not formats:
        raise ds.ExportError("no export format selected")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"thinkaloud-export-{stamp}-{len(sessions)}rec-{secrets.token_hex(3)}"
    out = Path(out_root) / name
    tmp = Path(out_root) / (name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    warnings: list[str] = []
    errors: list[str] = []
    skipped: list[dict] = []
    recs = []
    seen_ids: set[str] = set()
    try:
        for session in sessions:
            session = Path(session)
            rid = session.name  # the folder name, never a value from inside the (reviewer-written) file
            try:
                t, notes = effective_trajectory(session)
            except ds.ExportError as e:
                skipped.append({"id": rid, "reason": str(e)})
                continue
            if rid in seen_ids or not re.fullmatch(r"[\w.-]+", rid) or rid in (".", ".."):
                skipped.append({"id": rid, "reason": "duplicate or invalid recording folder name"})
                continue
            privacy = open_privacy_flags(t)
            if privacy and not allow_privacy_flags:
                skipped.append({"id": rid, "reason": f"{len(privacy)} open privacy flag(s): " + "; ".join(privacy),
                                "privacy": privacy})
                continue
            if privacy:
                warnings.append(f"{rid}: exported with {len(privacy)} open privacy flag(s) (confirmed by the user)")
            size = ((t.get("coordinate_space") or {}).get("frame_size")
                    or [(t.get("screen") or {}).get("w"), (t.get("screen") or {}).get("h")])
            if not (size and size[0] and size[1]):
                skipped.append({"id": rid, "reason": "no screen size recorded (the screen capture never started)"})
                continue
            n_err, n_warn = len(errors), len(warnings)
            try:
                seen_ids.add(rid)
                warnings += notes
                t["session_id"] = rid
                rdir = tmp / "recordings" / rid
                (rdir / "assets").mkdir(parents=True)
                files = set()
                for s in t["steps"]:
                    for k in ("before", "after"):
                        f = ((s.get("observations") or {}).get(k) or {}).get("file")
                        if f:
                            files.add(f)
                    if not s.get("observations") and s.get("screenshot"):
                        files.add(s["screenshot"])
                fin = (t.get("final_observation") or {}).get("file") or t.get("final_screenshot")
                if fin:
                    files.add(fin)
                assets = {}
                for f in sorted(files, key=str):
                    src = safe_frame(session, f)
                    if src is None:
                        errors.append(f"{rid}: image reference {str(f)[:80]!r} is not a frame of this recording; not exported")
                        continue
                    dst = f"assets/{Path(f).name}"
                    shutil.copy2(src, rdir / dst)
                    assets[f] = dst
                media_asset = None
                mfile = (t.get("media") or {}).get("file") or "playback.mp4"
                if not re.fullmatch(r"playback(-[0-9a-f]{8})?\.mp4", str(mfile)):
                    mfile = "playback.mp4"
                if include_media and (session / mfile).exists():
                    shutil.copy2(session / mfile, rdir / "assets" / "playback.mp4")
                    media_asset = "assets/playback.mp4"
                elif include_media:
                    warnings.append(f"{rid}: no playback.mp4 to include")
                rec = dataset_record(t, assets, media_asset)
                if "dataset" in formats:
                    (rdir / "trajectory.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
                entry = {"id": rid, "task": rec["recording"]["task"], "outcome": rec["review"]["outcome"],
                         "steps": len(rec["steps"]),
                         "dataset": f"recordings/{rid}/trajectory.json" if "dataset" in formats else None}
                if "claude" in formats:
                    from PIL import Image

                    scale = screenshot_scale(rec["coordinate_space"]["frame_width"], rec["coordinate_space"]["frame_height"])
                    cdir = tmp / "claude" / "assets" / rid
                    cdir.mkdir(parents=True)
                    cassets = {}
                    for rel in sorted(set(assets.values())):
                        with Image.open(rdir / rel) as im:
                            size = (round(im.width * scale), round(im.height * scale))
                            (im if size == im.size else im.resize(size, Image.LANCZOS)).save(cdir / Path(rel).name)
                        cassets[rel] = f"claude/assets/{rid}/{Path(rel).name}"
                    crec = claude_record(rec, cassets, scale)
                    (tmp / "claude").mkdir(exist_ok=True)
                    (tmp / "claude" / f"{rid}.json").write_text(json.dumps(crec, indent=2, ensure_ascii=False), encoding="utf-8")
                    entry["claude"] = f"claude/{rid}.json"
                    entry["claude_valid_for_training"] = crec["valid_for_training"]
                    errors += [f"{rid} (claude): {e}" for e in crec["errors"]]
                    warnings += [f"{rid} (claude): {w}" for w in crec["warnings"]]
                if not rec["review"]["outcome"]:
                    warnings.append(f"{rid}: no human outcome decision; do not use as a positive demonstration")
                for c in rec["review"]["checklist"]:
                    if c["verdict_outdated"]:
                        warnings.append(f"{rid}: checklist item {c['id']} was reworded after its verdict was given")
                for h in rec["review"]["rebase_history"][-1:]:
                    if h.get("dropped"):
                        warnings.append(f"{rid}: {len(h['dropped'])} review edit(s) could not be carried over when the "
                                        f"recording was reprocessed")
                recs.append(entry)
            except Exception as e:  # one broken recording must not take the whole export down
                shutil.rmtree(tmp / "recordings" / rid, ignore_errors=True)
                shutil.rmtree(tmp / "claude" / "assets" / rid, ignore_errors=True)
                (tmp / "claude" / f"{rid}.json").unlink(missing_ok=True)
                del errors[n_err:], warnings[n_warn:]
                seen_ids.discard(rid)
                skipped.append({"id": rid, "reason": f"export failed: {type(e).__name__}: {str(e)[:200]}"})
                continue
        if not recs:
            shutil.rmtree(tmp, ignore_errors=True)
            return {"ok": False, "error": "nothing was exported", "requested": len(sessions), "recordings": 0,
                    "skipped": skipped, "errors": errors, "warnings": warnings}
        # frozen builds (desktop app) report a .pyc path; engine/build.ps1 puts dataset.py beside it
        shutil.copy2(Path(ds.__file__).with_name("dataset.py"), tmp / "thinkaloud_dataset.py")
        (tmp / "README.md").write_text(ds.BUNDLE_README, encoding="utf-8")
        asset_list = []
        for p in sorted(tmp.rglob("*")):
            if p.is_file() and p.name != "manifest.json":
                rel = p.relative_to(tmp).as_posix()
                asset_list.append({"path": rel, "bytes": p.stat().st_size, "sha256": _sha(p)})
        manifest = {"format": f"thinkaloud.bundle/{EXPORT_VERSION}", "created_at": stamp, "generator": GENERATOR,
                    "formats": formats, "claude_tool": CLAUDE_TOOL if "claude" in formats else None,
                    "recordings": recs, "skipped": skipped, "files": asset_list,
                    "export_warnings": warnings, "export_errors": errors}
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        report = ds.validate_bundle(tmp)
        manifest["validation"] = {"ok": report["ok"], "errors": len(report["errors"]), "warnings": len(report["warnings"]),
                                  "validator": "thinkaloud_dataset.py"}
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    zip_path = None
    if zip_bundle:
        zip_path = out.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    z.write(p, f"{out.name}/{p.relative_to(out).as_posix()}")
    return {"ok": True, "bundle": str(out), "zip": str(zip_path) if zip_path else None, "formats": formats,
            "requested": len(sessions), "recordings": len(recs), "skipped": skipped,
            "assets": len(asset_list), "warnings": warnings, "errors": errors,
            "validation": {"ok": report["ok"], "errors": report["errors"], "warnings": report["warnings"]}}
