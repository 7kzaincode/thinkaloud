"""Hashes of what an AI evaluation judged. Must match viewer/lib/hash.ts exactly
(FNV-1a 32-bit over UTF-8, 8 hex chars), so the browser can tell when a human edit
made an evaluation stale."""
from __future__ import annotations


def fnv1a(text: str) -> str:
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def js_num(x) -> str:
    """Number formatting as JavaScript's String(x) for the values we use (seconds)."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return repr(x) if isinstance(x, float) else str(x)


def step_key(s: dict) -> str:
    """viewer/lib/review.ts stepKey()"""
    return s["uid"] if s.get("uid") else f"t{js_num(s['t_start'])}|{s['action']['type']}"


def narration_input(s: dict) -> str:
    uid = s["uid"] if s.get("uid") is not None else js_num(s["id"])
    return fnv1a(f"{uid}\n{s.get('description') or ''}\n{s.get('reasoning') or ''}")


def final_check_input(item_text: str, criteria: str, final_file: str | None) -> str:
    return fnv1a(f"{item_text}\n{criteria}\n{final_file or ''}")
