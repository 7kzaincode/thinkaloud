import json
import os
import threading
import time

import pytest

from thinkaloud import qc
from thinkaloud.fsutil import replace_retry, write_json_atomic


@pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
def test_atomic_write_waits_for_a_reader_to_let_go(tmp_path):
    p = tmp_path / "processing.json"
    write_json_atomic(p, {"state": "running"})
    f = open(p, "rb")                                   # another process polling the file
    tmp = tmp_path / "next.tmp"
    tmp.write_text("x")
    with pytest.raises(PermissionError):
        os.replace(tmp, p)                              # the failure the retry exists for
    threading.Timer(0.3, f.close).start()
    t0 = time.monotonic()
    write_json_atomic(p, {"state": "done"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"state": "done"}
    assert time.monotonic() - t0 >= 0.25
    assert [x.name for x in tmp_path.iterdir() if x.name.startswith("processing.json.")] == []
    replace_retry(tmp, tmp_path / "moved")              # unlocked: immediate
    assert (tmp_path / "moved").read_text() == "x"


def test_session_flags_for_incomplete_recordings_and_capture_problems():
    meta = {"_has_end_marker": False, "errors": ["video: encoder failed"],
            "audio": {"overflows": 2, "overflow_padding_s": 0.064}, "input": {"masked_focus_unknown": 5}}
    codes = {f["code"]: f["severity"] for f in qc.check_session(meta, [], [], None)}
    assert codes["recording_incomplete"] == "warn" and codes["recorder_errors"] == "warn"
    assert codes["audio_gaps"] == "info" and codes["masked_unknown_focus"] == "warn"
    clean = qc.check_session({"_has_end_marker": True}, [], [], None)
    assert not {"recording_incomplete", "recorder_errors", "audio_gaps", "masked_unknown_focus"} & {f["code"] for f in clean}
    # 0.1 recordings never had an end marker
    assert "recording_incomplete" not in {f["code"] for f in qc.check_session({"_has_end_marker": False}, [], [], None, legacy=True)}


def test_password_masking_off_is_flagged_for_new_recordings_only():
    for mode in ("off", "unavailable"):
        codes = {f["code"] for f in qc.check_session({"input": {"password_masking": mode}}, [], [], None)}
        assert "password_masking_off" in codes
    assert "password_masking_off" not in {f["code"] for f in qc.check_session({"input": {"password_masking": "on"}}, [], [], None)}
    assert "password_masking_off" not in {f["code"] for f in qc.check_session({}, [], [], None, legacy=True)}


def _t(text, t0, title="Sign in"):
    return {"t_start": t0, "t_end": t0 + 1, "action": {"type": "type", "text": text}, "flags": [], "reasoning": "",
            "observations": {}, "context": {"window_title": title, "hwnd": 7}}


def test_email_typed_in_two_bursts_is_flagged_and_redacted():
    steps = [_t("john.smith", 1.0), _t("@example.com", 5.0)]
    qc.check_steps(steps, [])
    assert all("possible_email" in {f["code"] for f in s["flags"]} for s in steps)
    qc.redact(steps)
    assert "john.smith" not in json.dumps(steps) and "example.com" not in json.dumps(steps)
    other_window = [_t("john.smith", 1.0), _t("@example.com", 5.0, title="Notes")]
    other_window[1]["context"]["hwnd"] = 8
    qc.check_steps(other_window, [])
    assert "possible_email" not in {f["code"] for f in other_window[0]["flags"]}


@pytest.mark.parametrize("url", [
    "https://app.example/cb#access_token=abc123&id_token=eyJx.eyJy.sig",
    "https://api.example/v1/data?apiKey=sk_live_123",
    "https://app.example/x?accessToken=abc",
    "https://app.example/x?sessionid=abc",
    "https://app.example/x?jwt=abc",
    "https://app.example/reset-password/6f1d2c3b4a5e6f7a8b9c",
    "https://app.example/x/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc",
])
def test_sensitive_urls_are_flagged_and_stripped(url):
    clean, found = qc.sanitize_url(url)
    assert found and "abc" not in clean and "sk_live" not in clean and "6f1d2c3b" not in clean and "eyJ" not in clean


@pytest.mark.parametrize("url", ["https://example.com/search?q=flights&sort=price",
                                 "https://example.com/author/jane?page=2", "https://example.com/#/settings"])
def test_ordinary_urls_are_not_flagged(url):
    assert qc.sanitize_url(url) == (url, False)


def test_email_in_narration_is_flagged_and_redacted():
    segs = [{"id": 0, "t_start": 0, "t_end": 1, "text": "I log in as bob@example.com here"}]
    steps = [dict(_t("x", 1.0), reasoning=segs[0]["text"], transcript_ids=[0])]
    qc.check_narration(steps, segs)
    assert "email_in_narration" in {f["code"] for f in steps[0]["flags"]}
    qc.redact_narration(steps, segs)
    assert "bob@" not in segs[0]["text"] and "bob@" not in steps[0]["reasoning"]


def test_privacy_flags_carry_a_subject_hash_of_the_content():
    a, b = [_t("alice@example.com", 1.0)], [_t("bob@example.com", 1.0)]
    qc.check_steps(a, [])
    qc.check_steps(b, [])
    fa = next(f for f in a[0]["flags"] if f["code"] == "possible_email")
    fb = next(f for f in b[0]["flags"] if f["code"] == "possible_email")
    assert fa["subject"] and fa["subject"] != fb["subject"]
    qc.redact(a)
    assert next(f for f in a[0]["flags"] if f["code"] == "redacted_value_on_screen")["subject"] == fa["subject"]
