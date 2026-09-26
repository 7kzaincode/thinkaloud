"""Attach transcript segments to steps.

People usually say what they're about to do, then do it. So the rule is:

  1. A segment goes to the FIRST step that starts inside
     [segment.t_start, segment.t_end + LOOKAHEAD].
  2. If no step starts in that window, it goes to the last step that started
     before the segment ended (commentary on something already done).
  3. If neither exists (narration before any action), it stays unassigned.
     Unassigned narration is kept in the transcript and reported by QC.

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

LOOKAHEAD = 4.0
CARRY_GAP = 6.0
TIMING_SLACK = 0.25


def assign(segment: dict, steps: list[dict], lookahead: float = LOOKAHEAD) -> int | None:
    lo, hi = segment["t_start"], segment["t_end"] + lookahead
    for s in steps:
        if lo <= s["t_start"] <= hi:
            return s["id"]
    before = [s for s in steps if s["t_start"] <= segment["t_end"]]
    return before[-1]["id"] if before else None


def timing(segment: dict, step: dict) -> str:
    if segment["t_end"] <= step["t_start"] + TIMING_SLACK:
        return "before_action"
    if segment["t_start"] >= step["t_end"]:
        return "after_action"
    return "during_action"


def align(segments: list[dict], steps: list[dict], lookahead: float = LOOKAHEAD,
          carry_gap: float = CARRY_GAP) -> None:
    """Mutates: segment['step_id']; step['reasoning'], ['transcript_ids'],
    ['reasoning_source'] ("narrated" | "carried" | None), ['carried_from']."""
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        s["reasoning"] = ""
        s["transcript_ids"] = []
        s["reasoning_source"] = None
        s["carried_from"] = None
        s["narration"] = []
    for seg in sorted(segments, key=lambda g: g["t_start"]):
        sid = assign(seg, steps, lookahead)
        seg["step_id"] = sid
        if sid is None:
            continue
        step = by_id[sid]
        step["transcript_ids"].append(seg["id"])
        seg["timing"] = timing(seg, step)
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
