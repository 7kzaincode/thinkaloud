import json
import shutil
from pathlib import Path

import pytest

from thinkaloud import qc
from thinkaloud.align import align, assign
from thinkaloud.pipeline import process
from thinkaloud.steps import merge_events

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "synthetic-flight"


def keys(t0, text, dt=0.1):
    return [{"t": round(t0 + i * dt, 3), "type": "key", "key": c} for i, c in enumerate(text)]


# ---- steps ---------------------------------------------------------------
def test_typing_burst_becomes_one_step_with_backspace_applied():
    ev = keys(1.0, "helo") + [{"t": 1.5, "type": "key", "key": "backspace"}] + keys(1.6, "lo")
    steps = merge_events(ev)
    assert len(steps) == 1
    assert steps[0]["action"] == {"type": "type", "text": "hello"}
    assert (steps[0]["t_start"], steps[0]["t_end"]) == (1.0, 1.7)


def test_pause_splits_typing_and_enter_is_its_own_step():
    ev = keys(0, "ab") + keys(5.0, "cd") + [{"t": 5.5, "type": "key", "key": "enter", "frame": "f.png"}]
    steps = merge_events(ev)
    assert [s["action"] for s in steps] == [
        {"type": "type", "text": "ab"}, {"type": "type", "text": "cd"},
        {"type": "key", "key": "enter"}]
    assert steps[2]["screenshot"] == "f.png"


def test_scrolls_merge_and_sum():
    ev = [{"t": t, "type": "scroll", "x": 5, "y": 5, "dx": 0, "dy": d}
          for t, d in [(1, -1), (1.5, -2), (2.2, -1), (10, 3)]]
    steps = merge_events(ev)
    assert [s["action"]["dy"] for s in steps] == [-4, 3]


def test_click_uses_own_frame_and_double_click_merges():
    ev = [{"t": 0, "type": "marker", "name": "start", "frame": "a.png"},
          {"t": 2, "type": "click", "x": 10, "y": 10, "button": "left", "frame": "b.png"},
          {"t": 2.2, "type": "click", "x": 11, "y": 10, "button": "left", "frame": "c.png"}]
    steps = merge_events(ev)
    assert len(steps) == 1
    assert steps[0]["action"]["count"] == 2
    assert steps[0]["screenshot"] == "b.png"


def test_modifier_combo_and_monitor_origin():
    ev = [{"t": 1, "type": "key", "key": "c", "mods": ["ctrl"]},
          {"t": 2, "type": "click", "x": 2660, "y": 50, "button": "left"}]
    steps = merge_events(ev, origin=(2560, 0))
    assert steps[0]["action"] == {"type": "key", "key": "ctrl+c"}
    assert steps[1]["action"]["x"] == 100


# ---- alignment -------------------------------------------------------------
STEPS = [{"id": i, "t_start": t, "t_end": t} for i, t in enumerate([5.0, 6.0, 30.0])]


def test_narration_goes_to_next_step_within_lookahead():
    assert assign({"t_start": 1.0, "t_end": 3.0}, STEPS) == 0      # 5.0 <= 3+4
    assert assign({"t_start": 5.5, "t_end": 6.5}, STEPS) == 1


def test_narration_falls_back_to_previous_step_then_none():
    assert assign({"t_start": 10.0, "t_end": 12.0}, STEPS) == 1    # commentary after the fact
    assert assign({"t_start": 0.0, "t_end": 0.5}, [{"id": 0, "t_start": 9.0}]) is None


def test_carry_forward_stops_at_pause():
    steps = [{"id": i, "t_start": t, "t_end": t} for i, t in enumerate([2.0, 4.0, 20.0])]
    align([{"id": 0, "t_start": 0.0, "t_end": 1.5, "text": "logging in"}], steps)
    assert [s["reasoning_source"] for s in steps] == ["narrated", "carried", None]
    assert steps[1]["carried_from"] == 0
    assert steps[2]["reasoning"] == ""


# ---- qc --------------------------------------------------------------------
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


def test_idle_gap_severity_depends_on_narration():
    steps = [{"id": 0, "t_start": 1, "t_end": 1, "action": {"type": "click"}, "reasoning": "x"},
             {"id": 1, "t_start": 30, "t_end": 30, "action": {"type": "click"}, "reasoning": "y"}]
    qc.check_steps(steps, [])
    assert [f["severity"] for f in steps[1]["flags"] if f["code"] == "idle_gap"] == ["warn"]
    for s in steps:
        s["flags"] = []
    qc.check_steps(steps, [{"t_start": 10, "t_end": 15}])
    assert [f["severity"] for f in steps[1]["flags"] if f["code"] == "idle_gap"] == ["info"]


# ---- end to end on the synthetic sample -------------------------------------
def test_synthetic_session_end_to_end(tmp_path):
    if not SAMPLE.exists():
        pytest.skip("run scripts/make_synthetic.py first")
    s = tmp_path / "sess"
    shutil.copytree(SAMPLE, s)
    traj = process(s, log=lambda *_: None)
    assert traj["qc"]["summary"] == \
        "18 steps, 1 missing reasoning, 1 idle gap, 1 possible secret, 1 typed email"
    typed = [st["action"]["text"] for st in traj["steps"] if st["action"]["type"] == "type"]
    assert typed == ["[REDACTED]", "[REDACTED]", "YYZ", "SFO"]
    raw = (s / "trajectory.json").read_text()
    assert "Tr4vel" not in raw and "zain.demo@" not in raw
    assert all((s / st["screenshot"]).exists() for st in traj["steps"])
    assert json.loads(raw)["final_screenshot"]
