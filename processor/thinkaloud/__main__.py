"""CLI: python -m thinkaloud <session_dir>... [--transcript t.json] [--model base.en] [--ocr]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import InputError, process


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "batch":  # python -m thinkaloud batch ... (also the Docker entrypoint)
        from .batch import main as batch_main

        return batch_main(argv[1:])
    if argv and argv[0] in ("export", "validate"):
        from . import commands

        return {"export": commands.export_main, "validate": commands.validate_main}[argv[0]](argv[1:])
    p = argparse.ArgumentParser(prog="thinkaloud",
                                description="Turn a recorded session into trajectory.json")
    p.add_argument("sessions", nargs="+", type=Path,
                   help="Session folder(s), or a parent folder containing sessions")
    p.add_argument("--transcript", type=Path,
                   help="Use this transcript JSON instead of running Whisper")
    p.add_argument("--model", default="base.en", help="faster-whisper model size")
    p.add_argument("--ocr", action="store_true", help="OCR screenshots for visible emails")
    p.add_argument("--no-redact", action="store_true",
                   help="Keep typed emails/secrets in the export (not recommended)")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 if any high-severity flag remains")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable progress, one JSON object per line (desktop app)")
    args = p.parse_args(argv)

    dirs: list[Path] = []
    for d in args.sessions:
        if (d / "events.jsonl").exists():
            dirs.append(d)
        elif d.is_dir():
            dirs += sorted(e.parent for e in d.rglob("events.jsonl"))
    if not dirs:
        print(f"no sessions found under {', '.join(map(str, args.sessions))} "
              "(looking for folders containing events.jsonl). "
              "Record one with: python recorder/record.py", file=sys.stderr)
        return 2
    if args.transcript and len(dirs) > 1:
        print("--transcript only works with a single session", file=sys.stderr)
        return 2

    worst = 0
    failed = 0
    for d in dirs:
        try:
            worst = max(worst, _one(d, args))
        except InputError as e:
            failed += 1
            msg = f"{d.name}: {e}"
            print(json.dumps({"event": "error", "dir": str(d), "message": str(e)}) if args.json else f"error: {msg}",
                  flush=True)
    if failed:
        return 1
    return 1 if (args.strict and worst) else 0


def _one(d, args) -> int:
    """Process one recording; returns its number of high-severity flags."""
    if args.json:
        def log(msg, d=d):
            print(json.dumps({"event": "progress", "dir": str(d), "message": msg}), flush=True)
        t = process(d, transcript=args.transcript, model=args.model, ocr=args.ocr, redact=not args.no_redact, log=log)
        print(json.dumps({"event": "processed", "dir": str(d), "session_id": t["session_id"],
                          "summary": t["qc"]["summary"], "high": t["qc"]["high_severity"]}), flush=True)
        return t["qc"]["high_severity"]
    print(f"== {d}")
    t = process(d, transcript=args.transcript, model=args.model, ocr=args.ocr, redact=not args.no_redact)
    print(f"QC: {t['qc']['summary']}")
    for f in t["session_flags"]:
        print(f"  [{f['severity']}] {f['code']}: {f['detail']}")
    for s in t["steps"]:
        for f in s["flags"]:
            if f["severity"] != "info":
                print(f"  step {s['id']:>3} [{f['severity']}] {f['code']}: {f['detail']}")
    print(f"wrote {d / 'trajectory.json'}")
    return t["qc"]["high_severity"]


if __name__ == "__main__":
    sys.exit(main())
