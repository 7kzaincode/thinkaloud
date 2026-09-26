"""Plain-English descriptions of steps ("Clicked the Add to Cart button").

A UI Automation target is only used when the lookup succeeded, returned a name,
and finished within TARGET_MAX_LATENCY_MS of the click; otherwise the description
falls back to coordinates (the raw target metadata is kept either way).
Mirrors viewer/lib/format.ts.
"""
from __future__ import annotations

TARGET_MAX_LATENCY_MS = 500
ROLE_WORDS = {"edit": "field", "combobox": "dropdown", "list item": "item", "tab item": "tab",
              "data item": "row", "tree item": "item", "split button": "button"}
KEY_LABELS = {"enter": "Enter", "tab": "Tab", "esc": "Esc", "backspace": "Backspace", "delete": "Delete",
              "up": "Up", "down": "Down", "left": "Left", "right": "Right", "page_up": "Page Up",
              "page_down": "Page Down", "home": "Home", "end": "End", "space": "Space",
              "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}


def target_reliable(target: dict | None) -> bool:
    """Looked up in time, and not a guess between overlapping elements (see recorder/uia.py _descend)."""
    return bool(target and target.get("status") == "ok" and not target.get("ambiguous")
                and (target.get("latency_ms") or 0) <= TARGET_MAX_LATENCY_MS)


def key_label(combo: str) -> str:
    # "ctrl++" is Ctrl and the + key, not Ctrl and two empty keys
    if combo.endswith("+") and (combo == "+" or combo.endswith("++")):
        head = combo[:-1].rstrip("+")
        parts = (head.split("+") if head else []) + ["+"]
    else:
        parts = combo.split("+")
    return "+".join(KEY_LABELS.get(p, p.upper() if len(p) == 1 else p.replace("_", " ").title())
                    for p in parts)


def describe(step: dict) -> str:
    a = step["action"]
    t = a["type"]
    if t == "click" and a.get("drag_problem") and a.get("release"):
        r = a["release"]
        start = "Double-clicked and dragged" if a.get("count", 1) > 1 else "Dragged"
        return f"{start} from ({a['x']}, {a['y']}) to ({r['x']}, {r['y']}) ({a['drag_problem']})"
    if t == "click":
        verb = {2: "Double-clicked", 3: "Triple-clicked"}.get(a.get("count", 1), "Clicked")
        if a.get("button") == "right":
            verb = "Right-clicked"
        elif a.get("button") == "middle":
            verb = "Middle-clicked"
        if a.get("mods"):
            verb = f"{'+'.join(key_label(m) for m in a['mods'])}+{verb.lower()}"
        tgt = step.get("target")
        if target_reliable(tgt):
            name = (tgt.get("name") or "").strip()
            role = ROLE_WORDS.get(tgt.get("role") or "", tgt.get("role") or "element")
            if name:
                return f"{verb} the {name[:80]} {role}"
            if role not in ("pane", "custom", "group", "document", "window", "unknown"):
                return f"{verb} a {role} at ({a['x']}, {a['y']})"
        return f"{verb} at ({a['x']}, {a['y']})"
    if t == "drag":
        verb = "Right-dragged" if a.get("button") == "right" else "Dragged"
        tgt = step.get("target")
        if target_reliable(tgt) and (tgt.get("name") or "").strip():
            role = ROLE_WORDS.get(tgt.get("role") or "", tgt.get("role") or "element")
            return f"{verb} the {tgt['name'].strip()[:80]} {role} to ({a['x2']}, {a['y2']})"
        return f"{verb} from ({a['x']}, {a['y']}) to ({a['x2']}, {a['y2']})"
    if t == "type":
        if a.get("redacted"):
            return "Typed text (redacted)"
        if a.get("masked_chars") and a["masked_chars"] == len(a["text"]):
            return f"Typed {a['masked_chars']} characters into a password field (masked)"
        if not a["text"] and a.get("backspaces"):
            # never quote deleted text: it is often a mistyped password or address
            n = len((a.get("keystrokes") or "").replace("⌫", "")) or a["backspaces"]
            return f"Typed {n} character{'s' if n != 1 else ''} and deleted {'them' if n != 1 else 'it'}"
        return f'Typed "{a["text"]}"'
    if t == "key":
        n = a.get("repeat", 1)
        return f"Pressed {key_label(a['key'])}" + (f" ×{n}" if n > 1 else "")
    if t == "scroll":
        runs = [r for r in a.get("runs", []) if r["direction"] != "none"]
        if not runs:
            return "Scrolled"
        parts = [f"{r['direction']} {r['amount']:g}" for r in runs]
        return "Scrolled " + ", then ".join(parts)
    return t
