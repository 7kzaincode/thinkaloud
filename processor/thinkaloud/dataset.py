"""Load and validate thinkaloud export bundles. Standard library only.

This file is copied into every bundle as thinkaloud_dataset.py, so a bundle can be
checked and used without installing thinkaloud:

    python thinkaloud_dataset.py path/to/bundle            # or the .zip
    python thinkaloud_dataset.py path/to/bundle --json     # machine-readable report

    import thinkaloud_dataset as td
    bundle = td.load_bundle("path/to/bundle")               # validates, raises on errors
    for rec in bundle.recordings():                          # thinkaloud.dataset/1.0 dicts
        ...
    msgs = bundle.claude_messages("2026-09-26T10-00-00")    # API-ready base64 images

What is checked: manifest file list and SHA-256 of every file; dataset schema,
step order and timestamps, that every "before" image was captured before its action
and every "after" image after it (and before the next action), coordinates inside the
frame, image references; for the Claude format: message alternation, member tool names
and inputs (computer_toolset_20260801), coordinates inside the screenshot, one
tool_result per tool_use with toolset_name "computer", image references.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path


class ExportError(Exception):
    pass


class ValidationError(Exception):
    def __init__(self, report: dict):
        super().__init__(f"{len(report['errors'])} validation error(s): " + "; ".join(report["errors"][:5]))
        self.report = report


DATASET_FORMAT = "thinkaloud.dataset/1.0"
CLAUDE_FORMAT = "thinkaloud.claude_computer_use/1.0"
CLAUDE_TOOL_TYPE = "computer_toolset_20260801"
ACTIONS = {"click", "type", "key", "scroll"}
BEFORE_STATUS = {"ok", "predates_previous_action", "stale", "missing", "at_action", "legacy_earlier_action"}
AFTER_STATUS = {"settled", "unsettled", "missing"}
TIMING = {"before_action", "during_action", "after_action", None}
REASONING_SRC = {"narrated", "carried", "reviewer", None}
DIRECTIONS = {"up", "down", "left", "right", "none"}
MEMBERS = {"screenshot", "zoom", "left_click", "right_click", "middle_click", "double_click", "triple_click",
           "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up", "cursor_position", "scroll",
           "type", "key", "hold_key", "wait"}
CLICKS = {"left_click", "right_click", "middle_click", "double_click", "triple_click"}
MODS = {"shift", "ctrl", "alt", "super"}
KEY_RE = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$|^[^\s+]$")
TOOL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
EPS = 1e-6

BUNDLE_README = """# thinkaloud export bundle

Human-demonstrated computer-use trajectories with the expert's narration, recorded with
thinkaloud and reviewed by a person.

* `manifest.json`: formats, recordings (with the human outcome), and every file with its SHA-256.
* `recordings/<id>/trajectory.json`: `thinkaloud.dataset/1.0`, vendor-neutral. Each step has the
  action (frame pixel coordinates), a before and an after screen with capture timestamps and a
  status, the UI element that was clicked (when UI Automation could tell), the human narration
  verbatim with its timing relative to the action, the current reasoning and whether a reviewer
  edited it, QC flags, and any AI suggestion together with the human decision on it.
* `claude/<id>.json` (if exported): the same recording as a conversation using Anthropic's
  computer-use toolset (`computer_toolset_20260801`): each step is an assistant turn with the
  action tool call(s) plus a `screenshot` call, answered with the after-state image. Coordinates
  are in the scaled screenshot space given in `screenshot`. Narration and review data are in
  `annotations`, not in the conversation. Image blocks use `{"type": "thinkaloud_asset"}`
  sources; `thinkaloud_dataset.py` converts them to base64 (`Bundle.claude_messages`).
  `valid_for_training` is false when an action could not be represented faithfully
  (e.g. a masked password); the reasons are in `errors`.

Validate:  `python thinkaloud_dataset.py .`

