"""Single entry point for the desktop app's Python side.

    engine record    [record.py args]    e.g. --json --task ... --criteria ... --device 1
    engine process   [thinkaloud args]   e.g. --json <session_dir>
    engine export    <sessions...> --out DIR [--formats dataset,claude] [--include-media]
    engine validate  <bundle dir or .zip>
    engine review    --session DIR --kind narration|checklist|final_screen   (JSON on stdin)
    engine ai-status
    engine batch     <sessions...> --jobs DIR [--concurrency N] [--force] [--retries N]

In development the app runs this with the repo's .venv. For the installer it is
frozen with PyInstaller (see engine/build.ps1) so users don't need Python.
"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)  # PyInstaller unpack dir; modules are bundled
else:
    ROOT = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(ROOT / "recorder"), str(ROOT / "processor")]


def main() -> int:
    commands = ("record", "process", "export", "validate", "review", "ai-status", "batch")
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"usage: engine {{{'|'.join(commands)}}} [args...]", file=sys.stderr)
        return 2
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "record":
        import record

        record.main(rest)
        return 0
    if cmd == "process":
        from thinkaloud.__main__ import main as process_main

        return process_main(rest)
    from thinkaloud import commands

    return {"export": commands.export_main, "validate": commands.validate_main,
            "review": commands.review_main, "ai-status": commands.ai_status_main,
            "batch": commands.batch_main}[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
