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
    assert br["error"] and "exit" in br["error"]
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
    run(library)
    assert status(library, "rec-a")["job_id"] == "other"
    assert not (library / "rec-a" / "trajectory.json").exists()
