"""Speech to timestamped segments with faster-whisper, or load a transcript JSON.

Transcript JSON format (also what we write to transcript.json):
    [{"id": 0, "t_start": 1.2, "t_end": 3.4, "text": "..."}, ...]
or {"segments": [...]} with the same items. Times are seconds since the
recording started.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_transcript(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["segments"] if isinstance(data, dict) else data
    return [{"id": i, "t_start": round(float(s["t_start"]), 3),
             "t_end": round(float(s["t_end"]), 3), "text": s["text"].strip()}
            for i, s in enumerate(sorted(items, key=lambda s: s["t_start"]))]


def transcribe(audio: Path, offset_s: float = 0.0, model_size: str = "base.en",
               device: str = "cpu", compute_type: str = "int8") -> list[dict]:
    """offset_s: when audio sample 0 was captured, in recording time."""
    from faster_whisper import WhisperModel  # heavy import, only when needed

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio), vad_filter=True, beam_size=5)
    out = []
    for i, seg in enumerate(segments):
        text = seg.text.strip()
        if text:
            out.append({"id": len(out), "t_start": round(seg.start + offset_s, 3),
                        "t_end": round(seg.end + offset_s, 3), "text": text})
    return out
