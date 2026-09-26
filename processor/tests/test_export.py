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
    assert c == [("left_click", {"coordinate": [50, 25], "text": "ctrl+shift"})]   # pixel 51 lies in scaled pixel 25
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
    assert c == [("type", {"text": "hello"})] and not p      # in-step corrections: final text is faithful
    c, p = calls({"type": "type", "text": "", "backspaces": 2, "keystrokes": "ab⌫⌫"})
    assert c == [] and p[0][0] == "warning"


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
    a["runs"][1]["amount"] = 0.25                                  # below one wheel click: not representable
    c, p = calls(a)
    assert len(c) == 1 and p[0][0] == "error"


def test_drag_and_punctuation_keys():
    c, p = calls({"type": "drag", "button": "left", "x": 10, "y": 20, "x2": 300, "y2": 40, "modifiers": []})
    assert c == [("left_click_drag", {"start_coordinate": [10, 20], "coordinate": [300, 40]})] and not p
    assert calls({"type": "key", "key": "/", "modifiers": ["ctrl"], "repeat": 1})[0] == [("key", {"text": "ctrl+slash"})]
    c, p = calls({"type": "key", "key": "é", "modifiers": ["alt"], "repeat": 1})
    assert c == [] and p[0][0] == "error"                          # never emit something the validator rejects


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
    r = export_bundle([processed], tmp_path / "out", include_media=True, allow_privacy_flags=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    b = Path(r["bundle"])
    # run the copy shipped inside the bundle with a separate interpreter
    out = subprocess.run([sys.executable, str(b / "thinkaloud_dataset.py"), str(b), "--json"], capture_output=True, text=True)
    rep = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and rep["ok"] and rep["recordings"] == 1
    with zipfile.ZipFile(r["zip"]) as z:
        assert any(n.endswith("manifest.json") for n in z.namelist())
    assert ds.load_bundle(r["zip"]).manifest["formats"] == ["dataset", "claude"]


def test_open_privacy_flags_block_export_unless_confirmed(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out")
    assert r["ok"] is False and r["recordings"] == 0
    assert r["skipped"][0]["id"] == "synthetic-flight" and "possible_email" in r["skipped"][0]["reason"]
    assert export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)["recordings"] == 1


def test_dataset_record_content(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
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
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
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
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    assert rec["review"]["outcome"] == "pass" and rec["review"]["outcome_source"] == "human"
    assert rec["review"]["checklist"][0]["human_verdict_source"] == "accepted_ai_suggestion"
    st = rec["steps"][1]
    assert st["reasoning"]["edited_by_reviewer"] and st["reasoning"]["original"]["source"] == "carried"
    assert st["narration"] == []                                                # edit didn't become narration
    ai = next(x for x in rec["steps"] if x["uid"] == "s000055")["ai_assessment"]
    assert ai["suggestion_only"] is True and ai["human_decision"] == "rejected"


def test_checklist_verdict_provenance_and_rewording_are_exported(processed, tmp_path):
    adopted = {"run_id": "ai-2", "verdict": "supported", "input_hash": "x"}
    t = review(processed, outcome="pass",
               checklist=[{"id": "c1", "text": "Nonstop flight on checkout", "origin": "human",
                           "human_verdict": "met", "human_verdict_source": "accepted_ai_suggestion",
                           "accepted_from": adopted, "verdict_text": "Nonstop flight on checkout",
                           "ai_check": {"run_id": "ai-5", "verdict": "contradicted", "evidence": "1 stop", "input_hash": "x"}},
                          {"id": "c2", "text": "Price under $400", "origin": "human",
                           "human_verdict": "met", "human_verdict_source": "reviewer", "verdict_text": "Price under $350",
                           "ai_check": None}],
               rebase_history=[{"at": "t", "from_hash": "old", "dropped": ["step s000001: edits no longer match a step"]}])
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    c1, c2 = rec["review"]["checklist"]
    assert c1["accepted_from"] == adopted and c1["ai_check"]["verdict"] == "contradicted"
    assert c1["verdict_outdated"] is False and c2["verdict_outdated"] is True
    assert c2["verdict_given_for"] == "Price under $350"
    assert rec["review"]["rebase_history"][0]["dropped"]
    assert any("c2 was reworded" in w for w in r["warnings"])
    assert any("could not be carried over" in w for w in r["warnings"])


def test_stale_review_is_refused(processed, tmp_path):
    t = review(processed, outcome="pass")
    t["review"]["base_hash"] = "0000000000000000"
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    assert r["ok"] is False and "older processing run" in r["skipped"][0]["reason"]


def test_validator_catches_tampering_and_ownership_violations(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", zip_bundle=False, allow_privacy_flags=True)
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
    r = export_bundle([s], tmp_path / "out", formats=("dataset",), allow_privacy_flags=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    assert rec["recording"]["legacy"] is True
    assert all(st["observations"]["after"]["status"] == "missing" and st["observations"]["after"]["image"] is None
               for st in rec["steps"])


def test_unprocessed_recording_is_reported(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    r = export_bundle([raw], tmp_path / "out")
    assert r["ok"] is False and "not processed" in r["skipped"][0]["reason"]


def test_reviewer_file_cannot_inject_paths_or_ids(processed, tmp_path):
    """Review B #1: the reviewer's file is browser-written; only review-owned fields are trusted."""
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    t = review(processed, outcome="pass")
    t["session_id"] = "../../../escaped"
    t["steps"][0]["observations"]["after"]["file"] = str(secret)
    t["steps"][1]["observations"]["before"]["file"] = "frames/../../secret.txt"
    t["final_observation"] = {"status": "ok", "file": str(secret)}
    t["steps"][0]["action"] = {"type": "type", "text": "injected"}
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    b = Path(r["bundle"])
    assert r["validation"]["ok"]
    assert not list(tmp_path.glob("escaped*")) and not (tmp_path.parent / "escaped").exists()
    assert all("top secret" not in p.read_text(errors="ignore") for p in b.rglob("*") if p.is_file() and p.suffix != ".png")
    rec = next(ds.load_bundle(r["bundle"]).recordings())
    assert rec["recording"]["id"] == "synthetic-flight"
    assert rec["steps"][0]["action"]["type"] == "click"             # the processor's action, not the injected one
    assert rec["review"]["outcome"] == "pass"                       # review-owned fields are kept


def test_bundle_names_never_collide(processed, tmp_path):
    a = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    b = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    assert a["bundle"] != b["bundle"] and a["ok"] and b["ok"]


def test_validator_rejects_non_png_images_and_zip_copies_are_cleaned(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    with ds.load_bundle(r["zip"]) as b:
        tmp_copy = b.path
        assert tmp_copy.exists()
    assert not tmp_copy.exists()
    bundle = Path(r["bundle"])
    img = next((bundle / "recordings" / "synthetic-flight" / "assets").glob("*.png"))
    img.write_bytes(b"text pretending to be a png")
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for f in man["files"]:
        if f["path"].endswith(img.name) and "recordings/" in f["path"]:
            f["sha256"], f["bytes"] = hashlib.sha256(img.read_bytes()).hexdigest(), img.stat().st_size
    (bundle / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    rep = ds.validate_bundle(bundle)
    assert any("not a complete PNG" in e for e in rep["errors"])


def test_last_pixel_of_a_4k_frame_stays_inside_the_screenshot():
    c, p = claude_calls(step({"type": "click", "button": "left", "count": 1, "x": 3839, "y": 2159, "modifiers": []}),
                        0.5, (3840, 2160))
    assert c == [("left_click", {"coordinate": [1919, 1079]})] and not p


def test_lone_win_and_alt_and_ctrl_plus():
    assert calls({"type": "key", "key": "cmd", "modifiers": [], "repeat": 1})[0] == [("key", {"text": "super"})]
    assert calls({"type": "key", "key": "alt", "modifiers": [], "repeat": 1})[0] == [("key", {"text": "alt"})]
    from thinkaloud.export import split_combo, _action
    assert split_combo("ctrl++") == (["ctrl"], "+") and split_combo("+") == ([], "+")
    assert split_combo("ctrl+shift+t") == (["ctrl", "shift"], "t")
    a = _action({"type": "key", "key": "ctrl++"})
    assert (a["key"], a["modifiers"]) == ("+", ["ctrl"])
    assert calls(a)[0] == [("key", {"text": "ctrl+plus"})]


def test_unrepresentable_drag_is_an_error_not_a_click():
    c, p = calls({"type": "click", "button": "left", "count": 1, "x": 5, "y": 5, "modifiers": [],
                  "release": {"x": 300, "y": 5, "t": 2.0}, "drag_problem": "other input happened during the drag"})
    assert c == [] and p[0][0] == "error" and "drag" in p[0][1]


def test_after_a_pause_the_model_sees_the_screen_it_acted_on(processed, tmp_path):
    t = json.loads((processed / "trajectory.json").read_text(encoding="utf-8"))
    s1, s2 = t["steps"][9], t["steps"][10]                    # two clicks with images
    t.setdefault("timeline", {})["pauses"] = [[s1["t_end"] + 0.01, s2["t_start"] - 0.01]]
    (processed / "trajectory.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out", allow_privacy_flags=True)
    assert r["validation"]["ok"], r["validation"]["errors"]
    b = ds.load_bundle(r["bundle"])
    rec = next(b.recordings())
    claude = json.loads((Path(r["bundle"]) / "claude" / "synthetic-flight.json").read_text(encoding="utf-8"))
    looks = [m for m in claude["messages"] if m["role"] == "assistant"
             and [u["id"] for u in m["content"]] == [f"toolu_synthetic_flight_{s2['uid']}_look"]]
    assert len(looks) == 1
    i = claude["messages"].index(looks[0])
    shown = claude["messages"][i + 1]["content"][0]["content"][0]["source"]["path"]
    before = next(s for s in rec["steps"] if s["uid"] == s2["uid"])["observations"]["before"]["image"]
    assert Path(shown).name == Path(before).name
    assert any("after a pause" in w for w in claude["warnings"])
    b.close()


def test_export_rebuilds_flags_from_the_processor_and_matches_privacy_dismissals_by_content(processed, tmp_path):
    t = review(processed, outcome="pass")
    i = next(k for k, s in enumerate(t["steps"]) if any(f["code"] == "possible_email" for f in s["flags"]))
    s = t["steps"][i]
    s["flags"] = []                                           # a file that just omits the flags ...
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out")
    assert r["ok"] is False and r["skipped"][0]["privacy"]    # ... does not close them
    stale = {"code": "possible_email", "severity": "high", "detail": "x", "source": "qc", "subject": "00000000"}
    s["dismissed_flags"] = [stale, {**stale, "code": "redacted_value_on_screen"}]
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out2")
    assert r["ok"] is False, "a dismissal of different content must not close the flag"
    orig = json.loads((processed / "trajectory.json").read_text(encoding="utf-8"))["steps"][i]["flags"]
    s["dismissed_flags"] = [f for f in orig if f["severity"] == "high"]
    (processed / "trajectory.reviewed.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed], tmp_path / "out3")
    assert r["ok"] and not r["skipped"]


def test_one_broken_recording_is_skipped_not_fatal(processed, tmp_path):
    broken = tmp_path / "broken-rec"
    shutil.copytree(processed, broken)
    t = json.loads((broken / "trajectory.json").read_text(encoding="utf-8"))
    t["screen"] = {"w": 0, "h": 0}
    t.setdefault("coordinate_space", {})["frame_size"] = [0, 0]
    (broken / "trajectory.json").write_text(json.dumps(t), encoding="utf-8")
    r = export_bundle([processed, broken], tmp_path / "out", allow_privacy_flags=True)
    assert r["ok"] and r["recordings"] == 1 and r["validation"]["ok"]
    assert r["skipped"][0]["id"] == "broken-rec" and "screen size" in r["skipped"][0]["reason"]


def _resign(bundle: Path, rel: str):
    man = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for f in man["files"]:
        if f["path"] == rel:
            p = bundle / rel
            f["sha256"], f["bytes"] = hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size
    (bundle / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return man


def test_validator_catches_truncated_images_imageless_captures_and_escaping_paths(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", zip_bundle=False, allow_privacy_flags=True)
    b = Path(r["bundle"])
    rec_rel = "recordings/synthetic-flight/trajectory.json"
    img = next((b / "recordings" / "synthetic-flight" / "assets").glob("*.png"))
    img.write_bytes(img.read_bytes()[:100])                                  # a cut-off PNG
    _resign(b, f"recordings/synthetic-flight/assets/{img.name}")
    assert any("not a complete PNG" in e for e in ds.validate_bundle(b)["errors"])
    rec = json.loads((b / rec_rel).read_text(encoding="utf-8"))
    st = next(s for s in rec["steps"] if s["observations"]["after"]["status"] == "settled")
    st["observations"]["after"]["image"] = None                             # "settled" but no image
    (b / rec_rel).write_text(json.dumps(rec), encoding="utf-8")
    man = _resign(b, rec_rel)
    assert any("captured) but has no image" in e for e in ds.validate_bundle(b)["errors"])
    man["files"].append({"path": "../outside.txt", "bytes": 1, "sha256": "0"})
    (b / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    assert any("outside the bundle" in e for e in ds.validate_bundle(b)["errors"])


def test_validator_cross_checks_claude_coordinates_against_the_dataset(processed, tmp_path):
    r = export_bundle([processed], tmp_path / "out", zip_bundle=False, allow_privacy_flags=True)
    b = Path(r["bundle"])
    rel = "claude/synthetic-flight.json"
    c = json.loads((b / rel).read_text(encoding="utf-8"))
    use = next(u for m in c["messages"] if m["role"] == "assistant" for u in m["content"] if u["name"] == "left_click")
    use["input"]["coordinate"] = [use["input"]["coordinate"][0] + 7, use["input"]["coordinate"][1]]
    (b / rel).write_text(json.dumps(c), encoding="utf-8")
    _resign(b, rel)
    assert any("does not match the dataset action" in e for e in ds.validate_bundle(b)["errors"])
