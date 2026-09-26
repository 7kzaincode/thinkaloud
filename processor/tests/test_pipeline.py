import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from thinkaloud import qc
from thinkaloud.align import align, assign
from thinkaloud.describe import describe
from thinkaloud.pipeline import process

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "synthetic-flight"
LEGACY = Path(__file__).resolve().parent / "fixtures" / "legacy-v01"


# ---- alignment -------------------------------------------------------------
STEPS = [{"id": i, "t_start": t, "t_end": t} for i, t in enumerate([5.0, 6.0, 30.0])]


def test_narration_goes_to_next_step_within_lookahead():
    assert assign({"t_start": 1.0, "t_end": 3.0}, STEPS) == 0      # 5.0 <= 3+4
    assert assign({"t_start": 5.5, "t_end": 6.5}, STEPS) == 1


def test_narration_falls_back_to_previous_step_then_none():
    assert assign({"t_start": 10.0, "t_end": 12.0}, STEPS) == 1    # commentary after the fact
    assert assign({"t_start": 0.0, "t_end": 0.5}, [{"id": 0, "t_start": 9.0}]) is None


def test_carry_forward_stops_at_pause_and_timing_is_recorded():
    steps = [{"id": i, "t_start": t, "t_end": t} for i, t in enumerate([2.0, 4.0, 20.0])]
    segs = [{"id": 0, "t_start": 0.0, "t_end": 1.5, "text": "logging in"},
            {"id": 1, "t_start": 21.0, "t_end": 22.0, "text": "that worked"}]
    align(segs, steps)
    assert [s["reasoning_source"] for s in steps] == ["narrated", "carried", "narrated"]
    assert steps[1]["carried_from"] == 0 and steps[1]["narration"] == []
    assert steps[0]["narration"][0]["timing"] == "before_action"
    assert steps[2]["narration"][0]["timing"] == "after_action"   # retrospective, kept distinguishable


# ---- qc -----------------------------------------------------------------------
@pytest.mark.parametrize("text,ctx,after_email,expected", [
    ("Hunter2!x", "", False, True),
    ("hello world", "", False, False),
    ("YYZ", "", False, False),
    ("sunshine", "now the password", False, True),
    ("sunshine", "", True, True),
    ("sunshine", "", False, False),
    ("me@example.com", "password", False, False),
])
def test_password_heuristic(text, ctx, after_email, expected):
    assert bool(qc.looks_like_password(text, ctx, after_email)) is expected


def test_idle_gap_severity_and_pauses_do_not_count():
    steps = [{"id": 0, "t_start": 1, "t_end": 1, "action": {"type": "click"}, "reasoning": "x"},
             {"id": 1, "t_start": 30, "t_end": 30, "action": {"type": "click"}, "reasoning": "y"}]
    qc.check_steps(steps, [])
    assert [f["severity"] for f in steps[1]["flags"] if f["code"] == "idle_gap"] == ["warn"]
    for s in steps:
        s["flags"] = []
    qc.check_steps(steps, [{"t_start": 10, "t_end": 15}])
    assert [f["severity"] for f in steps[1]["flags"] if f["code"] == "idle_gap"] == ["info"]
    for s in steps:
        s["flags"] = []
    qc.check_steps(steps, [], pauses=[[5.0, 25.0]])                   # 29 s gap, 20 s of it paused
    assert not [f for f in steps[1]["flags"] if f["code"] == "idle_gap"]


def test_masked_password_is_not_flagged_as_secret():
    steps = [{"id": 0, "t_start": 1, "t_end": 2, "reasoning": "x",
              "action": {"type": "type", "text": "•" * 10, "masked_chars": 10}}]
    qc.check_steps(steps, [])
    codes = [f["code"] for f in steps[0]["flags"]]
    assert "masked_input" in codes and "possible_secret" not in codes