Only recordings whose `review.outcome` is `"pass"` were confirmed by a human as completing
the task. Missing screens are marked missing; nothing was filled in.
"""


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------------------------------------------------------------- dataset record
def validate_record(rec: dict, base: Path, listed: set[str], rec_prefix: str) -> tuple[list[str], list[str]]:
    E: list[str] = []
    W: list[str] = []
    rid = (rec.get("recording") or {}).get("id", "?")
    err = lambda m: E.append(f"{rid}: {m}")
    warn = lambda m: W.append(f"{rid}: {m}")
    if rec.get("format") != DATASET_FORMAT:
        err(f"format is {rec.get('format')!r}, expected {DATASET_FORMAT}")
        return E, W
    r = rec.get("recording") or {}
    if not r.get("id") or not isinstance(r.get("task"), str):
        err("recording.id and recording.task are required")
    cs = rec.get("coordinate_space") or {}
    fw, fh = cs.get("frame_width"), cs.get("frame_height")
    if not (isinstance(fw, int) and isinstance(fh, int) and fw > 0 and fh > 0):
        err("coordinate_space.frame_width/height must be positive integers")
        fw = fh = None

    def image_ok(img, where):
        if img is None:
            return
        if not isinstance(img, str):
            err(f"{where}: image must be a path")
            return
        rel = f"{rec_prefix}/{img}"
        if rel not in listed:
            err(f"{where}: image {img} is not in the manifest")
        elif not (base / rel).exists():
            err(f"{where}: image {img} is missing")

    steps = rec.get("steps")
    if not isinstance(steps, list):
        err("steps must be a list")
        return E, W
    uids = set()
    prev_start = -1e18
    for i, s in enumerate(steps):
        w = f"step {i}"
        if s.get("index") != i:
            err(f"{w}: index {s.get('index')} out of order")
        uid = s.get("uid")
        if not uid or uid in uids:
            err(f"{w}: uid missing or duplicated ({uid})")
        uids.add(uid)
        t0, t1 = s.get("t_start"), s.get("t_end")
        if not (_num(t0) and _num(t1) and t1 >= t0 - EPS):
            err(f"{w}: t_start/t_end invalid ({t0}, {t1})")
            continue
        if t0 < prev_start - EPS:
            err(f"{w}: steps are not in time order")
        prev_start = t0
        a = s.get("action") or {}
        at = a.get("type")
        if at not in ACTIONS:
            err(f"{w}: unknown action type {at!r}")
        elif at in ("click", "scroll"):
            if not (_num(a.get("x")) and _num(a.get("y"))):
                err(f"{w}: {at} needs x and y")
            elif fw and not (0 <= a["x"] < fw and 0 <= a["y"] < fh):
                warn(f"{w}: {at} at ({a['x']}, {a['y']}) is outside the {fw}x{fh} frame (another monitor?)")
            if at == "scroll":
                for run in a.get("runs") or []:
                    if run.get("direction") not in DIRECTIONS or not _num(run.get("amount")):
                        err(f"{w}: invalid scroll run {run}")
                if not a.get("runs"):
                    err(f"{w}: scroll without runs")
            if at == "click" and a.get("button") not in ("left", "right", "middle", "x1", "x2"):
                err(f"{w}: unknown mouse button {a.get('button')!r}")
        elif at == "type" and not isinstance(a.get("text"), str):
            err(f"{w}: type needs text")
        elif at == "key" and not a.get("key"):
            err(f"{w}: key needs a key name")
        obs = s.get("observations") or {}
        b, af = obs.get("before") or {}, obs.get("after") or {}
        if b.get("status") not in BEFORE_STATUS:
            err(f"{w}: before.status {b.get('status')!r} invalid")
        if af.get("status") not in AFTER_STATUS:
            err(f"{w}: after.status {af.get('status')!r} invalid")
        if b.get("status") == "missing" and b.get("image"):
            err(f"{w}: before is 'missing' but has an image")
        if af.get("status") == "missing" and af.get("image"):
            err(f"{w}: after is 'missing' but has an image")
        image_ok(b.get("image"), f"{w} before")
        image_ok(af.get("image"), f"{w} after")
        if b.get("image") and b.get("status") in ("ok", "predates_previous_action", "stale"):
            if not _num(b.get("t_capture_end")) or b["t_capture_end"] > t0 + EPS:
                err(f"{w}: before image captured at {b.get('t_capture_end')} is not before the action at {t0}")
        if af.get("image"):
            if not _num(af.get("t_capture_start")) or af["t_capture_start"] < t1 - EPS:
                err(f"{w}: after image captured at {af.get('t_capture_start')} is not after the action ended at {t1}")
            if i + 1 < len(steps) and _num(af.get("t_capture_end")) and af["t_capture_end"] > steps[i + 1].get("t_start", 1e18) + EPS:
                err(f"{w}: after image captured after the next action began")
        for n in s.get("narration") or []:
            if not isinstance(n.get("text"), str) or n.get("timing") not in TIMING:
                err(f"{w}: invalid narration entry")
        rs = s.get("reasoning") or {}
        if rs.get("source") not in REASONING_SRC:
            err(f"{w}: reasoning.source {rs.get('source')!r} invalid")
        if rs.get("source") == "narrated" and not s.get("narration"):
            err(f"{w}: reasoning says 'narrated' but the step has no narration")
        ai = s.get("ai_assessment")
        if ai is not None and (ai.get("suggestion_only") is not True or ai.get("human_decision") not in ("accepted", "rejected", None)):
            err(f"{w}: AI assessment must be marked suggestion_only with a valid human_decision")
    fo = rec.get("final_observation") or {}
    image_ok(fo.get("image"), "final_observation")
    rv = rec.get("review") or {}
    if rv.get("outcome") not in ("pass", "fail", None):
        err(f"review.outcome {rv.get('outcome')!r} invalid")
    if bool(rv.get("outcome")) != (rv.get("outcome_source") == "human"):
        err("review.outcome must come from a human (outcome_source 'human')")
    for c in rv.get("checklist") or []:
        if c.get("human_verdict") not in ("met", "not_met", "unclear", None):
            err(f"checklist item {c.get('id')}: invalid human_verdict")
        if c.get("ai_check") and c["ai_check"].get("verdict") not in ("supported", "contradicted", "unknown"):
            err(f"checklist item {c.get('id')}: invalid ai_check verdict")
    tr_ids = set()
    for g in rec.get("transcript") or []:
        if not (_num(g.get("t_start")) and _num(g.get("t_end")) and g["t_end"] >= g["t_start"] - EPS):
            err(f"transcript segment {g.get('id')}: invalid times")
        tr_ids.add(g.get("id"))
    for s in steps:
        for n in s.get("narration") or []:
            if n.get("segment_id") not in tr_ids:
                err(f"step {s.get('uid')}: narration segment {n.get('segment_id')} not in transcript")
    return E, W


# ---------------------------------------------------------------- claude
def _coord_ok(c, size) -> bool:
    return (isinstance(c, list) and len(c) == 2 and all(isinstance(v, int) for v in c)
            and 0 <= c[0] < size[0] and 0 <= c[1] < size[1])


def validate_tool_input(name: str, inp: dict, size) -> list[str]:
    P = []
    if not isinstance(inp, dict):
        return ["input must be an object"]
    allowed = {"coordinate", "text"} if name in CLICKS else {
        "screenshot": set(), "type": {"text"}, "key": {"text", "repeat"}, "scroll": {"scroll_direction", "scroll_amount", "coordinate", "text"},
        "mouse_move": {"coordinate"}, "left_click_drag": {"start_coordinate", "coordinate", "text"}, "zoom": {"region"},
        "hold_key": {"text", "duration"}, "wait": {"duration"}, "left_mouse_down": set(), "left_mouse_up": set(),
        "cursor_position": set()}.get(name, set())
    extra = set(inp) - allowed
    if extra:
        P.append(f"unexpected input field(s) {sorted(extra)}")
    if name in CLICKS or name == "scroll":
        if "coordinate" in inp and not _coord_ok(inp["coordinate"], size):
            P.append(f"coordinate {inp['coordinate']} not inside the {size[0]}x{size[1]} screenshot")
        if "text" in inp and not all(m in MODS for m in str(inp["text"]).split("+")):
            P.append(f"modifier text {inp['text']!r} invalid")
    if name == "scroll":
        if inp.get("scroll_direction") not in ("up", "down", "left", "right"):
            P.append("scroll_direction invalid")
        if not (isinstance(inp.get("scroll_amount"), int) and inp["scroll_amount"] >= 1):
            P.append("scroll_amount must be a positive integer")
    if name == "type" and not (isinstance(inp.get("text"), str) and inp["text"]):
        P.append("type needs non-empty text")
    if name == "key":
        if not (isinstance(inp.get("text"), str) and KEY_RE.match(inp["text"])):
            P.append(f"key text {inp.get('text')!r} invalid")
        if "repeat" in inp and not (isinstance(inp["repeat"], int) and 1 <= inp["repeat"] <= 100):
            P.append("repeat must be 1-100")
    if name == "mouse_move" and not _coord_ok(inp.get("coordinate"), size):
        P.append("mouse_move needs a coordinate inside the screenshot")
    return P


def validate_claude(c: dict, base: Path, listed: set[str]) -> tuple[list[str], list[str]]:
    E: list[str] = []
    W: list[str] = []
    rid = c.get("recording_id", "?")
    err = lambda m: E.append(f"{rid} (claude): {m}")
    if c.get("format") != CLAUDE_FORMAT:
        err(f"format {c.get('format')!r}, expected {CLAUDE_FORMAT}")
        return E, W
    if (c.get("tool") or {}).get("type") != CLAUDE_TOOL_TYPE:
        err(f"tool type must be {CLAUDE_TOOL_TYPE}")
    size = (c.get("screenshot") or {}).get("screenshot_size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size)):
        err("screenshot.screenshot_size missing")
        return E, W
    if max(size) > 2576:
        err(f"screenshots {size} exceed the 2576 px long-edge limit")
    msgs = c.get("messages") or []
    if not msgs or msgs[0].get("role") != "user":
        err("messages must start with a user turn")
    ids_seen = set()

    def check_image(block, where):
        src = block.get("source") or {}
        if src.get("type") == "thinkaloud_asset":
            p = src.get("path")
            if p not in listed or not (base / p).exists():
                err(f"{where}: image {p} missing from bundle")
        elif src.get("type") != "base64":
            err(f"{where}: unsupported image source {src.get('type')!r}")

    for i, m in enumerate(msgs):
        if i and m.get("role") == msgs[i - 1].get("role"):
            err(f"message {i}: roles must alternate")
        content = m.get("content") or []
        if m.get("role") == "assistant":
            uses = [b for b in content if b.get("type") == "tool_use"]
            if len(uses) != len(content):
                err(f"message {i}: assistant turns contain only tool_use blocks in this format")
            nxt = msgs[i + 1].get("content") if i + 1 < len(msgs) and msgs[i + 1].get("role") == "user" else None
            if nxt is None:
                err(f"message {i}: tool calls without a following user turn of results")
                continue
            results = {b.get("tool_use_id"): b for b in nxt if b.get("type") == "tool_result"}
            for u in uses:
                uid = u.get("id")
                if not uid or not TOOL_ID_RE.match(uid) or uid in ids_seen:
                    err(f"message {i}: tool_use id {uid!r} invalid or duplicated")
                ids_seen.add(uid)
                if u.get("toolset_name") != "computer":
                    err(f"message {i}: tool_use {uid} lacks toolset_name 'computer'")
                if u.get("name") not in MEMBERS:
                    err(f"message {i}: {u.get('name')!r} is not a computer toolset member")
                    continue
                for p in validate_tool_input(u["name"], u.get("input"), size):
                    err(f"message {i} {u['name']}: {p}")
                r = results.get(uid)
                if r is None:
                    err(f"message {i}: no tool_result for {uid}")
                    continue
                if r.get("toolset_name") != "computer":
                    err(f"message {i + 1}: tool_result for {uid} lacks toolset_name 'computer'")
                rc = r.get("content") or []
                if u["name"] == "screenshot" and not r.get("is_error"):
                    imgs = [b for b in rc if b.get("type") == "image"]
                    if not imgs:
                        err(f"message {i + 1}: screenshot result without an image")
                    for b in imgs:
                        check_image(b, f"message {i + 1}")
            if len(results) != len(uses):
                err(f"message {i + 1}: {len(results)} results for {len(uses)} tool calls")
        else:
            for b in content:
                if b.get("type") == "image":
                    check_image(b, f"message {i}")
    if bool(c.get("valid_for_training")) != (not c.get("errors")):
        err("valid_for_training must be false exactly when errors are reported")
    return E, W


# ---------------------------------------------------------------- bundle
def validate_bundle(path) -> dict:
    base = Path(path)
    E: list[str] = []
    W: list[str] = []
    mp = base / "manifest.json"
    if not mp.exists():
        return {"ok": False, "errors": ["manifest.json not found"], "warnings": [], "recordings": 0}
    man = json.loads(mp.read_text(encoding="utf-8"))
    if not str(man.get("format", "")).startswith("thinkaloud.bundle/1."):
        E.append(f"manifest format {man.get('format')!r} not supported")
    listed = set()
    for f in man.get("files", []):
        p = base / f["path"]
        listed.add(f["path"])
        if not p.exists():
            E.append(f"file {f['path']} listed in manifest is missing")
        elif p.stat().st_size != f.get("bytes") or _sha(p) != f.get("sha256"):
            E.append(f"file {f['path']} does not match its manifest checksum")
    for p in base.rglob("*"):
        if p.is_file() and p.name != "manifest.json" and p.relative_to(base).as_posix() not in listed:
            W.append(f"file {p.relative_to(base).as_posix()} is not listed in the manifest")
    for r in man.get("recordings", []):
        if r.get("dataset"):
            dp = base / r["dataset"]
            if not dp.exists():
                E.append(f"{r.get('id')}: dataset file missing")
            else:
                e, w = validate_record(json.loads(dp.read_text(encoding="utf-8")), base, listed,
                                       str(Path(r["dataset"]).parent.as_posix()))
                E += e
                W += w
        if r.get("claude"):
            cp = base / r["claude"]
            if not cp.exists():
                E.append(f"{r.get('id')}: claude file missing")
            else:
                c = json.loads(cp.read_text(encoding="utf-8"))
                e, w = validate_claude(c, base, listed)
                E += e
                W += w
    return {"ok": not E, "errors": E, "warnings": W, "recordings": len(man.get("recordings", []))}


class Bundle:
    def __init__(self, path: Path, manifest: dict):
        self.path, self.manifest = path, manifest

    def recordings(self):
        for r in self.manifest["recordings"]:
            if r.get("dataset"):
                yield json.loads((self.path / r["dataset"]).read_text(encoding="utf-8"))

    def claude(self, recording_id: str) -> dict:
        r = next(x for x in self.manifest["recordings"] if x["id"] == recording_id)
        return json.loads((self.path / r["claude"]).read_text(encoding="utf-8"))

    def claude_messages(self, recording_id: str) -> list[dict]:
        return materialize_claude_messages(self.path, self.claude(recording_id))


def materialize_claude_messages(base: Path, c: dict) -> list[dict]:
    """Replace thinkaloud_asset image sources with base64 sources for the Messages API."""
    def fix(block):
        if block.get("type") == "image" and (block.get("source") or {}).get("type") == "thinkaloud_asset":
            data = base64.standard_b64encode((Path(base) / block["source"]["path"]).read_bytes()).decode()
            return {"type": "image", "source": {"type": "base64", "media_type": block["source"].get("media_type", "image/png"), "data": data}}
        if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
            return {**block, "content": [fix(b) for b in block["content"]]}
        return block
    return [{"role": m["role"], "content": [fix(b) for b in m["content"]]} for m in c["messages"]]


def load_bundle(path, strict: bool = True) -> Bundle:
    p = Path(path)
    if p.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="thinkaloud-"))
        with zipfile.ZipFile(p) as z:
            z.extractall(tmp)
        subs = [d for d in tmp.iterdir() if d.is_dir()]
        p = subs[0] if len(subs) == 1 and not (tmp / "manifest.json").exists() else tmp
    report = validate_bundle(p)
    if strict and not report["ok"]:
        raise ValidationError(report)
    return Bundle(p, json.loads((p / "manifest.json").read_text(encoding="utf-8")))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if not args:
        print(__doc__)
        return 2
    try:
        b = load_bundle(args[0], strict=False)
        report = validate_bundle(b.path)
    except Exception as e:
        report = {"ok": False, "errors": [f"could not open bundle: {e}"], "warnings": [], "recordings": 0}
    if as_json:
        print(json.dumps(report))
    else:
        print(f"{'OK' if report['ok'] else 'INVALID'}: {report['recordings']} recording(s), "
              f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
        for e in report["errors"]:
            print("  error:", e)
        for w in report["warnings"][:50]:
            print("  warning:", w)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
