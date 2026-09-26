"""Screenshot ownership (A2): a before image never shows its own action; an after image
is captured after the action and before the next one; nothing is fabricated."""
from thinkaloud.observations import Capture, assign, legacy_assign


def still(seq, t0, kind="before"):
    return Capture(seq, t0, t0 + 0.03, f"frames/f{seq:06d}.png", {kind}, "still")


def vid(seq, t0):
    return Capture(seq, t0, t0 + 0.03, None, {"video"}, "video", int((t0 + 0.015) * 1000))


def step(i, t0, t1=None):
    return {"id": i, "t_start": t0, "t_end": t0 if t1 is None else t1, "action": {"type": "click"}}


def test_before_finishes_before_action_and_after_is_in_window():
    steps = [step(0, 1.0), step(1, 3.0)]
    stills = [still(0, 0.8), still(1, 1.0), still(2, 1.7, "settled"), still(3, 2.9)]
    assign(steps, stills, [], duration=5.0)
    b0, a0 = steps[0]["observations"]["before"], steps[0]["observations"]["after"]
    assert b0["capture_seq"] == 0 and b0["t_capture_end"] <= 1.0 and b0["status"] == "ok"
    # frame 1 started exactly at the click and finished after it: not a before, and is an after
    assert a0["capture_seq"] == 3 and a0["status"] == "settled"      # latest in (1.0, 3.0)
    assert a0["t_capture_start"] >= 1.0 and a0["t_capture_end"] <= 3.0


def test_after_never_borrows_from_the_next_action():
    steps = [step(0, 1.0), step(1, 1.1)]
    stills = [still(0, 0.5), still(1, 1.2)]         # only capture after 1.0 is after the next action
    assign(steps, stills, [], duration=2.0)
    a0 = steps[0]["observations"]["after"]
    assert a0["status"] == "missing" and a0["file"] is None
    assert "next action began" in a0["reason"]
    assert steps[1]["observations"]["after"]["capture_seq"] == 1


def test_unsettled_when_captured_soon_after_action():
    steps = [step(0, 1.0), step(1, 1.5)]
    assign(steps, [still(0, 0.7), still(1, 1.2)], [], duration=2.0, settle_s=0.6)
    assert steps[0]["observations"]["after"]["status"] == "unsettled"


def test_before_that_predates_previous_action_is_labelled():
    steps = [step(0, 1.0, 1.4), step(1, 1.45)]   # typing ends 1.4, next action 50 ms later
    assign(steps, [still(0, 0.9), still(1, 1.3)], [], duration=2.0)
    b1 = steps[1]["observations"]["before"]
    assert b1["capture_seq"] == 1 and b1["status"] == "predates_previous_action"


def test_stale_and_missing_before():
    steps = [step(0, 0.01), step(1, 5.0)]
    assign(steps, [still(0, 2.0)], [], duration=6.0, max_age_s=1.0)
    assert steps[0]["observations"]["before"]["status"] == "missing"
    assert steps[1]["observations"]["before"]["status"] == "stale"


def test_final_step_after_uses_end_capture_grabbed_after_stop():
    steps = [step(0, 1.0)]
    assign(steps, [still(0, 0.5), still(9, 4.02, "end")], [], duration=4.0)
    a = steps[0]["observations"]["after"]
    assert a["capture_seq"] == 9 and a["status"] == "settled"


def test_pause_bounds_the_after_window():
    steps = [step(0, 1.0), step(1, 10.0)]
    stills = [still(0, 0.5), still(1, 1.8, "settled"), still(2, 9.8)]  # 9.8 is after resume
    assign(steps, stills, [], duration=11.0, pauses=[[2.0, 9.5]])
    assert steps[0]["observations"]["after"]["capture_seq"] == 1


def test_video_frame_fills_a_gap_and_is_reported_for_extraction():
    steps = [step(0, 1.0), step(1, 2.0)]
    needed = assign(steps, [still(0, 0.5)], [vid(0, 0.5), vid(5, 1.5)], duration=3.0)
    a0 = steps[0]["observations"]["after"]
    assert a0["source"] == "video" and a0["capture_seq"] == 5
    assert [c.seq for c in needed] == [5]


def test_legacy_assignment_is_explicit():
    steps = [{"id": 0, "t_start": 1.0, "t_end": 1.0, "action": {"type": "click"}},
             {"id": 1, "t_start": 1.5, "t_end": 2.0, "action": {"type": "type"}}]
    events = [{"t": 1.0, "type": "click", "frame": "frames/000001000.png"}]
    legacy_assign(steps, events)
    assert steps[0]["observations"]["before"]["status"] == "at_action"
    assert steps[1]["observations"]["before"]["status"] == "predates_previous_action"
    assert all(s["observations"]["after"]["status"] == "missing" for s in steps)
