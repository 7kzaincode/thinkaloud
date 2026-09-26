"""Exports (B1): mapping fidelity, provenance, validation, asset integrity."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from thinkaloud import dataset as ds
from thinkaloud.export import claude_calls, export_bundle, trajectory_hash
from thinkaloud.pipeline import process

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "synthetic-flight"
LEGACY = Path(__file__).resolve().parent / "fixtures" / "legacy-v01"
FRAME = (1600, 900)


# ---- action mapping (computer_toolset_20260801) ------------------------------------------
def step(action):
    return {"uid": "s1", "action": action}


def calls(action, scale=1.0):
    c, p = claude_calls(step(action), scale, FRAME)
    return c, [(lvl, m) for lvl, m in p]


@pytest.mark.parametrize("button,count,name", [
    ("left", 1, "left_click"), ("left", 2, "double_click"), ("left", 3, "triple_click"),
    ("right", 1, "right_click"), ("middle", 1, "middle_click")])
def test_click_variants(button, count, name):
    c, p = calls({"type": "click", "button": button, "count": count, "x": 100, "y": 50, "modifiers": []})
    assert c == [(name, {"coordinate": [100, 50]})] and not p


def test_click_modifiers_scale_and_unsupported():
    c, _ = calls({"type": "click", "button": "left", "count": 1, "x": 101, "y": 51, "modifiers": ["ctrl", "shift"]}, 0.5)
    assert c == [("left_click", {"coordinate": [50, 26], "text": "ctrl+shift"})]
    c, p = calls({"type": "click", "button": "right", "count": 2, "x": 1, "y": 1, "modifiers": []})
    assert c == [] and p[0][0] == "error"
    c, p = calls({"type": "click", "button": "left", "count": 1, "x": 1700, "y": 10, "modifiers": []})
    assert p and p[0][0] == "error" and "outside" in p[0][1]


def test_type_text_redacted_masked_backspace():
    assert calls({"type": "type", "text": "YYZ"})[0] == [("type", {"text": "YYZ"})]
    c, p = calls({"type": "type", "text": "[REDACTED]", "redacted": True})
    assert c == [] and p[0][0] == "error"
    c, p = calls({"type": "type", "text": "••", "masked_chars": 2})
    assert c == [] and "masked" in p[0][1]
    c, p = calls({"type": "type", "text": "hello", "backspaces": 1})
    assert c == [("type", {"text": "hello"})] and p[0][0] == "warning"


def test_keys_map_to_keysyms_with_repeat():
    assert calls({"type": "key", "key": "enter", "modifiers": [], "repeat": 1})[0] == [("key", {"text": "Return"})]
    assert calls({"type": "key", "key": "c", "modifiers": ["ctrl"], "repeat": 1})[0] == [("key", {"text": "ctrl+c"})]
    assert calls({"type": "key", "key": "tab", "modifiers": ["shift"], "repeat": 1})[0] == [("key", {"text": "shift+Tab"})]
    assert calls({"type": "key", "key": "page_down", "modifiers": [], "repeat": 3})[0] == [("key", {"text": "Page_Down", "repeat": 3})]
    c, _ = calls({"type": "key", "key": "down", "modifiers": [], "repeat": 130})
    assert c == [("key", {"text": "Down", "repeat": 100}), ("key", {"text": "Down", "repeat": 30})]
    c, p = calls({"type": "key", "key": "vk173", "modifiers": [], "repeat": 1})
    assert c == [] and p[0][0] == "error"


def test_scroll_runs_keep_order_direction_and_warn_on_rounding():
    a = {"type": "scroll", "x": 800, "y": 600, "modifiers": [],
         "runs": [{"direction": "down", "amount": 5.0, "t_start": 1.0}, {"direction": "up", "amount": 0.5, "t_start": 2.0}],
         "events": [{"t": 1.0, "x": 800, "y": 600}, {"t": 2.0, "x": 810, "y": 610}]}
    c, p = calls(a)
    assert c == [("scroll", {"scroll_direction": "down", "scroll_amount": 5, "coordinate": [800, 600]}),
                 ("scroll", {"scroll_direction": "up", "scroll_amount": 1, "coordinate": [810, 610]})]
    assert [lvl for lvl, _ in p] == ["warning"] and "rounded" in p[0][1]


# ---- bundles -----------------------------------------------------------------------------------
@pytest.fixture
def processed(tmp_path):
    if not (SAMPLE / "meta.json").exists():
        pytest.skip("sample missing")
    s = tmp_path / "synthetic-flight"
    shutil.copytree(SAMPLE, s, ignore=shutil.ignore_patterns("trajectory*.json", "processing.*"))
    process(s, log=lambda *_: None)
    return s


def review(session, **edits):
    t = json.loads((session / "trajectory.json").read_text(encoding="utf-8"))
    t["review"].update(base_hash=trajectory_hash(session / "trajectory.json"), **edits)
    return t


def test_export_validates_and_the_bundled_validator_agrees(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", include_media=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    b = Path(r["bundle"])
    # run the copy shipped inside the bundle with a separate interpreter
    out = subprocess.run([sys.executable, str(b / "thinkaloud_dataset.py"), str(b), "--json"], capture_output=True, text=True)
    rep = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and rep["ok"] and rep["recordings"] == 1
    with zipfile.ZipFile(r["zip"]) as z:
        assert any(n.endswith("manifest.json") for n in z.namelist())
    assert ds.load_bundle(r["zip"]).manifest["formats"] == ["dataset", "claude"]


def test_dataset_record_content(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out")
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    by = {s["uid"]: s for s in rec["steps"]}
    first = by["s000001"]
    assert first["target"]["name"] == "Email" and first["description"] == "Clicked the Email field"
    assert first["narration"][0]["timing"] == "before_action"
    assert first["narration"][0]["source"].startswith("human narration")
    carried = by["s000002"]
    assert carried["narration"] == [] and carried["reasoning"]["source"] == "carried"
    assert carried["reasoning"]["carried_from_uid"] == "s000001"
    silent = next(s for s in rec["steps"] if s["reasoning"]["source"] is None)
    assert silent["narration"] == [] and silent["reasoning"]["text"] == ""       # missing stays missing
    assert rec["review"]["outcome"] is None and rec["review"]["outcome_source"] is None
    scroll = next(s for s in rec["steps"] if s["action"]["type"] == "scroll")
    assert [r["direction"] for r in scroll["action"]["runs"]] == ["down", "up"] and len(scroll["action"]["events"]) == 3


def test_claude_export_structure(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out")
    b = ds.load_bundle(r["bundle"])
    c = b.claude("synthetic-flight")
    assert c["tool"] == {"type": "computer_toolset_20260801"}
    assert c["valid_for_training"] is False and len(c["errors"]) == 2          # redacted email, masked password
    uses = [u for m in c["messages"] if m["role"] == "assistant" for u in m["content"]]
    assert {u["toolset_name"] for u in uses} == {"computer"}
    assert "scroll" in {u["name"] for u in uses} and "key" in {u["name"] for u in uses}
    # narration never becomes part of Claude's turn
    blob = json.dumps(c["messages"])
    assert "Toronto has two airports" not in blob and "Toronto has two airports" in json.dumps(c["annotations"])
    msgs = b.claude_messages("synthetic-flight")
    img = next(x for m in msgs for x in m["content"] if x.get("type") == "image")
    assert img["source"]["type"] == "base64" and len(img["source"]["data"]) > 1000


def test_human_decisions_and_ai_suggestions_keep_provenance(processed, tmp_path):
    t = review(processed, outcome="pass", notes="looks right",
               checklist=[{"id": "c1", "text": "Nonstop flight on checkout", "origin": "ai", "ai_run_id": "ai-1",
                           "human_verdict": "met", "human_verdict_source": "accepted_ai_suggestion",
                           "ai_check": {"run_id": "ai-2", "verdict": "supported", "evidence": "AC 759", "input_hash": "x"}}],
               ai={"runs": [{"id": "ai-1", "kind": "checklist", "at": "t", "model": "m", "status": "ok"}],
                   "step_assessments": [{"uid": "s000055", "run_id": "ai-3", "verdict": "partially_explains",
                                         "explanation": "retrospective", "input_hash": "h", "decision": "rejected"}],
                   "checklist_drafts": []})
    s = t["steps"][1]
    s["reasoning_original"] = {"text": s["reasoning"], "source": s["reasoning_source"], "carried_from": s["carried_from"]}
    s["reasoning"], s["reasoning_source"], s["carried_from"] = "Typing my login email", "reviewer", None
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out")
    assert r["validation"]["ok"], r["validation"]["errors"]
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    assert rec["review"]["outcome"] == "pass" and rec["review"]["outcome_source"] == "human"
    assert rec["review"]["checklist"][0]["human_verdict_source"] == "accepted_ai_suggestion"
    st = rec["steps"][1]
    assert st["reasoning"]["edited_by_reviewer"] and st["reasoning"]["original"]["source"] == "carried"
    assert st["narration"] == []                                                # edit didn't become narration
    ai = next(x for x in rec["steps"] if x["uid"] == "s000055")["ai_assessment"]
    assert ai["suggestion_only"] is True and ai["human_decision"] == "rejected"


def test_stale_review_is_refused(processed, tmp_path):
    t = review(processed, outcome="pass")
    t["review"]["base_hash"] = "0000000000000000"
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    with pytest.raises(ds.ExportError):
        export_bundle([processed], tmp_path / "out")


def test_validator_catches_tampering_and_ownership_violations(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", zip_bundle=False)
    b = Path(r["bundle"])
    img = next((b / "recordings" / "synthetic-flight" / "assets").glob("*.png"))
    img.write_bytes(img.read_bytes() + b"x")
    rep = ds.validate_bundle(b)
    assert not rep["ok"] and any("checksum" in e for e in rep["errors"])
    # restore checksum, then break screenshot ownership in the record and re-sign it
    img.write_bytes(img.read_bytes()[:-1])
    recp = b / "recordings" / "synthetic-flight" / "trajectory.json"
    rec = json.loads(recp.read_text(encoding="utf-8"))
    st = next(s for s in rec["steps"] if s["observations"]["before"]["image"])
    st["observations"]["before"]["t_capture_end"] = st["t_start"] + 0.5
    recp.write_text(json.dumps(rec), encoding="utf-8")
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    for f in man["files"]:
        if f["path"].endswith("synthetic-flight/trajectory.json"):
            f["sha256"] = hashlib.sha256(recp.read_bytes()).hexdigest()
            f["bytes"] = recp.stat().st_size
    (b / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    rep = ds.validate_bundle(b)
    assert not rep["ok"] and any("is not before the action" in e for e in rep["errors"])


def test_legacy_recording_exports_with_explicit_statuses(tmp_path):
    s = tmp_path / "legacy"
    shutil.copytree(LEGACY, s)
    process(s, log=lambda *_: None)
    r = export_bundle([s], tmp_path / "out", formats=("dataset",))
    assert r["validation"]["ok"], r["validation"]["errors"]
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    assert rec["recording"]["legacy"] is True
    assert all(st["observations"]["after"]["status"] == "missing" and st["observations"]["after"]["image"] is None
               for st in rec["steps"])


def test_unprocessed_recording_is_reported(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(ds.ExportError, match="not processed"):
        export_bundle([raw], tmp_path / "out")
