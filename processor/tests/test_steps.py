"""Segmentation (A1): event-driven grouping, ordering, raw data preserved."""
from thinkaloud.steps import SegmentConfig, merge_events


def keys(t0, text, dt=0.1, **extra):
    return [{"t": round(t0 + i * dt, 3), "type": "key", "key": c, **extra} for i, c in enumerate(text)]


def scroll(t, dy, x=500, y=400, hwnd=1, dx=0.0, **extra):
    return {"t": t, "type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy, "raw": int((dy or dx) * 120),
            "window": {"hwnd": hwnd, "title": "w", "process": "p"}, **extra}


def click(t, x=10, y=10, button="left", hwnd=1, **extra):
    return {"t": t, "type": "click", "x": x, "y": y, "button": button,
            "window": {"hwnd": hwnd, "title": "w", "process": "p"}, **extra}


# ---- scroll ------------------------------------------------------------------
def test_continuous_scroll_longer_than_old_timer_is_one_step():
    ev = [scroll(1.0 + i * 0.4, -1.0) for i in range(12)]          # 4.4 s of scrolling
    ev.insert(6, scroll(3.4, -1.0))                                   # and a 1.6 s-ish gap inside
    steps = merge_events(sorted(ev, key=lambda e: e["t"]))
    assert len(steps) == 1
    a = steps[0]["action"]
    assert len(a["events"]) == 13 and a["runs"] == [
        {"direction": "down", "amount": 13.0, "t_start": 1.0, "t_end": 5.4, "n_events": 13}]


def test_pause_under_threshold_does_not_split_but_long_pause_does():
    cfg = SegmentConfig(scroll_pause_s=5.0)
    ev = [scroll(1.0, -1), scroll(2.8, -1), scroll(7.5, -1), scroll(13.0, -1)]  # gaps 1.8, 4.7, 5.5
    steps = merge_events(ev, config=cfg)
    assert [len(s["action"]["events"]) for s in steps] == [3, 1]
    assert len(merge_events(ev, config=SegmentConfig(scroll_pause_s=10))) == 1  # configurable


def test_scroll_click_scroll_splits_into_three():
    steps = merge_events([scroll(1, -1), scroll(1.1, -1), click(1.5), scroll(1.8, -2)])
    assert [s["action"]["type"] for s in steps] == ["scroll", "click", "scroll"]
    assert steps[2]["action"]["runs"][0]["amount"] == 2.0


def test_direction_change_keeps_both_runs_and_raw_events():
    steps = merge_events([scroll(1, -2), scroll(1.2, -3), scroll(1.5, 1), scroll(1.6, 0.25)])
    a = steps[0]["action"]
    assert [(r["direction"], r["amount"]) for r in a["runs"]] == [("down", 5.0), ("up", 1.25)]
    assert a["net_dy"] == -3.75
    assert [e["dy"] for e in a["events"]] == [-2, -3, 1, 0.25]   # fractional (touchpad) delta survives
    assert "dy" not in a                                          # no misleading bare net amount


def test_window_change_splits_scroll():
    steps = merge_events([scroll(1, -1, hwnd=1), scroll(1.1, -1, hwnd=1), scroll(1.2, -1, hwnd=2)])
    assert [len(s["action"]["events"]) for s in steps] == [2, 1]
    assert steps[1]["context"]["hwnd"] == 2


def test_modifier_change_splits_scroll():
    steps = merge_events([scroll(1, -1), scroll(1.1, 1, mods=["ctrl"])])
    assert len(steps) == 2 and steps[1]["action"]["mods"] == ["ctrl"]


def test_stop_during_scroll_flushes_final_step():
    ev = [{"t": 0, "type": "marker", "name": "start"}, scroll(5, -1), scroll(5.1, -1),
          {"t": 5.15, "type": "marker", "name": "end"}]
    steps = merge_events(ev)
    assert len(steps) == 1 and steps[0]["t_end"] == 5.1


def test_pause_marker_ends_scroll_step():
    ev = [scroll(1, -1), {"t": 1.2, "type": "marker", "name": "pause"},
          {"t": 4, "type": "marker", "name": "resume"}, scroll(4.1, -1)]
    assert len(merge_events(ev)) == 2


