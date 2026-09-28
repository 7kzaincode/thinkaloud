"""Speech-model choice (transcript cache per model) and editing a recording's task / "done when"."""
import hashlib
import json
import os
import shutil
import time
import wave
from pathlib import Path

import pytest

from thinkaloud import pipeline
from thinkaloud.batch import input_hash
from thinkaloud.details import DetailsError, update_details
from thinkaloud.pipeline import process

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "synthetic-flight"
quiet = lambda *_: None


@pytest.fixture
def sample(tmp_path):
    if not (SAMPLE / "meta.json").exists():
        pytest.skip("run scripts/make_synthetic.py first")
    s = tmp_path / "rec"
    shutil.copytree(SAMPLE, s, ignore=shutil.ignore_patterns("trajectory*.json", "playback*.mp4"))
    return s


@pytest.fixture
def fake_whisper(monkeypatch):
    calls = []

    def fake(audio, offset_s=0.0, model_size="x", language="en", log=None, **_):
        calls.append(model_size)
        return [{"id": 0, "t_start": 1.0, "t_end": 2.0, "text": f"said with {model_size}"}]

    monkeypatch.setattr(pipeline, "transcribe", fake)
    return calls


def as_whisper_recording(s: Path, model: str | None):
    """Pretend the cached transcript came from speech-to-text (model None = cached before models were recorded)."""
    if model is None:
        (s / "transcript.meta.json").unlink()
    else:
        (s / "transcript.meta.json").write_text(json.dumps({"source": "whisper", "model": model, "method": "words-v2"}),
                                                encoding="utf-8")


def texts(t):
    return [g["text"] for g in t["transcript"]]


def test_a_transcript_is_reused_only_by_the_model_that_made_it(sample, fake_whisper):
    as_whisper_recording(sample, "large-v3-turbo")
    before = json.loads((sample / "transcript.json").read_text(encoding="utf-8"))
    t = process(sample, model="large-v3-turbo", log=quiet, playback=False)
    assert fake_whisper == [] and texts(t) == [g["text"] for g in before]
    assert t["source"]["transcript"] == {"source": "whisper", "model": "large-v3-turbo", "method": "words-v2"}

    t = process(sample, model="small.en", log=quiet, playback=False)
    assert fake_whisper == ["small.en"] and texts(t) == ["said with small.en"]
    assert t["source"]["transcript"]["model"] == "small.en"
    prev = json.loads((sample / "transcript.previous.json").read_text(encoding="utf-8"))
    assert prev["info"]["model"] == "large-v3-turbo" and [g["text"] for g in prev["segments"]] == [g["text"] for g in before]

    process(sample, model="small.en", log=quiet, playback=False)  # same model again: cached
    assert fake_whisper == ["small.en"]


def test_transcripts_cached_before_word_timings_are_redone_once(sample, fake_whisper):
    as_whisper_recording(sample, None)               # made by an older version (base.en, no word timings)
    process(sample, model="base.en", log=quiet, playback=False)
    assert fake_whisper == ["base.en"]
    process(sample, model="base.en", log=quiet, playback=False)
    assert fake_whisper == ["base.en"]                # now cached with word timings


def test_a_supplied_transcript_is_never_replaced_by_speech_to_text(sample, fake_whisper, tmp_path):
    # the sample's scripted transcript is marked as coming from a file
    t = process(sample, model="large-v3-turbo", log=quiet, playback=False)
    assert fake_whisper == [] and t["source"]["transcript"]["source"] == "file"
    custom = tmp_path / "mine.json"
    custom.write_text(json.dumps([{"t_start": 3, "t_end": 4, "text": "hand-made"}]), encoding="utf-8")
    t = process(sample, transcript=custom, model="small.en", log=quiet, playback=False)
    assert texts(t) == ["hand-made"] and t["source"]["transcript"] == {"source": "file", "file": "mine.json"}
    t = process(sample, model="base.en", log=quiet, playback=False)  # later runs keep it
    assert fake_whisper == [] and texts(t) == ["hand-made"]


def test_a_transcript_made_without_word_timings_is_redone_with_the_task_as_context(sample, fake_whisper):
    (sample / "transcript.meta.json").write_text(json.dumps({"source": "whisper", "model": "large-v3-turbo"}),
                                                 encoding="utf-8")
    t = process(sample, model="large-v3-turbo", log=quiet, playback=False)
    assert fake_whisper == ["large-v3-turbo"]
    assert t["source"]["transcript"]["method"] == "words-v2" and t["source"]["transcript"]["context"] is True


def test_without_audio_the_cached_transcript_is_kept_whatever_the_model(sample, fake_whisper):
    as_whisper_recording(sample, "base.en")
    (sample / "audio.wav").unlink(missing_ok=True)
    t = process(sample, model="large-v3-turbo", log=quiet, playback=False)
    assert fake_whisper == [] and t["transcript"]


