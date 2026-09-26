"""CLI: python -m thinkaloud <session_dir>... [--transcript t.json] [--model base.en] [--ocr]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import process


def main(argv=None) -> int:
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
    for d in dirs:
        print(f"== {d}")
        t = process(d, transcript=args.transcript, model=args.model,
                    ocr=args.ocr, redact=not args.no_redact)
        print(f"QC: {t['qc']['summary']}")
        for f in t["session_flags"]:
            print(f"  [{f['severity']}] {f['code']}: {f['detail']}")
        for s in t["steps"]:
            for f in s["flags"]:
                if f["severity"] != "info":
                    print(f"  step {s['id']:>3} [{f['severity']}] {f['code']}: {f['detail']}")
        worst = max(worst, t["qc"]["high_severity"])
        print(f"wrote {d / 'trajectory.json'}")
    return 1 if (args.strict and worst) else 0


if __name__ == "__main__":
    sys.exit(main())
