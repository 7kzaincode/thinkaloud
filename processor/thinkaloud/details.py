"""Edit a recording's task and "done when" text after it was recorded.

meta.json stays the source of truth (the text as first recorded is kept under
"as_recorded"). A processed recording is updated in place, without reprocessing:
trajectory.json gets the new text, the task-related QC flags and a new QC summary, and a
review that was up to date with it (trajectory.reviewed.json, base_hash) is moved onto
the new version so the reviewer's page doesn't report it as reprocessed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import qc
from .fsutil import write_json_atomic

MAX_LEN = 1000
LOCK_STALE_S = 30.0


class DetailsError(Exception):
    pass


def _hash(p: Path) -> str:
    # same as the viewer's fileHash (viewer/lib/sessions.ts): the review's base_hash
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _clean(v, what: str) -> str:
    if not isinstance(v, str):
        raise DetailsError(f"{what} must be text")
    v = " ".join(v.split())
    if len(v) > MAX_LEN:
        raise DetailsError(f"{what} is too long (at most {MAX_LEN} characters)")
    return v


def _lock(session: Path) -> Path | None:
    """Take processing.lock so a processing job can't start (or be running) meanwhile."""
    lock = session / "processing.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"job_id": "details", "at": datetime.now(timezone.utc).isoformat()}).encode())
        os.close(fd)
        return lock
    except (FileExistsError, PermissionError):
        try:
            if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                return None  # left behind by a crashed job; nobody is processing
        except OSError:
            pass
        raise DetailsError("this recording is being processed right now; try again when it has finished")


def update_details(session: Path, task=None, success_criteria=None) -> dict:
    session = Path(session)
    meta_p = session / "meta.json"
    if not meta_p.exists():
        raise DetailsError("this recording has no meta.json (it may still be recording)")
    new = {}
    if task is not None:
        new["task"] = _clean(task, "the task")
        if not new["task"]:
            raise DetailsError("the task can't be empty")
    if success_criteria is not None:
        new["success_criteria"] = _clean(success_criteria, "“done when”")
    lock = _lock(session)
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        changed = {k: v for k, v in new.items() if (meta.get(k) or "") != v}
        if not changed:
            return {"ok": True, "changed": False, "task": meta.get("task", ""),
                    "success_criteria": meta.get("success_criteria", "")}
        orig = meta.setdefault("as_recorded", {})
        for k, v in changed.items():
            orig.setdefault(k, meta.get(k) or "")
            meta[k] = v
        meta["details_edited_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json_atomic(meta_p, meta)

        traj_p = session / "trajectory.json"
        if traj_p.exists():
            old_hash = _hash(traj_p)
            t = json.loads(traj_p.read_text(encoding="utf-8"))
            patch = _patch(t, meta)
            write_json_atomic(traj_p, t)
            rev_p = session / "trajectory.reviewed.json"
            if rev_p.exists():
                rev = json.loads(rev_p.read_text(encoding="utf-8"))
                if (rev.get("review") or {}).get("base_hash") == old_hash:
                    rev.update(patch)
                    rev["review"]["base_hash"] = _hash(traj_p)
                    write_json_atomic(rev_p, rev)
                # otherwise the review was already behind: the viewer rebases it on the next read
        return {"ok": True, "changed": True, "task": meta.get("task", ""),
                "success_criteria": meta.get("success_criteria", "")}
    finally:
        if lock:
            lock.unlink(missing_ok=True)


def _patch(t: dict, meta: dict) -> dict:
    """Apply the new text to a trajectory; returns the fields that changed."""
    flags = [f for f in t.get("session_flags", []) if f.get("code") not in qc.TASK_FLAG_CODES] + qc.task_flags(meta)
    report = {**(t.get("qc") or {}), **qc.summarize(t.get("steps", []), flags)}
    patch = {"task": meta.get("task", ""), "success_criteria": meta.get("success_criteria", ""),
             "session_flags": flags, "qc": report}
    t.update(patch)
    return patch


def main(argv=None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="engine details",
                                description='Change a recording\'s task / "done when" (JSON on stdin)')
    p.add_argument("session", type=Path)
    a = p.parse_args(argv)
    try:
        body = json.loads(sys.stdin.read() or "{}")
        r = update_details(a.session, body.get("task"), body.get("success_criteria"))
    except (DetailsError, ValueError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e)}), flush=True)
        return 1
    print(json.dumps(r, ensure_ascii=False), flush=True)
    return 0
