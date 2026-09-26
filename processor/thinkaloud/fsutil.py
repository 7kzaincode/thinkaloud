"""File helpers that survive Windows' file locking.

On Windows os.replace fails with PermissionError while any other process (the viewer
polling status files, the browser streaming playback.mp4) has the destination open.
Retry briefly instead of failing the whole run."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path


def replace_retry(src: Path, dst: Path, attempts: int = 40, delay: float = 0.05) -> None:
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (1 + i // 10))


def write_json_atomic(path: Path, data, indent: int | None = 2) -> None:
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    try:
        replace_retry(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
