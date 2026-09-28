"""Speech to timestamped segments with faster-whisper, or load a transcript JSON.

Transcript JSON format (also what we write to transcript.json):
    [{"id": 0, "t_start": 1.2, "t_end": 3.4, "text": "..."}, ...]
or {"segments": [...]} with the same items. Times are seconds since the
recording started.

Everything runs on this computer; the model is downloaded once (into HF_HOME) the first
time it is used. transcript.meta.json next to transcript.json records which model made it
(or that it came from a file), so switching models re-transcribes and a supplied
transcript is never replaced.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Offered in the app's Settings. The viewer keeps a copy of this table (viewer/lib/speech.ts).
SPEECH_MODELS = {
    "base.en": {"label": "Fast", "download_mb": 145},
    "small.en": {"label": "Balanced", "download_mb": 465},
    "large-v3-turbo": {"label": "Accurate", "download_mb": 1620},
}
DEFAULT_MODEL = "large-v3-turbo"
# Transcripts cached before transcript.meta.json existed were all made with this model.
LEGACY_MODEL = "base.en"


def default_model() -> str:
    return os.environ.get("THINKALOUD_WHISPER_MODEL") or DEFAULT_MODEL


def load_transcript(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["segments"] if isinstance(data, dict) else data
    return [{"id": i, "t_start": round(float(s["t_start"]), 3),
             "t_end": round(float(s["t_end"]), 3), "text": s["text"].strip()}
            for i, s in enumerate(sorted(items, key=lambda s: s["t_start"]))]


def model_downloaded(model_size: str) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(model_size, local_files_only=True)
        return True
    except Exception:
        return False


def transcribe(audio: Path, offset_s: float = 0.0, model_size: str = DEFAULT_MODEL,
               device: str = "cpu", compute_type: str = "int8", language: str | None = "en",
               log=None) -> list[dict]:
    """offset_s: when audio sample 0 was captured, in recording time. language None = detect."""
    from faster_whisper import WhisperModel  # heavy import, only when needed

    if log and not model_downloaded(model_size):
        size = SPEECH_MODELS.get(model_size, {}).get("download_mb")
        log(f"downloading the speech model {model_size}{f' (~{size:,} MB)' if size else ''}; "
            "this happens once, the first time it is used...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    if model_size.endswith(".en"):
        language = "en"  # English-only models
    segments, _info = model.transcribe(str(audio), vad_filter=True, beam_size=5, language=language)
    out = []
    for i, seg in enumerate(segments):
        text = seg.text.strip()
        if text:
            out.append({"id": len(out), "t_start": round(seg.start + offset_s, 3),
                        "t_end": round(seg.end + offset_s, 3), "text": text})
    return out