def test_batch_treats_another_speech_model_as_changed_input(sample):
    assert input_hash(sample, "base.en") != input_hash(sample, "large-v3-turbo")
    assert input_hash(sample, "base.en") == input_hash(sample, "base.en")


# ---- editing the task / "done when" -----------------------------------------------------------
def sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def test_editing_details_updates_meta_and_the_processed_trajectory(sample):
    meta0 = json.loads((sample / "meta.json").read_text(encoding="utf-8"))
    process(sample, log=quiet, playback=False)
    r = update_details(sample, "  Find the cheapest\nnonstop flight  ", "")
    assert r == {"ok": True, "changed": True, "task": "Find the cheapest nonstop flight", "success_criteria": ""}
    meta = json.loads((sample / "meta.json").read_text(encoding="utf-8"))
    assert meta["task"] == "Find the cheapest nonstop flight" and meta["success_criteria"] == ""
    assert meta["as_recorded"] == {"task": meta0["task"], "success_criteria": meta0["success_criteria"]}
    t = json.loads((sample / "trajectory.json").read_text(encoding="utf-8"))
    assert t["task"] == "Find the cheapest nonstop flight"
    assert "missing_success_criteria" in [f["code"] for f in t["session_flags"]]
    assert "no success criteria" in t["qc"]["summary"] and "redacted_steps" in t["qc"]

    update_details(sample, None, "The cheapest nonstop is selected")
    t = json.loads((sample / "trajectory.json").read_text(encoding="utf-8"))
    assert "missing_success_criteria" not in [f["code"] for f in t["session_flags"]]
    assert "no success criteria" not in t["qc"]["summary"]
    meta = json.loads((sample / "meta.json").read_text(encoding="utf-8"))
    assert meta["as_recorded"]["success_criteria"] == meta0["success_criteria"]  # the first text stays on record
    # a full reprocess agrees with the in-place edit
    fresh = process(sample, log=quiet, playback=False)
    assert fresh["task"] == t["task"] and fresh["qc"]["summary"] == t["qc"]["summary"]
    assert sorted(f["code"] for f in fresh["session_flags"]) == sorted(f["code"] for f in t["session_flags"])


def test_editing_details_keeps_an_up_to_date_review_up_to_date(sample):
    process(sample, log=quiet, playback=False)
    traj = sample / "trajectory.json"
    rev = json.loads(traj.read_text(encoding="utf-8"))
    rev["review"].update(base_hash=sha16(traj), outcome="pass", notes="checked")
    (sample / "trajectory.reviewed.json").write_text(json.dumps(rev), encoding="utf-8")
    update_details(sample, "Renamed task", None)
    after = json.loads((sample / "trajectory.reviewed.json").read_text(encoding="utf-8"))
    assert after["task"] == "Renamed task" and after["review"]["outcome"] == "pass"
    assert after["review"]["base_hash"] == sha16(traj)  # still matches: the page won't show "reprocessed"


def test_a_review_that_was_already_behind_is_left_for_the_viewer_to_rebase(sample):
    process(sample, log=quiet, playback=False)
    rev = json.loads((sample / "trajectory.json").read_text(encoding="utf-8"))
    rev["review"]["base_hash"] = "0000000000000000"
    (sample / "trajectory.reviewed.json").write_text(json.dumps(rev), encoding="utf-8")
    update_details(sample, "Renamed task", None)
    after = json.loads((sample / "trajectory.reviewed.json").read_text(encoding="utf-8"))
    assert after["review"]["base_hash"] == "0000000000000000" and after["task"] != "Renamed task"


def test_editing_details_is_refused_while_processing_and_for_bad_input(sample):
    with pytest.raises(DetailsError, match="empty"):
        update_details(sample, "   ", None)
    with pytest.raises(DetailsError, match="too long"):
        update_details(sample, "x" * 1001, None)
    lock = sample / "processing.lock"
    lock.write_text("{}")
    with pytest.raises(DetailsError, match="being processed"):
        update_details(sample, "New name", None)
    old = time.time() - 120  # a lock left behind by a crashed job doesn't block
    os.utime(lock, (old, old))
    assert update_details(sample, "New name", None)["changed"] is True
    assert lock.exists()  # someone else's (stale) lock is not ours to remove
    lock.unlink()
    assert update_details(sample, "New name", None)["changed"] is False
    assert not lock.exists()


def test_editing_details_needs_a_finished_recording(tmp_path):
    (tmp_path / "events.jsonl").write_text("")
    with pytest.raises(DetailsError, match="meta.json"):
        update_details(tmp_path, "x", None)