def test_horizontal_scroll_direction():
    steps = merge_events([scroll(1, 0.0, dx=1.0), scroll(1.1, 0.0, dx=-2.0)])
    assert [(r["direction"], r["amount"]) for r in steps[0]["action"]["runs"]] == [("right", 1.0), ("left", 2.0)]


# ---- typing / keys --------------------------------------------------------------
def test_typing_burst_is_one_step_and_backspace_edits():
    ev = keys(1.0, "helo") + [{"t": 1.5, "type": "key", "key": "backspace"}] + keys(1.6, "lo")
    steps = merge_events(ev)
    assert len(steps) == 1 and steps[0]["action"]["text"] == "hello"
    assert steps[0]["action"]["backspaces"] == 1


def test_enter_and_shortcuts_are_their_own_steps():
    ev = keys(0, "ab") + [{"t": 0.3, "type": "key", "key": "enter"},
                          {"t": 0.5, "type": "key", "key": "c", "mods": ["ctrl"]},
                          {"t": 0.6, "type": "key", "key": "c", "mods": ["ctrl"]}]
    steps = merge_events(ev)
    assert [s["action"] for s in steps] == [
        {"type": "type", "text": "ab"}, {"type": "key", "key": "enter"},
        {"type": "key", "key": "ctrl+c"}, {"type": "key", "key": "ctrl+c"}]


def test_masked_keys_stay_masked():
    ev = keys(0, "•••", masked=True)
    a = merge_events(ev)[0]["action"]
    assert a["text"] == "•••" and a["masked_chars"] == 3


def test_typing_in_other_window_splits():
    ev = [dict(e, window={"hwnd": 1}) for e in keys(0, "ab")] + [dict(e, window={"hwnd": 2}) for e in keys(0.3, "cd")]
    assert [s["action"]["text"] for s in merge_events(ev)] == ["ab", "cd"]


def test_key_repeat_merges():
    ev = [{"t": 0.1 * i, "type": "key", "key": "down"} for i in range(4)]
    steps = merge_events(ev)
    assert len(steps) == 1 and steps[0]["action"]["repeat"] == 4


# ---- clicks ------------------------------------------------------------------------
def test_double_and_triple_click_merge_but_not_other_button():
    ev = [click(1.0), click(1.2), click(1.35), click(1.5, button="right")]
    steps = merge_events(ev, config=SegmentConfig(double_click_s=0.5))
    assert [(s["action"]["button"], s["action"].get("count", 1)) for s in steps] == [("left", 3), ("right", 1)]


def test_monitor_origin_transform_and_target_kept():
    tgt = {"status": "ok", "role": "button", "name": "Add to Cart", "rect": [2600, 40, 80, 30]}
    steps = merge_events([click(1, x=2660, y=50, target=tgt)], origin=(2560, 0))
    a = steps[0]["action"]
    assert (a["x"], a["y"], a["screen_x"], a["screen_y"]) == (100, 50, 2660, 50)
    assert steps[0]["target"] == tgt


# ---- ordering / ids ---------------------------------------------------------------
def test_order_uses_seq_for_equal_timestamps_and_uids_are_stable():
    ev = [dict(click(1.0), seq=5), dict(scroll(1.0, -1), seq=6), dict(click(2.0, x=300), seq=7)]
    a = merge_events(list(reversed(ev)))
    b = merge_events(ev)
    assert [s["uid"] for s in a] == [s["uid"] for s in b] == ["s000005", "s000006", "s000007"]
    assert [s["action"]["type"] for s in a] == ["click", "scroll", "click"]


def test_legacy_v01_events_without_seq_or_raw():
    ev = [{"t": 0, "type": "marker", "name": "start", "frame": "frames/0.png"},
          {"t": 1, "type": "scroll", "x": 5, "y": 5, "dx": 0, "dy": -1},
          {"t": 1.2, "type": "scroll", "x": 5, "y": 5, "dx": 0, "dy": -1}]
    steps = merge_events(ev)
    assert steps[0]["uid"] == "s000001"
    assert "recorder 0.1" in steps[0]["action"]["unit"]
