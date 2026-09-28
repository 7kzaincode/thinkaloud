"""Narration split into sentences with word timings and matched to the actions they explain.

Timings below are from a real think-aloud recording (a flight search), trimmed."""
import sys
import types

import pytest

from thinkaloud import qc, transcribe
from thinkaloud.align import align, assign, is_noise, phrases


def words(*items):
    """("One", 9.0, 9.4), ... -> Whisper-style words (leading space = a new word)."""
    return [{"w": w if w.startswith("-") else " " + w, "t_start": a, "t_end": b} for w, a, b in items]


def seg(ws):
    return {"id": 0, "t_start": ws[0]["t_start"], "t_end": ws[-1]["t_end"], "text": "", "words": ws}


def click(i, t, kind="click", t_end=None, **action):
    return {"id": i, "t_start": t, "t_end": t if t_end is None else t_end, "action": {"type": kind, **action}}


# a flight search: menu at 10.8 and 11.6, From field at 20.2, typing 21.5-23.2, To field at 31.7
STEPS = [click(0, 1.0, "key", key="alt"), click(1, 10.8), click(2, 11.6), click(3, 20.2),
         click(4, 21.5, "type", t_end=23.2, text="Pearson"), click(5, 24.2), click(6, 31.7)]
SPEECH = words(("One", 9.0, 9.4), ("way,", 9.5, 9.7), ("I", 9.9, 10.0), ("just", 10.1, 10.2), ("need", 10.2, 10.3),
               ("to", 10.3, 10.4), ("get", 10.4, 10.5), ("there.", 10.5, 10.6),
               ("Pearson", 13.2, 13.4), ("specifically,", 13.5, 13.9),
               ("it's", 17.9, 18.0), ("the", 18.1, 18.2), ("one", 18.2, 18.3), ("I", 18.4, 18.5), ("can", 18.5, 18.6),
               ("get", 18.8, 18.9), ("to", 19.0, 19.1), ("from", 19.1, 19.2), ("Waterloo.", 19.3, 19.8),
               ("SFO,", 26.3, 26.8), ("not", 26.9, 27.0), ("any", 27.1, 27.2), ("San", 27.3, 27.4),
               ("Francisco", 27.5, 27.7), ("airport.", 27.8, 28.2))


def test_sentences_stay_whole_and_go_to_the_action_they_announce():
    ph = phrases([seg(SPEECH)], STEPS)
    assert [p["text"] for p in ph] == [
        "One way, I just need to get there.",
        "Pearson specifically, it's the one I can get to from Waterloo.",  # a 4 s pause, no action: one sentence
        "SFO, not any San Francisco airport.",
    ]
    steps = [dict(s) for s in STEPS]
    align(ph, steps)
    by = {s["id"]: s for s in steps}
    assert by[1]["reasoning"] == "One way, I just need to get there."
    assert by[2]["reasoning_source"] == "carried" and by[2]["carried_from"] == 1
    assert by[3]["reasoning"].startswith("Pearson specifically")
    assert by[4]["carried_from"] == 3 and by[5]["carried_from"] == 3     # typing and picking the suggestion
    assert by[6]["reasoning"] == "SFO, not any San Francisco airport."
    assert by[0]["reasoning_source"] is None                            # the stray Alt press gets nothing


def test_a_pause_for_an_action_splits_speech_even_without_punctuation():
    ws = words(("one", 9.0, 9.4), ("way", 9.5, 9.7), ("i", 9.9, 10.0), ("need", 10.2, 10.3), ("to", 10.3, 10.4),
               ("get", 10.4, 10.5), ("there", 10.5, 10.6),           # clicks at 10.8 and 11.6 while silent
               ("pearson", 12.5, 13.0), ("specifically", 13.6, 14.0))
    ph = phrases([seg(ws)], STEPS)
    assert [p["text"] for p in ph] == ["one way i need to get there", "pearson specifically"]


def test_talking_straight_through_a_click_keeps_the_sentence_together():
    ws = words(("I'll", 20.0, 20.1), ("pick", 20.12, 20.3), ("this", 20.32, 20.5), ("one.", 20.52, 20.8))
    ph = phrases([seg(ws)], [click(0, 20.31)])  # no pause around the click
    assert [p["text"] for p in ph] == ["I'll pick this one."]


def test_words_join_like_the_speech_to_text_output():
    ws = words(("Non", 44.5, 44.6), ("-stop", 44.6, 44.8), ("only.", 44.9, 45.1))
    assert phrases([seg(ws)], [])[0]["text"] == "Non-stop only."


def test_transcripts_without_word_timings_keep_their_segments():
    segs = [{"id": 0, "t_start": 1.0, "t_end": 9.0, "text": "a long supplied segment"}]
    assert phrases(segs, STEPS) is segs


