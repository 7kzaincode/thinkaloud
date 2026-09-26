"""Session folder -> trajectory.json."""
from __future__ import annotations

import json
from pathlib import Path

from . import qc
from .align import align
from .steps import merge_events
from .transcribe import load_transcript, transcribe

SCHEMA_VERSION = "0.1"


def read_events(session: Path) -> list[dict]:
    lines = (session / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def process(session: Path, transcript: Path | None = None, model: str = "base.en",
            ocr: bool = False, redact: bool = True, log=print) -> dict:
    session = Path(session)
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    events = read_events(session)
    screen = meta.get("screen", {})

    # 1. transcript: explicit file > cached transcript.json > whisper on audio.wav
    cached = session / "transcript.json"
    if transcript:
        segments = load_transcript(transcript)
        log(f"transcript: {len(segments)} segments from {transcript}")
    elif cached.exists():
        segments = load_transcript(cached)
        log(f"transcript: {len(segments)} segments (cached transcript.json)")
    elif (session / "audio.wav").exists():
        log(f"transcribing audio.wav with faster-whisper ({model})...")
        segments = transcribe(session / "audio.wav", meta.get("audio", {}).get("offset_s", 0.0), model)
        log(f"transcript: {len(segments)} segments")
    else:
        segments = []
        log("transcript: none (no audio.wav and no --transcript)")
    cached.write_text(json.dumps(segments, indent=2), encoding="utf-8")

    # 2. steps, 3. alignment
    steps = merge_events(events, origin=(screen.get("left", 0), screen.get("top", 0)))
    align(segments, steps)

    # 4. quality checks
    final = next((e.get("frame") for e in reversed(events)
                  if e["type"] == "marker" and e.get("name") == "end"), None)
    if final and not (session / final).exists():
        final = None
    for s in steps:
        s["flags"] = []
    qc.check_steps(steps, segments)
    if ocr:
        try:
            qc.ocr_emails(steps, session)
        except ImportError:
            log("[warn] --ocr needs pytesseract + Tesseract (use the Docker image)")
    session_flags = qc.check_session(meta, steps, segments, final)
    n_redacted = qc.redact(steps) if redact else 0
    report = qc.summarize(steps, session_flags)
    report["redacted_steps"] = n_redacted

    traj = {
        "schema_version": SCHEMA_VERSION,
        "session_id": meta.get("session_id", session.name),
        "task": meta.get("task", ""),
        "success_criteria": meta.get("success_criteria", ""),
        "recorded_at": meta.get("started_at"),
        "duration_s": meta.get("duration_s"),
        "screen": {"w": screen.get("w"), "h": screen.get("h")},
        "final_screenshot": final,
        "steps": [{"id": s["id"], "t_start": s["t_start"], "t_end": s["t_end"],
                   "action": s["action"], "screenshot": s["screenshot"],
                   "reasoning": s["reasoning"], "reasoning_source": s["reasoning_source"],
                   "carried_from": s["carried_from"], "transcript_ids": s["transcript_ids"],
                   "flags": s["flags"]} for s in steps],
        "transcript": segments,
        "session_flags": session_flags,
        "qc": report,
        "review": {"outcome": None, "reviewer": None, "notes": "", "edited": False},
    }
    (session / "trajectory.json").write_text(json.dumps(traj, indent=2), encoding="utf-8")
    return traj
