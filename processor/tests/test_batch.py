"""Batch processing (B3): isolation, persistence, idempotency, retries, restart."""
import json
import shutil
from pathlib import Path

import pytest

from thinkaloud import batch

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "synthetic-flight"
LEGACY = Path(__file__).resolve().parent / "fixtures" / "legacy-v01"


@pytest.fixture
def library(tmp_path):
    if not (SAMPLE / "meta.json").exists():
        pytest.skip("sample missing")
    lib = tmp_path / "sessions"
    lib.mkdir()
    ign = shutil.ignore_patterns("trajectory*.json", "playback.mp4", "processing.*")
    shutil.copytree(SAMPLE, lib / "rec-a", ignore=ign)
    shutil.copytree(SAMPLE, lib / "rec-b", ignore=ign)
    shutil.copytree(LEGACY, lib / "rec-legacy", ignore=ign)
    bad = lib / "rec-broken"
    bad.mkdir()
    (bad / "meta.json").write_text(json.dumps({"session_id": "rec-broken", "task": "x"}))
    (bad / "events.jsonl").write_text('{"t": 0, "type": "marker"\nnot json at all\n')
    return lib


def run(lib, *extra):
    return batch.main([str(lib), "--jobs", str(lib.parent / "jobs"), "--concurrency", "2", "--retries", "1",
                       "--job-id", f"j{len(list((lib.parent / 'jobs').glob('*.json'))) if (lib.parent / 'jobs').exists() else 0}",
                       *extra])


def status(lib, name):
    return json.loads((lib / name / "processing.json").read_text())


def test_failure_is_isolated_and_everything_is_persisted(library):
    code = run(library)
    assert code == 1                                            # one recording failed
    for ok in ("rec-a", "rec-b", "rec-legacy"):
        st = status(library, ok)
        assert st["state"] == "done" and st["attempts"] == 1 and (library / ok / "trajectory.json").exists()
    br = status(library, "rec-broken")
    assert br["state"] == "failed" and br["attempts"] == 2      # retried once
    assert br["error"] == "events.jsonl line 1 is not valid JSON (Expecting ',' delimiter); the recording may have been cut off"
    assert not (library / "rec-broken" / "trajectory.json").exists()
    assert "attempt 2" in (library / "rec-broken" / "processing.log").read_text()
    job = json.loads((library.parent / "jobs" / "j0.json").read_text())
    assert (job["total"], job["done"], job["failed"], job["state"]) == (4, 3, 1, "done_with_failures")


def test_rerun_is_idempotent_and_force_reprocesses(library):
    run(library)
    before = (library / "rec-a" / "trajectory.json").stat().st_mtime_ns
    run(library)
    job = json.loads((library.parent / "jobs" / "j1.json").read_text())
    assert {s["id"]: s["state"] for s in job["sessions"]}["rec-a"] == "skipped"
    assert (library / "rec-a" / "trajectory.json").stat().st_mtime_ns == before
    run(library, "--force")
    assert (library / "rec-a" / "trajectory.json").stat().st_mtime_ns != before


def test_changed_input_is_reprocessed(library):
    run(library)
    ev = library / "rec-b" / "events.jsonl"
    ev.write_text(ev.read_text() + "\n")                        # inputs changed
    run(library)
    job = json.loads((library.parent / "jobs" / "j1.json").read_text())
    states = {s["id"]: s["state"] for s in job["sessions"]}
    assert states["rec-b"] == "done" and states["rec-a"] == "skipped"


def test_interrupted_run_is_recovered(library):
    # simulate a crash: a job and a recording left "running" with an old heartbeat
    jobs = library.parent / "jobs"
    jobs.mkdir()
    (jobs / "dead.json").write_text(json.dumps({"id": "dead", "state": "running", "heartbeat_at": "2020-01-01T00:00:00+00:00"}))
    (library / "rec-a" / "processing.json").write_text(json.dumps(
        {"state": "running", "job_id": "dead", "heartbeat_at": "2020-01-01T00:00:00+00:00", "attempts": 1}))
    run(library)
    assert json.loads((jobs / "dead.json").read_text())["state"] == "interrupted"
    assert status(library, "rec-a")["state"] == "done"


def test_live_run_by_another_job_is_not_touched(library):
    (library / "rec-a" / "processing.json").write_text(json.dumps(
        {"state": "running", "job_id": "other", "heartbeat_at": batch.now_iso(), "attempts": 1}))
    (library / "rec-a" / "processing.lock").write_text(json.dumps({"job_id": "other"}))
    run(library)
    assert status(library, "rec-a")["job_id"] == "other"
    assert not (library / "rec-a" / "trajectory.json").exists()
    assert (library / "rec-a" / "processing.lock").exists()          # not ours to remove


def test_stale_lock_is_taken_over_and_locks_are_released(library):
    import os, time
    lock = library / "rec-a" / "processing.lock"
    lock.write_text("{}")
    old = time.time() - 120
    os.utime(lock, (old, old))                                        # its holder died two minutes ago
    run(library)
    assert status(library, "rec-a")["state"] == "done"
    assert not any(p.name == "processing.lock" for p in library.rglob("*"))


def test_queued_recordings_keep_heart_beating(library, monkeypatch):
    """Review A #5 / B #5: queued entries went stale and showed as interrupted."""
    monkeypatch.setattr(batch, "HEARTBEAT_S", 0.2)
    job = batch.Job(batch.discover([library]), library.parent / "jobs", "hb", 1, 0, False, "base.en", "local")
    job.jobs_dir.mkdir()
    for s in job.sessions:
        batch.write_json(s / "processing.json", {"state": "queued", "job_id": "hb", "heartbeat_at": "2020-01-01T00:00:00+00:00"})
    import threading
    t = threading.Thread(target=job.heartbeat, daemon=True)
    t.start()
    import time
    time.sleep(0.6)
    job.stop.set()
    for s in job.sessions:
        assert batch.age_s(status(library, s.name)["heartbeat_at"]) < 5


def test_only_one_job_takes_over_a_stale_lock(library):
    """Review A new #7: two jobs that both saw the same stale lock both 'took it over'."""
    import os, threading, time
    session = library / "rec-a"
    lock = session / "processing.lock"
    wins = []
    for _round in range(20):
        lock.write_text("{}")
        old = time.time() - 120
        os.utime(lock, (old, old))
        jobs = [batch.Job([session], library.parent / "jobs", f"j{i}", 1, 0, False, "base.en", "local") for i in range(4)]
        barrier = threading.Barrier(len(jobs))
        got = []

        def go(j):
            barrier.wait()
            got.append(j.claim(session))

        threads = [threading.Thread(target=go, args=(j,)) for j in jobs]
        [t.start() for t in threads]
        [t.join() for t in threads]
        wins.append(sum(got))
        lock.unlink(missing_ok=True)
    assert all(w == 1 for w in wins), wins
    assert not list(session.glob("processing.lock.stale-*"))