def test_speech_while_typing_belongs_to_the_typing_and_stray_modifiers_are_skipped():
    assert assign({"t_start": 22.0, "t_end": 22.5}, STEPS) == 4
    assert assign({"t_start": 0.2, "t_end": 0.8}, STEPS) is None        # only the Alt press is near: unassigned
    assert is_noise(STEPS[0]) and not is_noise(click(9, 1.0, "key", key="enter"))


def test_a_stray_modifier_press_is_not_flagged_for_missing_reasoning():
    steps = [dict(click(0, 1.0, "key", key="alt"), reasoning=""), dict(click(1, 5.0), reasoning="")]
    for s in steps:
        s["flags"] = []
    qc.check_steps(steps, [])
    assert [f["code"] for f in steps[0]["flags"]] == []
    assert "missing_reasoning" in [f["code"] for f in steps[1]["flags"]]


# ---- transcription -----------------------------------------------------------------------------
class FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class FakeSeg:
    def __init__(self, text, start, end, words):
        self.text, self.start, self.end, self.words = text, start, end, words


@pytest.fixture
def fake_model(monkeypatch):
    seen = {}

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            seen.update(kw)
            return iter([
                FakeSeg(" One way, I just need to get there.", 1.0, 3.0,
                        [FakeWord(" One", 1.0, 1.2), FakeWord(" way,", 1.3, 1.5), FakeWord(" there.", 2.5, 3.0)]),
                FakeSeg(" Find the cheapest nonstop flight to SFO.", 5.0, 7.0, []),   # the context read back
                FakeSeg("  ", 8.0, 8.5, []),
            ]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    return seen


def test_transcription_uses_the_task_as_context_keeps_words_and_drops_an_echo(fake_model):
    prompt = transcribe.context_prompt("Find the cheapest nonstop flight to SFO", "The flight is selected")
    assert prompt == "Find the cheapest nonstop flight to SFO. The flight is selected."
    out = transcribe.transcribe("a.wav", offset_s=0.5, model_size="large-v3-turbo", prompt=prompt)
    assert fake_model["initial_prompt"] == prompt and fake_model["word_timestamps"] is True
    assert fake_model["language"] == "en"
    assert [s["text"] for s in out] == ["One way, I just need to get there."]
    assert out[0]["words"][0] == {"w": " One", "t_start": 1.5, "t_end": 1.7}     # recording time (offset applied)
    assert transcribe.context_prompt("", "") is None


# ---- click targets an app doesn't expose -------------------------------------------------------
def test_apps_that_only_expose_their_window_are_reported():
    meta = {"screen": {"w": 2560, "h": 1440}}
    pane = {"status": "ok", "role": "pane", "name": "", "rect": [0, 0, 2560, 1392]}
    steps = [dict(click(i, i), target=dict(pane), context={"process": "Arc.exe"}) for i in range(4)]
    assert qc.window_sized_targets(meta, steps) == (4, 4, "Arc")
    named = [dict(click(i, i), target={"status": "ok", "role": "button", "name": "Search", "rect": [10, 10, 80, 30]},
                  context={"process": "chrome.exe"}) for i in range(4)]
    assert qc.window_sized_targets(meta, named) is None
    assert qc.window_sized_targets(meta, steps[:2]) is None          # too few clicks to say


def test_short_sentences_about_the_same_step_become_one_narration_entry():
    steps = [click(16, 55.6), click(17, 63.8)]
    ws = words(("This", 58.6, 58.9), ("one", 59.0, 59.1), ("is", 59.1, 59.2), ("basic", 60.4, 60.6), ("economy.", 60.7, 61.0),
               ("No", 61.2, 61.3), ("carry-on.", 61.3, 61.8),                       # before the click at 63.8
               ("That's", 66.0, 66.4), ("the", 66.5, 66.5), ("one.", 66.5, 66.8), ("Non", 67.0, 67.2),
               ("-stop.", 67.2, 67.5), ("Done.", 67.9, 68.2))                        # after it
    segs = align(phrases([seg(ws)], steps), steps)
    assert [(g["text"], g["step_id"], g["timing"]) for g in segs] == [
        ("This one is basic economy. No carry-on.", 17, "before_action"),
        ("That's the one. Non-stop. Done.", 17, "after_action"),
    ]
    assert [n["timing"] for n in steps[1]["narration"]] == ["before_action", "after_action"]
    assert steps[1]["transcript_ids"] == [0, 1] and "phrase" not in segs[0]
    assert steps[1]["reasoning"] == "This one is basic economy. No carry-on. That's the one. Non-stop. Done."
