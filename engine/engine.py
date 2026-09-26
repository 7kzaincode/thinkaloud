"""Single entry point for the desktop app's Python side.

    engine record  [record.py args]      e.g. --json --task ... --criteria ... --device 1
    engine process [thinkaloud args]     e.g. --json <session_dir>

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
    if len(sys.argv) < 2 or sys.argv[1] not in ("record", "process"):
        print("usage: engine {record|process} [args...]", file=sys.stderr)
        return 2
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "record":
        import record

        record.main(rest)
        return 0
    from thinkaloud.__main__ import main as process_main

    return process_main(rest)


if __name__ == "__main__":
    sys.exit(main())
