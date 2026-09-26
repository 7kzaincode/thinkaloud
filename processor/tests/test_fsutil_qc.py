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
    assert codes["audio_gaps"] == "info" and codes["masked_unknown_focus"] == "info"
    clean = qc.check_session({"_has_end_marker": True}, [], [], None)
    assert not {"recording_incomplete", "recorder_errors", "audio_gaps", "masked_unknown_focus"} & {f["code"] for f in clean}
    # 0.1 recordings never had an end marker
    assert "recording_incomplete" not in {f["code"] for f in qc.check_session({"_has_end_marker": False}, [], [], None, legacy=True)}
