"""Session folder -> trajectory.json (schema 0.2) + playback.mp4.

Reads recorder 0.2 sessions (stills index, screen video, UIA targets, clock
metadata) and recorder 0.1 sessions (one screenshot per click/Enter) alike.
Outputs are written atomically, so an interrupted run never leaves a
half-written trajectory.json behind.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import qc
from .align import align
from .describe import describe, target_reliable
from .media import build_playback
from .observations import assign, extract_video_frames, legacy_assign, load_stills, load_video_frames
from .steps import SegmentConfig, merge_events
from .transcribe import LEGACY_MODEL, default_model, load_transcript, transcribe

SCHEMA_VERSION = "0.2"
SESSION_SCHEMA_V02 = "thinkaloud.session/0.2"


class InputError(Exception):
    """The recording folder is incomplete or corrupt (reported to the user as-is)."""


def read_events(session: Path) -> list[dict]:
    p = session / "events.jsonl"
    if not p.exists():
        raise InputError("events.jsonl is missing: this folder is not a complete recording")
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            raise InputError(f"events.jsonl line {n} is not valid JSON ({e.msg}); the recording may have been cut off")
        if not isinstance(ev, dict) or "t" not in ev or "type" not in ev:
            raise InputError(f"events.jsonl line {n} is not an event (needs 't' and 'type')")
        out.append(ev)
    return out


def read_meta(session: Path) -> dict:
    p = session / "meta.json"
    if not p.exists():
        raise InputError("meta.json is missing: the recorder did not finish writing this recording")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise InputError(f"meta.json is not valid JSON ({e.msg})")


from .fsutil import write_json_atomic  # noqa: E402  (unique temp names, Windows lock retries)


def _transcript(session: Path, supplied: Path | None, model: str, language: str | None, offset_s: float,
                log) -> tuple[list[dict], dict]:
    cached, info_path, audio = session / "transcript.json", session / "transcript.meta.json", session / "audio.wav"
    info = None
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = None
    if info is None and cached.exists():
        info = {"source": "whisper", "model": LEGACY_MODEL}  # cached before models were recorded
    if supplied:
        segments = load_transcript(supplied)
        info = {"source": "file", "file": Path(supplied).name}
        log(f"transcript: {len(segments)} segments from {supplied}")
    elif cached.exists() and (not audio.exists() or info.get("source") == "file" or info.get("model") == model):
        segments = load_transcript(cached)
        log(f"transcript: {len(segments)} segments (cached transcript.json, {info.get('model') or 'from a file'})")
    elif audio.exists():
        if cached.exists():  # made by another model: keep the old one next to it, just in case
            write_json_atomic(session / "transcript.previous.json", {"info": info, "segments": load_transcript(cached)})
            log(f"re-transcribing: the cached transcript was made with {info.get('model')}, not {model}")
        log(f"transcribing audio.wav with faster-whisper ({model})...")
        segments = transcribe(audio, offset_s, model, language=language, log=log)
        info = {"source": "whisper", "model": model, "language": "en" if model.endswith(".en") else language}
        log(f"transcript: {len(segments)} segments")
    else:
        segments, info = [], {"source": "none"}
        log("transcript: none (no audio.wav and no --transcript)")
    write_json_atomic(cached, segments)
    write_json_atomic(info_path, info)
    return segments, info


def process(session: Path, transcript: Path | None = None, model: str | None = None,
            ocr: bool = False, redact: bool = True, log=print, playback: bool = True,
            config: SegmentConfig | None = None, language: str | None = "en") -> dict:
    session = Path(session)
    meta = read_meta(session)
    events = read_events(session)
    legacy = meta.get("schema") != SESSION_SCHEMA_V02
    screen = meta.get("screen", {})
    origin = (screen.get("left", 0), screen.get("top", 0))
    audio_meta = meta.get("audio") or {}
    pauses = (meta.get("input") or {}).get("pauses") or []

    # 1. transcript: explicit file > cached transcript.json (if made by this model, or supplied
    #    as a file) > whisper on audio.wav
    segments, transcript_info = _transcript(session, transcript, model or default_model(), language,
                                            audio_meta.get("offset_s") or 0.0, log)

    # 2. steps
    cfg = config or SegmentConfig()
    cap = meta.get("capture") or {}
    dct = cap.get("double_click_time_s")
    if config is None and dct:
        cfg.double_click_s = float(dct)
    box = cap.get("double_click_size_px")
    if config is None and isinstance(box, list) and box and all(isinstance(v, (int, float)) and v > 0 for v in box):
        cfg.double_click_px = max(1, int(max(box)) // 2)     # the system box is centred on the first click
    steps = merge_events(events, origin=origin, config=cfg)

    # 3. before/after observations
    duration = float(meta.get("duration_s") or (events[-1]["t"] if events else 0.0))
    capture_meta = meta.get("capture") or {}
    if legacy:
        legacy_assign(steps, events)
        final = next((e.get("frame") for e in reversed(events)
                      if e["type"] == "marker" and e.get("name") == "end"), None)
        final_obs = {"status": "ok" if final else "missing", "file": final, "source": "still",
                     "reason": None if final else "no end screenshot"}
    else:
        stills = load_stills(session)
        video = load_video_frames(session)
        needed = assign(steps, stills, video, duration, pauses,
                        settle_s=float(capture_meta.get("settle_s") or 0.6),
                        max_age_s=max(1.0, 3.0 / float(capture_meta.get("fps") or 4.0)))
        if needed:
            try:
                files = extract_video_frames(session, needed, (screen.get("w"), screen.get("h")))
            except Exception as e:  # corrupt/missing video: those observations become missing
                log(f"[warn] could not extract video frames: {e}")
                files = {}
            for s in steps:
                for key in ("before", "after"):
                    o = s["observations"][key]
                    if o.get("source") == "video":
                        f = files.get(o.get("capture_seq"))
                        if f:
                            o["file"] = f
                        else:
                            s["observations"][key] = {"status": "missing", "file": None,
                                                      "reason": "video frame could not be decoded"}
        for s in steps:
            rel = s["action"].get("release") if s["action"].get("drag_problem") else None
            aft = s["observations"]["after"]
            if rel and aft.get("file") and (aft.get("t_capture_start") or 0) < rel["t"]:
                # captured before the drop: it would show the drag in progress, labelled as the result
                s["observations"]["after"] = {"status": "missing", "file": None,
                                              "reason": "the drag ended after later input; no capture after the drop"}
        end = [c for c in stills if "end" in c.kinds]
        if end:
            c = end[-1]
            final_obs = {"status": "ok", "file": c.file, "source": "still", "capture_seq": c.seq,
                         "t_capture_start": c.t0, "t_capture_end": c.t1}
        else:
            final_obs = {"status": "missing", "file": None, "reason": "no end capture"}
    final = final_obs.get("file")
    if final and not (session / final).exists():
        final_obs = {"status": "missing", "file": None, "reason": "end capture file is missing"}
        final = None

    # 4. narration alignment
    align(segments, steps)

    # 5. quality checks
    for s in steps:
        s["flags"] = []
        s["screenshot"] = (s["observations"]["before"] or {}).get("file")  # 0.1-compatible field
    qc.use_subject_key(session)
    qc.check_steps(steps, segments, pauses=pauses, legacy=legacy)
    qc.check_context(steps)
    qc.check_narration(steps, segments)
    meta["_has_end_marker"] = any(e["type"] == "marker" and e.get("name") == "end" for e in events)
    if ocr:
        try:
            qc.ocr_emails(steps, session)
        except ImportError:
            log("[warn] --ocr needs pytesseract + Tesseract (use the Docker image built with WITH_OCR=1)")
    session_flags = qc.check_session(meta, steps, segments, final, legacy=legacy)
    n_redacted = qc.redact(steps) if redact else 0
    if redact:
        qc.redact_narration(steps, segments)
    report = qc.summarize(steps, session_flags)
    report["redacted_steps"] = n_redacted

    # 6. playback media
    media = build_playback(session, meta, log) if playback and not legacy else {
        "file": None, "status": "unavailable", "video": False, "audio": (session / "audio.wav").exists(),
        "error": "recorder 0.1 recorded no screen video" if legacy else "skipped"}
    if legacy and (session / "audio.wav").exists() and playback:
        media = build_playback(session, {**meta, "video": {}}, log)
        media["note"] = "audio only: recorder 0.1 recorded no screen video"

    cs = meta.get("coordinate_space") or {}
    traj = {
        "schema_version": SCHEMA_VERSION,
        "session_id": meta.get("session_id", session.name),
        "task": meta.get("task", ""),
        "success_criteria": meta.get("success_criteria", ""),
        "recorded_at": meta.get("started_at"),
        "duration_s": meta.get("duration_s"),
        "source": {"session_schema": meta.get("schema", "thinkaloud.session/0.1"),
                   "recorder_version": meta.get("recorder_version"), "legacy": legacy,
                   "transcript": transcript_info,
                   "segmentation": {"type_gap_s": cfg.type_gap_s, "scroll_pause_s": cfg.scroll_pause_s,
                                    "repeat_gap_s": cfg.repeat_gap_s, "double_click_s": cfg.double_click_s,
                                    "double_click_px": cfg.double_click_px}},
        "screen": {"w": screen.get("w"), "h": screen.get("h")},
        "coordinate_space": {
            "frame_size": [screen.get("w"), screen.get("h")],
            "frame_origin_on_screen": [origin[0], origin[1]],
            "monitor_dpi_scale": cs.get("monitor_dpi_scale"),
            "units": "physical pixels; action x/y and target rects are relative to the frame",
        },
        "timeline": {"clock": "recorder perf_counter", "unit": "s", "origin": "recording start",
                     "audio_offset_s": audio_meta.get("offset_s"),
                     "pauses": pauses},
        "media": media,
        "final_screenshot": final,
        "final_observation": final_obs,
        "steps": [],
        "transcript": segments,
        "session_flags": session_flags,
        "qc": report,
        "review": {"outcome": None, "reviewer": None, "notes": "", "edited": False},
    }
    for s in steps:
        tgt = s.get("target")
        if tgt is not None:
            tgt = dict(tgt)
            tgt["reliable"] = target_reliable(tgt)
            if tgt.get("rect"):
                r = tgt["rect"]
                tgt["frame_rect"] = [r[0] - origin[0], r[1] - origin[1], r[2], r[3]]
        traj["steps"].append({
            "id": s["id"], "uid": s["uid"], "t_start": s["t_start"], "t_end": s["t_end"],
            "action": s["action"], "description": describe({**s, "target": tgt}),
            "context": s.get("context"), "target": tgt,
            "observations": s["observations"], "screenshot": s["screenshot"],
            "reasoning": s["reasoning"], "reasoning_source": s["reasoning_source"],
            "carried_from": s["carried_from"], "transcript_ids": s["transcript_ids"],
            "narration": s["narration"], "event_range": s["event_range"], "n_events": s["n_events"],
            "flags": s["flags"]})
    write_json_atomic(session / "trajectory.json", traj)
    return traj
