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
# How transcripts are made; a cached transcript made another way is redone. "words-v2": per-word
# timings and the task text as context.
TRANSCRIBE_METHOD = "words-v2"
# Transcripts cached before transcript.meta.json existed were all made with this model.
LEGACY_MODEL = "base.en"


def default_model() -> str:
    return os.environ.get("THINKALOUD_WHISPER_MODEL") or DEFAULT_MODEL


def load_transcript(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["segments"] if isinstance(data, dict) else data
    out = []
    for i, s in enumerate(sorted(items, key=lambda s: s["t_start"])):
        seg = {"id": i, "t_start": round(float(s["t_start"]), 3), "t_end": round(float(s["t_end"]), 3),
               "text": s["text"].strip()}
        if s.get("words"):
            seg["words"] = [{"w": str(w["w"]), "t_start": round(float(w["t_start"]), 3),
                             "t_end": round(float(w["t_end"]), 3)} for w in s["words"]]
        out.append(seg)
    return out


def model_downloaded(model_size: str) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(model_size, local_files_only=True)
        return True
    except Exception:
        return False


def context_prompt(task: str = "", criteria: str = "") -> str | None:
    """The task text, given to the model as preceding context. It supplies the vocabulary
    ("Pearson", "SFO") and the punctuation style: without it, large models often return long
    lowercase runs with no sentence breaks, which can't be split at the right places."""
    text = " ".join(x.strip().rstrip(".") + "." for x in (task, criteria) if x and x.strip())
    return text or None


def _norm(s: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def transcribe(audio: Path, offset_s: float = 0.0, model_size: str = DEFAULT_MODEL,
               device: str = "cpu", compute_type: str = "int8", language: str | None = "en",
               log=None, prompt: str | None = None) -> list[dict]:
    """offset_s: when audio sample 0 was captured, in recording time. language None = detect.
    Segments carry per-word timings ("words": [{"w", "t_start", "t_end"}]) so narration can be
    split into sentences and matched to actions (align.phrases)."""
    from faster_whisper import WhisperModel  # heavy import, only when needed

    if log and not model_downloaded(model_size):
        size = SPEECH_MODELS.get(model_size, {}).get("download_mb")
        log(f"downloading the speech model {model_size}{f' (~{size:,} MB)' if size else ''}; "
            "this happens once, the first time it is used...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    if model_size.endswith(".en"):
        language = "en"  # English-only models
    segments, _info = model.transcribe(str(audio), vad_filter=True, beam_size=5, language=language,
                                       word_timestamps=True, initial_prompt=prompt)
    echo = _norm(prompt or "")
    out = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        # a model sometimes repeats its context instead of hearing speech: never keep that as narration
        if echo and len(_norm(text).split()) >= 4 and _norm(text) in echo:
            continue
        # "w" keeps Whisper's leading space: words join with "" ("non" + "-stop" -> "non-stop")
        words = [{"w": w.word, "t_start": round(w.start + offset_s, 3), "t_end": round(w.end + offset_s, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        out.append({"id": len(out), "t_start": round(seg.start + offset_s, 3),
                    "t_end": round(seg.end + offset_s, 3), "text": text, "words": words})
    return out
