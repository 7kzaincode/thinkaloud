"""Engine subcommands used by the viewer server and the CLI. Each prints one JSON result
line on stdout (last line), so callers can parse it; human-readable notes go to stderr."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def export_main(argv=None) -> int:
    from .dataset import ExportError
    from .export import export_bundle

    p = argparse.ArgumentParser(prog="engine export")
    p.add_argument("sessions", nargs="+", type=Path)
    p.add_argument("--out", type=Path, required=True, help="folder that receives the bundle and .zip")
    p.add_argument("--formats", default="dataset,claude")
    p.add_argument("--include-media", action="store_true")
    p.add_argument("--no-zip", action="store_true")
    p.add_argument("--allow-privacy-flags", action="store_true",
                   help="export recordings that still have open high-severity privacy flags")
    p.add_argument("--allow-privacy-for", action="append", default=[], metavar="ID",
                   help="like --allow-privacy-flags, but only for this recording (repeatable)")
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    allow = True if a.allow_privacy_flags else set(a.allow_privacy_for)
    try:
        r = export_bundle(a.sessions, a.out, formats=a.formats.split(","), include_media=a.include_media,
                          zip_bundle=not a.no_zip, allow_privacy_flags=allow)
    except ExportError as e:
        _out({"ok": False, "error": str(e)})
        return 1
    except Exception as e:
        _out({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    _out(r)
    return 0 if r.get("ok") else 1


def validate_main(argv=None) -> int:
    from .dataset import main as validate

    return validate(["--json", *(argv or [])])


def review_main(argv=None) -> int:
    from .ai_review import run_review

    p = argparse.ArgumentParser(prog="engine review")
    p.add_argument("--session", type=Path, required=True)
    p.add_argument("--kind", choices=["narration", "checklist", "final_screen"], required=True)
    a = p.parse_args(argv)
    payload = json.loads(sys.stdin.read() or "{}")
    _out(run_review(a.session, a.kind, payload.get("trajectory") or {}))
    return 0


def ai_status_main(argv=None) -> int:
    from .ai_review import status

    _out(status())
    return 0


def batch_main(argv=None) -> int:
    from .batch import main as batch

    return batch(argv)
