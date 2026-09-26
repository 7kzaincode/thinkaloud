"""Pair each step with a truthful "before" and "after" screen.

Candidates are captures with known grab start/end times on the recording clock:
PNG stills the recorder kept (frames/stills.jsonl) and, as a fallback, frames of
the screen video (video_frames.jsonl, decoded on demand).

before(step):
    the latest capture whose grab FINISHED at or before step.t_start.
    status "ok"      it also started after the previous step ended, so it shows the
                     result of everything before this action and nothing of this one
    status "predates_previous_action"
                     it started before the previous step finished (rapid actions);
                     it is still strictly before this action, but may not show the
                     previous action's result
    status "stale"   older than max_age_s (capture fell behind or was paused)
    status "missing" nothing was captured before the action

after(step):
    the latest capture that STARTED at or after step.t_end and FINISHED before the
    next step started (or before a pause began / the recording ended).
    status "settled"   captured at least settle_s after the action ended, with no
                       other input in between
    status "unsettled" captured sooner; the screen may still have been changing
    status "missing"   the next action began before any new capture

Recorder 0.1 recordings have one screenshot per click/Enter, grabbed
asynchronously when the action happened (no grab end time). Those become
before.status "at_action" (the step's own action; may include early effects) or
"legacy_earlier_action" (an earlier action's screenshot), and after.status "missing".
Nothing is ever fabricated: a missing image stays missing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Capture:
    seq: int | None
    t0: float
    t1: float
    file: str | None          # still PNG path relative to the session, or None (video only)
    kinds: set
    source: str               # "still" | "video"
    pts_ms: int | None = None


def load_stills(session: Path) -> list[Capture]:
    p = session / "frames" / "stills.jsonl"
    if not p.exists():
        return []
    by_seq: dict[int, Capture] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") not in ("ok", "ok_duplicate") or r.get("seq") is None:
            continue
        c = by_seq.get(r["seq"])
        if c is None:
            if not (session / r["file"]).exists():
                continue
            by_seq[r["seq"]] = Capture(r["seq"], r["t_capture_start"], r["t_capture_end"], r["file"],
                                       {r["kind"]}, "still")
        else:
            c.kinds.add(r["kind"])
    return sorted(by_seq.values(), key=lambda c: c.t1)


def load_video_frames(session: Path) -> list[Capture]:
    p = session / "video_frames.jsonl"
    if not p.exists() or not (session / "screen.mkv").exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.append(Capture(r["seq"], r["t_capture_start"], r["t_capture_end"], None, {"video"},
                               "video", r["pts_ms"]))
    return out


def _obs(c: Capture | None, status: str, ref_t: float, reason: str | None = None) -> dict:
    if c is None:
        return {"status": status, "file": None, "reason": reason}
    rec = {"status": status, "file": c.file, "source": c.source, "capture_seq": c.seq,
           "t_capture_start": round(c.t0, 4), "t_capture_end": round(c.t1, 4),
           "offset_s": round(c.t0 - ref_t, 4)}
    if c.pts_ms is not None:
        rec["video_pts_ms"] = c.pts_ms
    if reason:
        rec["reason"] = reason
    return rec


def assign(steps: list[dict], stills: list[Capture], video: list[Capture], duration: float,
           pauses: list[list[float]] | None = None, settle_s: float = 0.6,
           max_age_s: float = 1.0) -> list[Capture]:
    """Sets step['observations'] = {before, after}. Returns video-only captures that
    were chosen, so the caller can extract them from screen.mkv."""
    pauses = [p for p in (pauses or []) if p and p[0] is not None]
    still_seqs = {c.seq for c in stills}
    pool_video = [c for c in video if c.seq not in still_seqs]
    needed: dict[int, Capture] = {}

    def pick_before(t_start: float, prev_end: float | None) -> tuple[Capture | None, str]:
        best = None
        for pool in (stills, pool_video):
            cands = [c for c in pool if c.t1 <= t_start]
            if cands:
                c = max(cands, key=lambda c: c.t1)
                # prefer a lossless still unless the video frame is strictly newer and the still
                # predates the previous action (then the video frame is the more truthful one)
                if best is None or (prev_end is not None and best.t0 < prev_end <= c.t0):
                    best = c
        if best is None:
            return None, "missing"
        if t_start - best.t1 > max_age_s:
            return best, "stale"
        if prev_end is not None and best.t0 < prev_end:
            return best, "predates_previous_action"
        return best, "ok"

    def pick_after(t_end: float, window_end: float) -> Capture | None:
        for pool in (stills, pool_video):
            cands = [c for c in pool if c.t0 >= t_end and c.t1 <= window_end]
            if cands:
                return max(cands, key=lambda c: c.t1)
        return None

    for i, s in enumerate(steps):
        prev_end = steps[i - 1]["t_end"] if i else None
        before, bstatus = pick_before(s["t_start"], prev_end)
        nxt = steps[i + 1]["t_start"] if i + 1 < len(steps) else float("inf")
        window_end = nxt
        for p0, _p1 in pauses:
            if s["t_end"] <= p0 < window_end:
                window_end = p0
        if window_end == float("inf"):
            window_end = duration + 5.0  # the "end" capture is grabbed just after stop
        after = pick_after(s["t_end"], window_end)
        if after is None:
            gap = (nxt if nxt != float("inf") else duration) - s["t_end"]
            aobs = _obs(None, "missing", s["t_end"],
                        "next action began before a new capture" if gap < 1.0 else "no capture in the window")
        else:
            status = "settled" if after.t0 - s["t_end"] >= settle_s else "unsettled"
            aobs = _obs(after, status, s["t_end"])
        bobs = _obs(before, bstatus, s["t_start"],
                    None if before else "nothing was captured before this action")
        s["observations"] = {"before": bobs, "after": aobs}
        for c in (before, after):
            if c is not None and c.source == "video":
                needed[c.seq] = c
    return list(needed.values())


def extract_video_frames(session: Path, captures: list[Capture], size: tuple[int, int] | None = None) -> dict[int, str]:
    """Decode the chosen video frames to PNG (frames/v<seq>.png). Returns seq -> file.
    H.264 needs even dimensions, so the recorder crops a pixel off odd-sized screens; frames are
    padded back to `size` (the screen size every other image has). Written atomically, so an
    interrupted run never leaves a half-written frame behind."""
    if not captures:
        return {}
    import os
    import secrets

    import av
    from PIL import Image

    from .fsutil import replace_retry

    want = {c.pts_ms: c for c in captures}
    out: dict[int, str] = {}
    with av.open(str(session / "screen.mkv")) as container:
        stream = container.streams.video[0]
        tb = stream.time_base
        for frame in container.decode(stream):
            pts_ms = round(frame.pts * tb * 1000) if frame.pts is not None else None
            c = want.get(pts_ms)
            if c is None:
                continue
            rel = f"frames/v{c.seq:06d}.png"
            im = frame.to_image()
            if size and size[0] and size[1] and im.size != tuple(size):
                canvas = Image.new("RGB", tuple(size))
                canvas.paste(im.crop((0, 0, min(im.width, size[0]), min(im.height, size[1]))), (0, 0))
                im = canvas
            tmp = session / f"{rel}.{os.getpid()}.{secrets.token_hex(3)}.tmp"
            im.save(tmp, format="PNG")
            replace_retry(tmp, session / rel)
            out[c.seq] = rel
            if len(out) == len(want):
                break
    return out


def legacy_assign(steps: list[dict], events: list[dict]) -> None:
    """Recorder 0.1: the only images are the ones grabbed when a click/Enter/Tab happened."""
    by_t = sorted((e["t"], e["frame"]) for e in events if e.get("frame"))
    for s in steps:
        earlier = [(t, f) for t, f in by_t if t <= s["t_start"] + 1e-6]
        if not earlier:
            before = {"status": "missing", "file": None,
                      "reason": "recorder 0.1 took no screenshot before this action"}
        elif abs(earlier[-1][0] - s["t_start"]) < 1e-6:
            before = {"status": "at_action", "file": earlier[-1][1], "source": "still",
                      "t_capture_start": earlier[-1][0],
                      "reason": "recorder 0.1 grabbed this when the action happened; "
                                "it may show early effects of the action"}
        else:
            before = {"status": "legacy_earlier_action", "file": earlier[-1][1], "source": "still",
                      "t_capture_start": earlier[-1][0],
                      "reason": "recorder 0.1 screenshot grabbed when an earlier action happened; "
                                "it may not show that action's result"}
        s["observations"] = {"before": before,
                             "after": {"status": "missing", "file": None,
                                       "reason": "recorder 0.1 did not capture after-states"}}
