"""Batch-process many recordings with bounded concurrency. Runs natively or in Docker.

    python -m thinkaloud batch <session dirs or folders of sessions> --jobs DIR
           [--concurrency 2] [--retries 1] [--force] [--job-id ID] [--model large-v3-turbo]

Each recording is processed in its own subprocess (`python -m thinkaloud <dir>`), so a
crash or bad input in one recording cannot take down the others. State is persisted:

  <session>/processing.json   state queued|running|done|failed|interrupted, attempts,
                              error, input_hash, heartbeat_at, job_id (read by the viewer)
  <session>/processing.log    output of every attempt
  <jobs>/<job_id>.json        job summary: counts, per-recording states, heartbeat

Claiming: a recording is claimed with an exclusive lock file (processing.lock, created with
O_EXCL, touched by the heartbeat), so two jobs never process it at once; a lock whose
heartbeat stopped for STALE_S is taken over.

Idempotent: a recording whose inputs (and speech model) hash to the same value as its last
successful run (processing.json done_input_hash, and trajectory.json exists) is skipped unless
--force. Outputs are written
atomically by the pipeline, so a failed or interrupted attempt leaves the previous
good output in place. Restart handling: a "running" entry whose heartbeat is older
than STALE_S is treated as interrupted and processed again; a recording another live
job is working on is skipped. Heartbeats (not PIDs) are used so this also works when
the batch runs inside a container and the viewer runs on the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .fsutil import write_json_atomic

STALE_S = 30.0
HEARTBEAT_S = 2.0
PROCESSOR_VERSION = "0.2"
INPUT_FILES = ("events.jsonl", "meta.json", "frames/stills.jsonl", "video_frames.jsonl")
MEDIA_FILES = ("audio.wav", "screen.mkv")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age_s(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return 1e9


def write_json(p: Path, data) -> None:
    write_json_atomic(p, data)  # unique temp name + retries on Windows file locks


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def input_hash(session: Path, model: str = "") -> str:
    h = hashlib.sha256(PROCESSOR_VERSION.encode())
    h.update(f"model:{model}".encode())  # another speech model means another transcript
    for rel in INPUT_FILES:
        p = session / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    for rel in MEDIA_FILES:  # large: size + mtime is enough to notice a change
        p = session / rel
        st = p.stat() if p.exists() else None
        h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}".encode() if st else f"{rel}:missing".encode())
    return h.hexdigest()[:16]


def discover(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if (p / "events.jsonl").exists():
            out.append(p)
        elif p.is_dir():
            out += sorted(c for c in p.iterdir() if c.is_dir() and not c.name.startswith((".", "_"))
                          and ((c / "events.jsonl").exists() or (c / "meta.json").exists()))
    seen, uniq = set(), []
    for p in out:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def processor_command(session: Path, model: str) -> list[str]:
    if getattr(sys, "frozen", False):  # the desktop app's engine executable
        return [sys.executable, "process", "--json", "--model", model, str(session)]
    return [sys.executable, "-m", "thinkaloud", "--json", "--model", model, str(session)]


class Job:
    def __init__(self, sessions: list[Path], jobs_dir: Path, job_id: str, concurrency: int, retries: int,
                 force: bool, model: str, runner: str):
        self.sessions, self.jobs_dir, self.id = sessions, jobs_dir, job_id
        self.concurrency, self.retries, self.force, self.model, self.runner = concurrency, retries, force, model, runner
        self.lock = threading.Lock()
        self.file_lock = threading.RLock()   # serializes read-modify-write of processing.json files
        self.errors: list[str] = []
        self.state = {s.name: {"id": s.name, "state": "queued", "attempts": 0, "error": None} for s in sessions}
        self.running: set[Path] = set()
        self.stop = threading.Event()
        self.file = jobs_dir / f"{job_id}.json"
        self.started = now_iso()

    # ---- persistence ------------------------------------------------------------
    def status(self, session: Path, **fields) -> None:
        p = session / "processing.json"
        with self.lock:
            self.state[session.name].update({k: v for k, v in fields.items() if k in ("state", "attempts", "error")})
        try:
            with self.file_lock:
                cur = read_json(p) or {}
                cur.update(fields, job_id=self.id, heartbeat_at=now_iso())
                write_json(p, cur)
        except OSError as e:  # never let a status write abort the job
            self.errors.append(f"{session.name}: could not write status: {e}")

    def claim(self, session: Path) -> bool:
        lock = session / "processing.lock"
        for _ in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"job_id": self.id, "at": now_iso()}).encode())
                os.close(fd)
                return True
            except PermissionError:
                # Windows: the lock is being deleted by another job right now (delete pending)
                return False
            except FileExistsError:
                try:
                    age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                except PermissionError:
                    return False
                if age <= STALE_S:
                    return False
                # the holder stopped heart-beating: take over, one job at a time. The takeover itself
                # is guarded by a second exclusive lock, so the stale check, the removal and the new
                # exclusive create can't interleave between jobs.
                if not self._takeover(session, lock):
                    return False
        return False

    def _takeover(self, session: Path, lock: Path) -> bool:
        """Remove a stale processing.lock while holding processing.takeover; False if another job
        is taking over (or the lock turned out to be live)."""
        guard = session / "processing.takeover"
        try:
            fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except PermissionError:  # Windows: another job's guard is being deleted right now
            return False
        except FileExistsError:
            try:  # a takeover guard left by a crashed job
                if time.time() - guard.stat().st_mtime > STALE_S:
                    guard.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        try:
            try:
                if time.time() - lock.stat().st_mtime <= STALE_S:
                    return False  # someone else took over before we got the guard
            except FileNotFoundError:
                return True
            lock.unlink(missing_ok=True)
            return True
        finally:
            guard.unlink(missing_ok=True)

    def release(self, session: Path) -> None:
        try:
            (session / "processing.lock").unlink(missing_ok=True)
        except OSError:
            pass

    def save(self, final_state: str | None = None) -> None:
        with self.lock:
            vals = list(self.state.values())
            data = {"id": self.id, "runner": self.runner, "concurrency": self.concurrency, "retries": self.retries,
                    "force": self.force, "state": final_state or "running", "started_at": self.started,
                    "heartbeat_at": now_iso(), "finished_at": now_iso() if final_state else None,
                    "total": len(vals), "done": sum(v["state"] in ("done", "skipped") for v in vals),
                    "failed": sum(v["state"] == "failed" for v in vals), "sessions": vals,
                    "errors": self.errors[-20:]}
        write_json(self.file, data)

    def heartbeat(self) -> None:
        while not self.stop.wait(HEARTBEAT_S):
            try:
                self.save()
                with self.lock:
                    running = set(self.running)
                    waiting = [s for s in self.sessions if self.state[s.name]["state"] in ("queued", "running")]
                for s in waiting:  # queued ones too, or the viewer would call them interrupted
                    with self.file_lock:
                        cur = read_json(s / "processing.json") or {}
                        if cur.get("job_id") == self.id and cur.get("state") in ("queued", "running"):
                            cur["heartbeat_at"] = now_iso()
                            write_json(s / "processing.json", cur)
                    if s in running:
                        try:
                            os.utime(s / "processing.lock")
                        except OSError:
                            pass
            except Exception as e:  # a failed beat must not stop the heartbeat
                self.errors.append(f"heartbeat: {type(e).__name__}: {e}")

    # ---- work ---------------------------------------------------------------------
    def one(self, session: Path) -> None:
        if not self.claim(session):
            prev = read_json(session / "processing.json") or {}
            with self.lock:
                self.state[session.name].update(state="skipped", error=f"already being processed by job {prev.get('job_id')}")
            return
        try:
            self._one(session)
        finally:
            self.release(session)

    def _one(self, session: Path) -> None:
        prev = read_json(session / "processing.json") or {}
        try:
            ih = input_hash(session, self.model)
        except OSError as e:
            self.status(session, state="failed", attempts=0, error=f"cannot read recording: {e}", finished_at=now_iso())
            return
        if (not self.force and prev.get("done_input_hash") == ih and (session / "trajectory.json").exists()):
            self.status(session, state="done", error=None)  # up to date: nothing to do
            with self.lock:
                self.state[session.name].update(state="skipped", error=None)
            return
        if prev.get("state") == "running":
            prev["note"] = "previous run was interrupted"
        with self.lock:
            self.running.add(session)
        log = session / "processing.log"
        attempts, error = 0, None
        try:
            while attempts <= self.retries and not self.stop.is_set():
                attempts += 1
                self.status(session, state="running", attempts=attempts, error=None, started_at=now_iso(),
                            finished_at=None, input_hash=ih)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"\n=== {now_iso()} job {self.id} attempt {attempts} ===\n")
                    f.flush()
                    try:
                        pkg_parent = str(Path(__file__).resolve().parent.parent)
                        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
                               "PYTHONPATH": os.pathsep.join(filter(None, [pkg_parent, os.environ.get("PYTHONPATH")]))}
                        r = subprocess.run(processor_command(session, self.model), stdout=f, stderr=subprocess.STDOUT,
                                           timeout=3600, env=env)
                        code = r.returncode
                    except subprocess.TimeoutExpired:
                        code = -9
                        f.write("timed out after 3600 s\n")
                if code == 0 and (session / "trajectory.json").exists():
                    self.status(session, state="done", attempts=attempts, error=None, finished_at=now_iso(),
                                input_hash=ih, done_input_hash=ih)
                    return
                lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
                plain = []  # InputError messages from the processor (text or --json event lines)
                for l in lines:
                    if l.startswith("error: "):
                        plain.append(l[len("error: "):])
                    elif l.startswith("{") and '"event": "error"' in l:
                        try:
                            plain.append(json.loads(l)["message"])
                        except (ValueError, KeyError):
                            pass
                error = plain[-1][:400] if plain else f"exit {code}: " + " | ".join(lines[-3:])[-400:]
                if attempts <= self.retries:
                    time.sleep(min(10, 1.5 * attempts))
            self.status(session, state="failed", attempts=attempts, error=error or "interrupted", finished_at=now_iso())
        finally:
            with self.lock:
                self.running.discard(session)

    def run(self) -> dict:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        recover(self.jobs_dir)
        for s in self.sessions:
            cur = read_json(s / "processing.json") or {}
            try:
                held = time.time() - (s / "processing.lock").stat().st_mtime <= STALE_S
            except OSError:  # no lock, or released between checks
                held = False
            if not held:
                cur.update(state="queued", job_id=self.id, heartbeat_at=now_iso(), error=None)
                write_json(s / "processing.json", cur)
        self.save()
        hb = threading.Thread(target=self.heartbeat, daemon=True)
        hb.start()
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                list(pool.map(self.one, self.sessions))
            final = "done" if not any(v["state"] == "failed" for v in self.state.values()) else "done_with_failures"
        except KeyboardInterrupt:
            final = "interrupted"
            raise
        finally:
            self.stop.set()
            self.save(final_state=locals().get("final", "interrupted"))
        return read_json(self.file)


def recover(jobs_dir: Path) -> list[str]:
    """Mark jobs whose heartbeat stopped as interrupted (the process died)."""
    fixed = []
    for f in jobs_dir.glob("*.json"):
        j = read_json(f)
        if j and j.get("state") == "running" and age_s(j.get("heartbeat_at")) > STALE_S:
            j["state"] = "interrupted"
            write_json(f, j)
            fixed.append(j["id"])
    return fixed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="thinkaloud batch")
    p.add_argument("sessions", nargs="+", type=Path, help="session folders, or folders containing sessions")
    p.add_argument("--jobs", type=Path, required=True, help="folder for job status files")
    p.add_argument("--concurrency", type=int, default=int(os.environ.get("THINKALOUD_CONCURRENCY", "2")))
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--force", action="store_true", help="reprocess even if inputs are unchanged")
    p.add_argument("--job-id", default=None)
    p.add_argument("--model", default=None, help="speech model (default: THINKALOUD_WHISPER_MODEL or large-v3-turbo)")
    p.add_argument("--runner", default=os.environ.get("THINKALOUD_RUNNER", "local"))
    a = p.parse_args(argv)
    if not 1 <= a.concurrency <= 16:
        p.error("--concurrency must be 1-16")
    sessions = discover(a.sessions)
    if not sessions:
        print(json.dumps({"ok": False, "error": "no recordings found"}))
        return 2
    if not a.model:
        from .transcribe import default_model

        a.model = default_model()
    job_id = a.job_id or f"b{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
    print(json.dumps({"event": "started", "job_id": job_id, "total": len(sessions)}), flush=True)
    result = Job(sessions, a.jobs, job_id, a.concurrency, a.retries, a.force, a.model, a.runner).run()
    print(json.dumps({"ok": True, "job_id": job_id, "state": result["state"], "total": result["total"],
                      "done": result["done"], "failed": result["failed"]}), flush=True)
    return 0 if result["failed"] == 0 else 1
