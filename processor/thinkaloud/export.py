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
import os
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


def trajectory_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def effective_trajectory(session: Path) -> tuple[dict, list[str]]:
    """The reviewed trajectory if it matches the processor output, else the processor output.
    A review made against an older processor output is an error: its edits would be lost."""
    orig = session / "trajectory.json"
    rev = session / "trajectory.reviewed.json"
    if not orig.exists():
        raise ds.ExportError(f"{session.name}: not processed (no trajectory.json)")
    t = json.loads(orig.read_text(encoding="utf-8"))
    notes: list[str] = []
    if rev.exists():
        r = json.loads(rev.read_text(encoding="utf-8"))
        if r.get("review", {}).get("base_hash") == trajectory_hash(orig):
            return r, notes
        raise ds.ExportError(f"{session.name}: the review was made against an older processing run; "
                             "open it in the viewer once so the edits are carried over, then export again")
    notes.append(f"{session.name}: not reviewed; exported without human decisions")
    return t, notes


# ------------------------------------------------------------------ dataset record
def _action(a: dict) -> dict:
    t = a["type"]
    if t == "click":
        return {"type": "click", "button": a.get("button", "left"), "count": a.get("count", 1),
                "x": a["x"], "y": a["y"], "modifiers": a.get("mods", [])}
    if t == "type":
        out = {"type": "type", "text": a["text"], "redacted": bool(a.get("redacted")),
               "masked_chars": a.get("masked_chars", 0), "backspaces": a.get("backspaces", 0)}
        return out
    if t == "key":
        parts = a["key"].split("+")
        return {"type": "key", "key": parts[-1], "modifiers": parts[:-1], "repeat": a.get("repeat", 1)}
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
                           "ai_check": c.get("ai_check")} for c in review.get("checklist", [])],
            "ai_runs": ai.get("runs", []),
            "ai_checklist_drafts": ai.get("checklist_drafts", []),
        },
        "qc": {"summary": t.get("qc", {}).get("summary"), "counts": t.get("qc", {}).get("counts", {})},
        "session_flags": t.get("session_flags", []),
    }


# ------------------------------------------------------------------ claude computer use
def screenshot_scale(w: int, h: int, long_edge: int = SCREENSHOT_LONG_EDGE) -> float:
    return min(1.0, long_edge / max(w, h))


def _key_text(key: str, mods: list[str], problems: list) -> str | None:
    k = XDOTOOL.get(key.lower(), key if len(key) == 1 else None)
    if k is None:
        problems.append(("error", f"key {key!r} has no computer-use key name"))
        return None
    bad = [m for m in mods if m not in MOD]
    if bad:
        problems.append(("error", f"modifier(s) {bad} not supported"))
        return None
    return "+".join([MOD[m] for m in mods] + [k])


def claude_calls(step: dict, scale: float, frame: tuple[int, int]) -> tuple[list[tuple[str, dict]], list]:
    """Map one dataset step to computer-toolset member calls. Returns (calls, problems)."""
    a = step["action"]
    problems: list = []
    sw, sh = round(frame[0] * scale), round(frame[1] * scale)

    def coord(x, y):
        c = [round(x * scale), round(y * scale)]
        if not (0 <= c[0] < sw and 0 <= c[1] < sh):
            problems.append(("error", f"coordinate {c} outside the {sw}x{sh} screenshot "
                                      "(action on another monitor or outside the captured frame)"))
        return c

    t = a["type"]
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
    if t == "type":
        if a.get("redacted"):
            problems.append(("error", "typed text was redacted by QC (email/secret); it cannot be represented "
                                      "without changing meaning, so this action is omitted"))
            return [], problems
        if a.get("masked_chars"):
            problems.append(("error", "password characters were masked at capture; the typed text is unknown, "
                                      "so this action is omitted"))
            return [], problems
        if a.get("backspaces"):
            problems.append(("warning", f"typing included {a['backspaces']} backspace(s); exported as the final text"))
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
            n = max(1, round(amt))
            if abs(n - amt) > 1e-9:
                problems.append(("warning", f"scroll {r['direction']} {amt:g} notches rounded to {n} "
                                            "(the tool takes whole wheel clicks)"))
            first = next((e for e in events if r.get("t_start") is not None and abs(e["t"] - r["t_start"]) < 1e-6), None)
            x, y = (first["x"], first["y"]) if first else (a["x"], a["y"])
            inp = {"scroll_direction": r["direction"], "scroll_amount": n, "coordinate": coord(x, y)}
            if mods:
                inp["text"] = "+".join(mods)
            calls.append(("scroll", inp))
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
    for s in rec["steps"]:
        calls, problems = claude_calls(s, scale, frame)
        for lvl, msg in problems:
            (errors if lvl == "error" else warnings).append(f"step {s['uid']}: {msg}")
        annotations[s["uid"]] = {"description": s["description"], "t_start": s["t_start"], "t_end": s["t_end"],
                                 "narration": s["narration"], "reasoning": s["reasoning"],
                                 "flags": s["flags"], "ai_assessment": s["ai_assessment"]}
        if not calls:
            step_map.append({"uid": s["uid"], "message_index": None, "tool_use_ids": [], "omitted": True})
            continue
        uses, results = [], []
        for n, (name, inp) in enumerate(calls):
            tid = f"toolu_{safe}_{s['uid']}_{n}"
            uses.append({"type": "tool_use", "id": tid, "name": name, "toolset_name": "computer", "input": inp})
            results.append({"type": "tool_result", "tool_use_id": tid, "toolset_name": "computer",
                            "content": [{"type": "text", "text": "OK"}]})
        shot_id = f"toolu_{safe}_{s['uid']}_shot"
        uses.append({"type": "tool_use", "id": shot_id, "name": "screenshot", "toolset_name": "computer", "input": {}})
        after = img(s["observations"]["after"])
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
                       "rule": f"long edge <= {SCREENSHOT_LONG_EDGE}px, aspect kept; x_shot = round(x_frame * scale)"},
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


def export_bundle(sessions: list[Path], out_root: Path, formats=("dataset", "claude"),
                  include_media: bool = False, zip_bundle: bool = True) -> dict:
    formats = [f for f in formats if f in ("dataset", "claude")]
    if not formats:
        raise ds.ExportError("no export format selected")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"thinkaloud-export-{stamp}-{len(sessions)}rec"
    out = Path(out_root) / name
    tmp = Path(out_root) / (name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    warnings: list[str] = []
    errors: list[str] = []
    recs = []
    try:
        for session in sessions:
            session = Path(session)
            try:
                t, notes = effective_trajectory(session)
            except ds.ExportError as e:
                errors.append(str(e))
                continue
            warnings += notes
            rid = t["session_id"]
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
            for f in sorted(files):
                src = session / f
                if not src.exists():
                    errors.append(f"{rid}: referenced image {f} is missing from the session folder")
                    continue
                dst = f"assets/{Path(f).name}"
                shutil.copy2(src, rdir / dst)
                assets[f] = dst
            media_asset = None
            if include_media and (session / "playback.mp4").exists():
                shutil.copy2(session / "playback.mp4", rdir / "assets" / "playback.mp4")
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
            recs.append(entry)
        if not recs:
            raise ds.ExportError("nothing to export: " + "; ".join(errors))
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
                    "recordings": recs, "files": asset_list,
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
            "recordings": len(recs), "assets": len(asset_list), "warnings": warnings, "errors": errors,
            "validation": {"ok": report["ok"], "errors": report["errors"], "warnings": report["warnings"]}}
