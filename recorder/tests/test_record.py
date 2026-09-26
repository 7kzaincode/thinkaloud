"""Recorder input handling, driven directly (no real input, no screen capture).

Regressions from the independent reviews: a password typed after Tab was stored in plain
text; Ctrl+Shift+T was recorded as Ctrl+T; AltGr characters broke typed text."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pynput import keyboard, mouse  # noqa: E402

import record  # noqa: E402


class FakeUIA:
    """Answers focus lookups from a script, clicks from a dict."""

    def __init__(self, focus_answers, click_answer=None):
        self.focus_answers = list(focus_answers)
        self.click_answer = click_answer or {"status": "ok", "role": "edit", "name": "User", "is_password": False}
        self.answers = {}
        self.n = 0
        self.available = True

    def submit(self, kind, t, *args, epoch=None):
        self.n += 1
        if kind == "focus":
            ans = self.focus_answers.pop(0) if len(self.focus_answers) > 1 else self.focus_answers[0]
            self.answers[self.n] = dict(ans)
        else:
            self.answers[self.n] = dict(self.click_answer)
        return self.n

    def result(self, rid, timeout_s=1.5):
        return self.answers.pop(rid)


def make(tmp_path, uia):
    r = record.Recorder(tmp_path / "s", "t", "c", audio=False, video=False, use_uia=False)
    r.t0 = time.perf_counter()
    r.uia = uia
    return r


def finish(r):
    r.inbox.put(None)
    r.writer_loop()
    return [json.loads(l) for l in (r.out / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def type_str(r, text):
    for ch in text:
        r.on_press(keyboard.KeyCode.from_char(ch))


OK_USER = {"status": "ok", "role": "edit", "name": "User", "is_password": False}
OK_PW = {"status": "ok", "role": "edit", "name": "Password", "is_password": True}


def test_password_after_tab_is_masked(tmp_path):
    r = make(tmp_path, FakeUIA([OK_USER, OK_PW]))
    r.on_click(10, 10, mouse.Button.left, True)
    type_str(r, "alice")
    r.on_press(keyboard.Key.tab)
    type_str(r, "hunter2")
    ev = finish(r)
    keys = "".join(e["key"] if len(e["key"]) == 1 else "|" for e in ev if e["type"] == "key")
    assert keys == "alice|" + "•" * 7
    assert "hunter2" not in (r.out / "events.jsonl").read_text(encoding="utf-8")


def test_unknown_focus_fails_closed(tmp_path):
    r = make(tmp_path, FakeUIA([{"status": "timeout"}]))
    type_str(r, "secret")
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert all(e["key"] == "•" and e["mask_reason"] == "focused field could not be checked" for e in ev)
    assert r.n_mask_unknown == 6


def test_no_uia_means_no_masking(tmp_path):
    r = make(tmp_path, None)
    type_str(r, "hello")
    assert "".join(e["key"] for e in finish(r) if e["type"] == "key") == "hello"


def test_ctrl_shift_letter_keeps_shift_and_altgr_is_text(tmp_path):
    r = make(tmp_path, None)
    r.on_press(keyboard.Key.ctrl_l)
    r.on_press(keyboard.Key.shift)
    r.on_press(keyboard.KeyCode(vk=0x54, char="\x14"))          # Ctrl+Shift+T
    r.on_release(keyboard.Key.shift)
    r.on_release(keyboard.Key.ctrl_l)
    r.on_press(keyboard.Key.ctrl_l)
    r.on_press(keyboard.Key.alt_l)
    r.on_press(keyboard.KeyCode(vk=0x51, char="@"))             # AltGr+Q on a German layout
    r.on_release(keyboard.Key.alt_l)
    r.on_release(keyboard.Key.ctrl_l)
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert (ev[0]["key"], ev[0]["mods"]) == ("t", ["ctrl", "shift"])
    assert ev[1]["key"] == "@" and "mods" not in ev[1] and ev[1]["altgr"]


def test_lone_win_key_is_recorded(tmp_path):
    r = make(tmp_path, None)
    r.on_press(keyboard.Key.cmd)
    r.on_release(keyboard.Key.cmd)
    r.on_press(keyboard.Key.cmd)                                  # Win+E is a shortcut, not a lone Win
    r.on_press(keyboard.KeyCode(vk=0x45, char="e"))
    r.on_release(keyboard.Key.cmd)
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert [(e["key"], e.get("mods")) for e in ev] == [("cmd", None), ("e", ["cmd"])]


def test_drag_release_recorded_only_when_moved(tmp_path):
    r = make(tmp_path, None)
    r.on_click(10, 10, mouse.Button.left, True)
    r.on_click(12, 11, mouse.Button.left, False)                 # a click
    r.on_click(10, 10, mouse.Button.left, True)
    r.on_click(200, 40, mouse.Button.left, False)                # a drag
    ev = finish(r)
    assert [e["type"] for e in ev] == ["click", "click", "release"]
    assert (ev[2]["x"], ev[2]["y"]) == (200, 40)


def test_window_captured_at_input_time(tmp_path, monkeypatch):
    r = make(tmp_path, None)
    monkeypatch.setattr(record.winctx, "foreground_hwnd", lambda: 111)
    monkeypatch.setattr(record.winctx, "describe_hwnd", lambda h: {"hwnd": h, "title": "A", "process": "a.exe"})
    r.on_press(keyboard.KeyCode.from_char("x"))
    monkeypatch.setattr(record.winctx, "foreground_hwnd", lambda: 222)   # focus moves before the writer runs
    ev = finish(r)
    assert ev[0]["window"]["hwnd"] == 111


def test_transient_focus_failure_is_retried_not_masked(tmp_path):
    r = make(tmp_path, FakeUIA([{"status": "skipped_stale"}, OK_USER]))
    type_str(r, "noise")
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert "".join(e["key"] for e in ev) == "noise" and r.n_mask_unknown == 0


def test_focus_that_moved_before_the_lookup_stays_masked(tmp_path):
    r = make(tmp_path, FakeUIA([{"status": "focus_moved"}]))
    type_str(r, "pw")
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert [e["key"] for e in ev] == ["•", "•"]


def test_uia_unavailable_behaves_like_no_uia_and_is_reported(tmp_path):
    r = make(tmp_path, FakeUIA([{"status": "unavailable", "error": "COM failed"}]))
    type_str(r, "hello")
    ev = [e for e in finish(r) if e["type"] == "key"]
    assert "".join(e["key"] for e in ev) == "hello" and r.masking_unavailable


def test_focus_epoch_moves_with_clicks_and_non_text_keys(tmp_path):
    r = make(tmp_path, None)
    e0 = r.focus_epoch
    type_str(r, "ab")
    assert r.focus_epoch == e0
    r.on_press(keyboard.Key.tab)
    assert r.focus_epoch == e0 + 1
    r.on_click(5, 5, mouse.Button.left, True)
    assert r.focus_epoch == e0 + 2


def test_shift_wheel_keeps_shift(tmp_path):
    from types import SimpleNamespace
    r = make(tmp_path, None)
    r.on_press(keyboard.Key.shift)
    r.mouse_filter(record.WM_MOUSEWHEEL, SimpleNamespace(pt=SimpleNamespace(x=5, y=5), mouseData=(-120 & 0xFFFF) << 16, flags=0))
    ev = finish(r)
    assert ev[0]["type"] == "scroll" and ev[0]["mods"] == ["shift"] and ev[0]["dy"] == -1.0


def test_lone_win_survives_auto_repeat_but_not_a_scroll(tmp_path):
    from types import SimpleNamespace
    r = make(tmp_path, None)
    for _ in range(4):
        r.on_press(keyboard.Key.cmd)                               # held long enough to auto-repeat
    r.on_release(keyboard.Key.cmd)
    r.on_press(keyboard.Key.cmd)
    r.mouse_filter(record.WM_MOUSEWHEEL, SimpleNamespace(pt=SimpleNamespace(x=5, y=5), mouseData=120 << 16, flags=0))
    r.on_release(keyboard.Key.cmd)                                 # Win+wheel: not a lone Win
    ev = finish(r)
    assert [(e["type"], e.get("key")) for e in ev] == [("key", "cmd"), ("scroll", None)]


def test_audio_overflow_is_padded_by_the_time_actually_lost(tmp_path):
    import numpy as np
    a = record.AudioCapture(tmp_path, lambda: 0.0, None, lambda: False)
    a.latency = 0.12                                                # must not shrink the padding
    chunk = np.zeros((1600, 1), np.int16)                           # 0.1 s
    a.q.put((chunk, 0.10, False, False))
    a.q.put((chunk, 0.25, True, False))                             # 50 ms lost before this chunk
    a.q.put(None)
    a._write()
    assert 0.04 <= a.padded_s <= 0.06