# ---- descriptions -----------------------------------------------------------------
def test_description_uses_reliable_target_only():
    base = {"action": {"type": "click", "x": 5, "y": 6, "button": "left"}}
    ok = {"status": "ok", "role": "button", "name": "Add to Cart", "latency_ms": 20}
    assert describe({**base, "target": ok}) == "Clicked the Add to Cart button"
    assert describe({**base, "target": {**ok, "latency_ms": 900}}) == "Clicked at (5, 6)"
    assert describe({**base, "target": {"status": "timeout"}}) == "Clicked at (5, 6)"
    assert describe({**base, "target": {**ok, "role": "edit", "name": "Email"}}) == "Clicked the Email field"
    assert describe({"action": {"type": "scroll", "runs": [{"direction": "down", "amount": 5},
                                                           {"direction": "up", "amount": 1}]}}) \
        == "Scrolled down 5, then up 1"


def test_description_of_drags_and_deleted_typing():
    drag = {"type": "drag", "x": 10, "y": 20, "x2": 300, "y2": 40, "button": "left"}
    assert describe({"action": drag}) == "Dragged from (10, 20) to (300, 40)"
    ok = {"status": "ok", "role": "slider", "name": "Max price", "latency_ms": 30}
    assert describe({"action": drag, "target": ok}) == "Dragged the Max price slider to (300, 40)"
    assert describe({"action": {**drag, "button": "right"}}) == "Right-dragged from (10, 20) to (300, 40)"
    typed = {"type": "type", "text": "", "backspaces": 2, "keystrokes": "ab⌫⌫"}
    assert describe({"action": typed}) == "Typed 2 characters and deleted them"
    assert describe({"action": {"type": "key", "key": "backspace", "repeat": 3}}) == "Pressed Backspace ×3"


# ---- end to end ---------------------------------------------------------------------
@pytest.fixture
def sample(tmp_path):
    if not (SAMPLE / "meta.json").exists():
        pytest.skip("run scripts/make_synthetic.py first")
    s = tmp_path / "synthetic-flight"
    shutil.copytree(SAMPLE, s, ignore=shutil.ignore_patterns("trajectory*.json", "playback.mp4"))
    return s


def test_synthetic_v02_session_end_to_end(sample):
    traj = process(sample, log=lambda *_: None)
    assert traj["schema_version"] == "0.2" and traj["source"]["legacy"] is False
    assert traj["qc"]["summary"] == ("18 steps, 1 missing reasoning, 1 idle gap, 1 typed email, "
                                     "1 redacted value on screen, 1 missing after-state, 1 masked input")
    email_step = next(s for s in traj["steps"] if s["action"].get("redacted"))
    assert {"possible_email", "redacted_value_on_screen"} <= {f["code"] for f in email_step["flags"]}
    steps = {s["uid"]: s for s in traj["steps"]}
    scroll = next(s for s in traj["steps"] if s["action"]["type"] == "scroll")
    assert [(r["direction"], r["amount"]) for r in scroll["action"]["runs"]] == [("down", 5.0), ("up", 1.0)]
    assert steps["s000001"]["description"] == "Clicked the Email field"
    # ownership invariants hold for every step
    for i, s in enumerate(traj["steps"]):
        b, a = s["observations"]["before"], s["observations"]["after"]
        if b["file"]:
            assert b["t_capture_end"] <= s["t_start"] and (sample / b["file"]).exists()
        if a["file"]:
            assert a["t_capture_start"] >= s["t_end"] and (sample / a["file"]).exists()
            if i + 1 < len(traj["steps"]):
                assert a["t_capture_end"] <= traj["steps"][i + 1]["t_start"]
    yyz = next(s for s in traj["steps"] if s["action"].get("text") == "YYZ")
    assert yyz["observations"]["after"]["status"] == "missing"
    raw = (sample / "trajectory.json").read_text(encoding="utf-8")
    assert "zain.demo@" not in raw                       # typed email redacted
    assert traj["final_observation"]["status"] == "ok"
    if (sample / "audio.wav").exists():
        assert traj["media"]["status"] == "ok" and (sample / "playback.mp4").exists()


def test_reprocessing_is_idempotent(sample):
    a = process(sample, log=lambda *_: None)
    b = process(sample, log=lambda *_: None)
    strip = lambda t: json.dumps({k: v for k, v in t.items() if k != "media"}, sort_keys=True)
    assert strip(a) == strip(b)
    assert [s["uid"] for s in a["steps"]] == [s["uid"] for s in b["steps"]]


