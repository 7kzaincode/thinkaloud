"""Attach narration to steps.

First the speech-to-text output is cut into phrases (`phrases`): sentences, and also
wherever the speaker paused while an action happened ("one way, I just need to get there"
[click][click] "Pearson specifically..."). Speech-to-text segments are too coarse for this:
a large model returns 10-second runs that cover several actions and end mid-sentence.
Transcripts without word timings (supplied files, older recordings) keep their segments.

People usually say what they're about to do, then do it. So the rule for each phrase is:

  1. A phrase that starts while a longer action is under way (typing, a drag) goes to it.
  2. Otherwise it goes to the FIRST step that starts inside
     [phrase.t_start, phrase.t_end + LOOKAHEAD].
  3. If no step starts in that window, it goes to the last step that started
     before the phrase ended (commentary on something already done).
  4. If none of these exist (narration before any action), it stays unassigned.
     Unassigned narration is kept in the transcript and reported by QC.
  A lone modifier key press (Alt, Ctrl, Shift, Windows) is never a target: it is almost
  always a stray press, not something the narration is about.

A step's reasoning is the text of its segments joined in time order.

Carry-forward: one sentence of intent often covers several actions ("now the
search, I'll use airport codes" -> click, type, click, type). A step with no
narration of its own inherits the previous step's reasoning when it starts
within CARRY_GAP seconds of that step ending. Such steps get
reasoning_source="carried" and carried_from=<step id>; QC only flags steps
that have neither their own nor carried reasoning.

Timing: every narration segment attached to a step records how it relates to the
action in time, so an intention stated beforehand can be told apart from a
comment made afterwards:
    before_action   segment ended by the time the step started (+TIMING_SLACK)
    after_action    segment started after the step ended
    during_action   anything else (overlaps the action)
"""
from __future__ import annotations

LOOKAHEAD = 5.0
CARRY_GAP = 6.0
TIMING_SLACK = 0.25
SENTENCE_END = (".", "?", "!")
ACTION_PAUSE = 0.3   # a pause at least this long with an action inside it ends a phrase
RUNON_PAUSE = 2.5    # in a transcript without punctuation, a pause this long ends a phrase
MERGE_GAP = 1.0      # neighbouring phrases about the same step, said the same way, closer than this join
MODIFIERS = {"alt", "altgr", "ctrl", "control", "shift", "win", "cmd", "super", "meta"}


def is_noise(step: dict) -> bool:
    """A lone modifier key press: never what the narration is about."""
    a = step.get("action") or {}
    return a.get("type") == "key" and str(a.get("key", "")).lower() in MODIFIERS


def phrases(segments: list[dict], steps: list[dict]) -> list[dict]:
    """Cut narration into phrases using per-word timings: after a sentence ends, and at a
    pause during which an action started. Segments without word timings stay whole."""
    if not any(g.get("words") for g in segments):
        return segments
    words = []
    for g in sorted(segments, key=lambda g: g["t_start"]):
        if g.get("words"):
            words += [dict(w) for w in g["words"]]
        else:
            words.append({"w": " " + g["text"], "t_start": g["t_start"], "t_end": g["t_end"]})
    punctuated = any(w["w"].strip().endswith(SENTENCE_END) for w in words)
    actions = sorted(s["t_start"] for s in steps if not is_noise(s))
    units: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            gap = w["t_start"] - prev["t_end"]
            acted = any(prev["t_end"] - 0.05 <= a <= w["t_start"] + 0.05 for a in actions)
            if (prev["w"].strip().endswith(SENTENCE_END) or (gap >= ACTION_PAUSE and acted)
                    or (not punctuated and gap >= RUNON_PAUSE)):
                units.append(cur)
                cur = []
        cur.append(w)
    if cur:
        units.append(cur)
    return [{"id": i, "t_start": u[0]["t_start"], "t_end": u[-1]["t_end"],
             "text": "".join(w["w"] for w in u).strip(), "phrase": True} for i, u in enumerate(units)]


def assign(segment: dict, steps: list[dict], lookahead: float = LOOKAHEAD) -> int | None:
    lo, hi = segment["t_start"], segment["t_end"] + lookahead
    targets = [s for s in steps if not is_noise(s)]
    for s in targets:
        if s["t_start"] < lo < s["t_end"]:   # said while typing or dragging
            return s["id"]
    for s in targets:
        if lo <= s["t_start"] <= hi:
            return s["id"]
    before = [s for s in targets if s["t_start"] <= segment["t_end"]]
    return before[-1]["id"] if before else None


def timing(segment: dict, step: dict) -> str:
    if segment["t_end"] <= step["t_start"] + TIMING_SLACK:
        return "before_action"
    if segment["t_start"] >= step["t_end"]:
        return "after_action"
    return "during_action"


def _merge(segments: list[dict]) -> list[dict]:
    """Join neighbouring phrases that went to the same step and relate to it the same way
    ("That's the one." "Non-stop." "Done." after the last click): one narration entry each.
    Only phrases cut by `phrases` are joined; supplied segments are kept as they are."""
    out: list[dict] = []
    for g in segments:
        last = out[-1] if out else None
        if (last and g.get("phrase") and last.get("phrase") and g["step_id"] is not None
                and g["step_id"] == last["step_id"] and g.get("timing") == last.get("timing")
                and g["t_start"] - last["t_end"] <= MERGE_GAP):
            last.update(t_end=g["t_end"], text=f"{last['text']} {g['text']}")
        else:
            out.append(dict(g))
    for i, g in enumerate(out):
        g["id"] = i
    return out


def align(segments: list[dict], steps: list[dict], lookahead: float = LOOKAHEAD,
          carry_gap: float = CARRY_GAP) -> list[dict]:
    """Returns the narration segments (neighbouring phrases about the same step joined), each
    with 'step_id' and 'timing'. Sets on steps: 'reasoning', 'transcript_ids', 'narration',
    'reasoning_source' ("narrated" | "carried" | None), 'carried_from'."""
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        s["reasoning"] = ""
        s["transcript_ids"] = []
        s["reasoning_source"] = None
        s["carried_from"] = None
        s["narration"] = []
    segments = sorted(segments, key=lambda g: g["t_start"])
    for seg in segments:
        seg["step_id"] = assign(seg, steps, lookahead)
        if seg["step_id"] is not None:
            seg["timing"] = timing(seg, by_id[seg["step_id"]])
    if any(g.get("phrase") for g in segments):
        segments = _merge(segments)
    for seg in segments:
        sid = seg["step_id"]
        if sid is None:
            continue
        step = by_id[sid]
        step["transcript_ids"].append(seg["id"])
        step["narration"].append({"segment_id": seg["id"], "t_start": seg["t_start"],
                                  "t_end": seg["t_end"], "timing": seg["timing"]})
        text = seg["text"].strip()
        step["reasoning"] = f"{step['reasoning']} {text}".strip() if step["reasoning"] else text

    prev = None
    for s in steps:
        if s["reasoning"]:
            s["reasoning_source"] = "narrated"
        elif prev and prev["reasoning"] and s["t_start"] - prev["t_end"] <= carry_gap:
            s["reasoning"] = prev["reasoning"]
            s["reasoning_source"] = "carried"
            s["carried_from"] = prev["carried_from"] if prev["carried_from"] is not None else prev["id"]
        prev = s
    for g in segments:
        g.pop("phrase", None)
    return segments