def test_legacy_v01_recording_still_processes(tmp_path):
    s = tmp_path / "legacy"
    shutil.copytree(LEGACY, s)
    traj = process(s, log=lambda *_: None)
    assert traj["source"]["legacy"] is True
    assert "legacy_recording" in [f["code"] for f in traj["session_flags"]]
    for st in traj["steps"]:
        assert st["observations"]["after"]["status"] == "missing"          # never invented
        assert st["observations"]["before"]["status"] in ("at_action", "legacy_earlier_action", "missing")
    assert traj["steps"][0]["screenshot"]                                   # 0.1-compatible field kept
    assert traj["final_observation"]["status"] == "ok"


def test_playback_places_audio_on_recording_timeline(sample):
    """A click (screen turns white) and a beep both happen at t=2.0 on the recording clock.
    In playback.mp4 the first bright video frame and the beep onset must line up with it."""
    import av
    from fractions import Fraction

    s = sample
    for p in ("screen.mkv", "video_frames.jsonl", "audio.wav"):
        (s / p).unlink(missing_ok=True)
    offset = 0.137                                   # audio sample 0 captured at t=0.137
    sr = 16000
    pcm = np.zeros(int(4 * sr), np.int16)
    onset = int((2.0 - offset) * sr)
    pcm[onset:onset + 1600] = (8000 * np.sin(np.arange(1600) * 2 * np.pi * 1000 / sr)).astype(np.int16)
    with wave.open(str(s / "audio.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr), w.writeframes(pcm.tobytes())
    c = av.open(str(s / "screen.mkv"), "w")
    st = c.add_stream("libx264", rate=4)
    st.width, st.height, st.pix_fmt = 64, 48, "yuv420p"
    st.codec_context.time_base = Fraction(1, 1000)
    st.options = {"bf": "0"}
    for i in range(16):
        t0 = 0.1 + i * 0.25
        img = np.full((48, 64, 3), 255 if t0 >= 2.0 else 0, np.uint8)
        f = av.VideoFrame.from_ndarray(img, format="rgb24")
        f.pts, f.time_base = int(round((t0 + 0.015) * 1000)), Fraction(1, 1000)
        for p in st.encode(f):
            c.mux(p)
    for p in st.encode():
        c.mux(p)
    c.close()
    meta = json.loads((s / "meta.json").read_text())
    meta["video"]["frames"] = 16
    meta["audio"].update(offset_s=offset, file="audio.wav")
    meta["duration_s"] = 4.0
    from thinkaloud.media import build_playback
    info = build_playback(s, meta, log=lambda *_: None)
    assert info["status"] == "ok" and info["video_mode"] == "copy"
    with av.open(str(s / "playback.mp4")) as pc:
        v = pc.streams.video[0]
        bright = next(float(fr.pts * fr.time_base) for fr in pc.decode(v) if fr.to_ndarray().mean() > 100)
    with av.open(str(s / "playback.mp4")) as pc:
        a = pc.streams.audio[0]
        samples = np.concatenate([fr.to_ndarray().reshape(-1) for fr in pc.decode(a)])
        start = float(a.start_time * a.time_base) if a.start_time else 0.0
    beep = start + np.argmax(np.abs(samples) > 0.05) / a.rate
    assert abs(bright - 2.115) < 0.02          # frame captured at 2.1 (+15 ms midpoint): first after the click
    assert abs(beep - 2.0) < 0.07              # AAC priming/frame granularity tolerance


def test_emails_and_tokens_in_urls_titles_are_flagged_and_redacted():
    steps = [{"id": 0, "t_start": 1, "t_end": 1, "reasoning": "x", "flags": [],
              "action": {"type": "click", "x": 1, "y": 1},
              "target": {"status": "ok", "name": "Inbox - bob@example.com",
                         "url": "https://mail.example/inbox?auth_token=abc123&tab=1"},
              "context": {"window_title": "bob@example.com - Mail"}}]
    qc.check_context(steps)
    codes = {f["code"] for f in steps[0]["flags"]}
    assert {"email_in_context", "sensitive_url"} <= codes
    qc.redact(steps)
    blob = json.dumps(steps[0])
    assert "bob@example.com" not in blob and "abc123" not in blob


def test_segmentation_uses_the_recorded_double_click_box(sample):
    meta = json.loads((sample / "meta.json").read_text(encoding="utf-8"))
    meta.setdefault("capture", {})["double_click_size_px"] = [30, 30]
    (sample / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    traj = process(sample, log=lambda *_: None)
    assert traj["source"]["segmentation"]["double_click_px"] == 15
    meta["capture"]["double_click_size_px"] = "garbage"
    (sample / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert process(sample, log=lambda *_: None)["source"]["segmentation"]["double_click_px"] == 2


def _typing_step(keystrokes: str) -> dict:
    text = []
    for ch in keystrokes:
        if ch == "⌫":
            text = text[:-1]
        else:
            text.append(ch)
    a = {"type": "type", "text": "".join(text)}
    if "⌫" in keystrokes:
        a.update(backspaces=keystrokes.count("⌫"), keystrokes=keystrokes)
    return {"id": 0, "t_start": 1.0, "t_end": 2.0, "action": a, "flags": [], "reasoning": "", "observations": {}}


@pytest.mark.parametrize("keystrokes,code", [
    ("bob@example.com" + "⌫" * 15, "possible_email"),               # typed, then deleted entirely
    ("Hunter2!x" + "⌫" * 9, "possible_secret"),                     # password typed into the wrong field
    ("bob.smith@exampel" + "⌫" * 5 + "ample.com", "possible_email"),  # corrected mid-typing
])
def test_deleted_text_is_checked_and_never_kept(keystrokes, code):
    steps = [_typing_step(keystrokes)]
    qc.check_steps(steps, [])
    assert code in {f["code"] for f in steps[0]["flags"]}
    qc.redact(steps)
    a = steps[0]["action"]
    assert a["redacted"] and "keystrokes" not in a
    blob = json.dumps(steps) + describe(steps[0])
    for secret in ("bob", "example", "Hunter2", "exampel"):
        assert secret not in blob


def test_describing_deleted_typing_never_quotes_it():
    s = _typing_step("abc" + "⌫" * 3)
    assert describe(s) == "Typed 3 characters and deleted them"
    assert qc.deleted_runs("abc⌫⌫d⌫e") == ["bc", "d"]


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows file locking")
def test_reprocessing_while_the_replay_is_open_keeps_a_versioned_copy(sample, monkeypatch):
    from thinkaloud import fsutil, media
    process(sample, log=lambda *_: None)
    monkeypatch.setattr(media, "replace_retry", lambda a, b: (_ for _ in ()).throw(PermissionError("in use")))
    with open(sample / "playback.mp4", "rb"):                          # a review page streaming it
        t = process(sample, log=lambda *_: None)
    assert media.VERSIONED_RE.fullmatch(t["media"]["file"]) and (sample / t["media"]["file"]).exists()
    assert not list(sample.glob("playback.mp4.*.tmp"))
    monkeypatch.setattr(media, "replace_retry", fsutil.replace_retry)
    t = process(sample, log=lambda *_: None)                           # released: back to the plain name
    assert t["media"]["file"] == "playback.mp4" and not list(sample.glob("playback-*.mp4"))


def test_video_fallback_frames_are_padded_to_the_screen_size(sample, tmp_path):
    from PIL import Image
    from thinkaloud.observations import Capture, extract_video_frames, load_video_frames
    caps = load_video_frames(sample)[:1]
    if not caps:
        pytest.skip("sample has no video frames")
    files = extract_video_frames(sample, caps, (1601, 901))            # an odd-sized screen
    with Image.open(sample / files[caps[0].seq]) as im:
        assert im.size == (1601, 901)


def test_description_of_unrepresentable_drags_ambiguous_targets_and_ctrl_plus():
    from thinkaloud.describe import key_label
    a = {"type": "click", "x": 1, "y": 2, "button": "left", "count": 2, "release": {"x": 90, "y": 2, "t": 3.0},
         "drag_problem": "double-click drag"}
    assert describe({"action": a}) == "Double-clicked and dragged from (1, 2) to (90, 2) (double-click drag)"
    ok = {"status": "ok", "role": "button", "name": "Buy", "latency_ms": 20, "ambiguous": True}
    assert describe({"action": {"type": "click", "x": 5, "y": 6, "button": "left"}, "target": ok}) == "Clicked at (5, 6)"
    assert key_label("ctrl++") == "Ctrl++" and key_label("ctrl+shift+t") == "Ctrl+Shift+T"
