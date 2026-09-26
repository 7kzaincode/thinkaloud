"""Quality checks on a processed trajectory.

Every flag is {"code", "severity", "detail", "source"} where severity is
"high" (privacy: must be fixed before the data leaves the machine),
"warn" (probably lowers training value) or "info".

Step checks
  missing_reasoning   no narration was aligned to this step
  idle_gap            > IDLE_GAP seconds of no actions before this step
                      (warn if silent, info if the expert was talking: thinking aloud)
  possible_email      typed text contains an email address
  possible_secret     typed text looks like a password (see looks_like_password)
  email_in_screenshot OCR found an email address in the step's screenshot (--ocr)
  masked_input        (info) the recorder masked typed characters in a password field
  redacted_value_on_screen (high) a redacted typed value may still be visible in screenshots
  missing_after_state (warn) no screen was captured between this action and the next
  stale_before_state  (info) the "before" screen is older than expected

Session checks
  missing_task, missing_success_criteria, no_final_screenshot,
  unassigned_narration, no_narration, legacy_recording (info)
"""
from __future__ import annotations

import re

IDLE_GAP = 20.0
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SECRET_WORDS = re.compile(r"\b(password|passcode|passphrase|pin|secret|api key|token)\b", re.I)
REDACTED = "[REDACTED]"


def flag(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail, "source": "qc"}


def char_classes(s: str) -> int:
    return sum([any(c.islower() for c in s), any(c.isupper() for c in s),
                any(c.isdigit() for c in s), any(not c.isalnum() for c in s)])


def looks_like_password(text: str, context: str = "", after_email: bool = False) -> str | None:
    """Return a reason string if `text` looks like a password, else None.

    A single token (no spaces), 6-64 chars, not an email, plus ONE of:
      * mixes >= 3 character classes (e.g. 'Hunter2!x')
      * narration nearby mentions a password / pin / secret
      * it was typed right after an email address (login form pattern)
    """
    t = text.strip()
    if not (6 <= len(t) <= 64) or " " in t or EMAIL_RE.fullmatch(t):
        return None
    if len(t) >= 8 and char_classes(t) >= 3:
        return "single token mixing letters, digits and symbols"
    if SECRET_WORDS.search(context):
        return "narration mentions a password or secret"
    if after_email:
        return "typed right after an email address (login form?)"
    return None


def paused_between(pauses: list, a: float, b: float) -> float:
    total = 0.0
    for p0, p1 in pauses or []:
        if p0 is None:
            continue
        p1 = b if p1 is None else p1
        total += max(0.0, min(b, p1) - max(a, p0))
    return total


def check_steps(steps: list[dict], segments: list[dict], pauses: list | None = None,
                legacy: bool = False) -> None:
    """Mutates each step's 'flags' list."""
    prev_end = 0.0
    last_typed_email = False
    for i, s in enumerate(steps):
        s.setdefault("flags", [])
        a = s["action"]

        if not s.get("reasoning", "").strip():
            s["flags"].append(flag("missing_reasoning", "warn", "No narration aligned to this step."))

        gap = s["t_start"] - prev_end - paused_between(pauses, prev_end, s["t_start"])
        if gap > IDLE_GAP:
            talked = any(g["t_end"] > prev_end and g["t_start"] < s["t_start"] for g in segments)
            s["flags"].append(flag(
                "idle_gap", "info" if talked else "warn",
                f"{gap:.1f}s with no actions before this step"
                + (" (expert was narrating)" if talked else " (silent)")))
        prev_end = max(prev_end, s["t_end"])

        obs = s.get("observations") or {}
        if not legacy and (obs.get("after") or {}).get("status") == "missing":
            s["flags"].append(flag("missing_after_state", "warn",
                                   "No screen was captured between this action and the next "
                                   f"({(obs['after'].get('reason') or 'unknown reason')})."))
        if (obs.get("before") or {}).get("status") == "stale":
            s["flags"].append(flag("stale_before_state", "info",
                                   "The 'before' screen was captured well before this action."))

        if a["type"] == "type" and a.get("masked_chars"):
            s["flags"].append(flag("masked_input", "info",
                                   f"{a['masked_chars']} character(s) typed into a password field were "
                                   "masked by the recorder and never stored."))
            unmasked = a["text"].replace("•", "")
            if not unmasked.strip():
                last_typed_email = False
                continue

        if a["type"] == "type":
            text = a["text"]
            if EMAIL_RE.search(text):
                s["flags"].append(flag("possible_email", "high", "Typed text contains an email address."))
                last_typed_email = True
                continue
            neighbours = " ".join(x.get("reasoning", "") for x in steps[max(0, i - 1): i + 2])
            reason = looks_like_password(text, neighbours, after_email=last_typed_email)
            if reason:
                s["flags"].append(flag("possible_secret", "high", f"Typed text may be a password: {reason}."))
            last_typed_email = False


def check_session(meta: dict, steps: list[dict], segments: list[dict],
                  final_screenshot: str | None, legacy: bool = False) -> list[dict]:
    out = []
    if legacy:
        out.append(flag("legacy_recording", "info",
                        "Recorded with recorder 0.1: no after-state screenshots, no video, "
                        "no click targets; before-screenshots were grabbed at action time."))
    if not meta.get("task"):
        out.append(flag("missing_task", "warn", "No task description was recorded."))
    if not meta.get("success_criteria"):
        out.append(flag("missing_success_criteria", "warn",
                        "No 'done when' criteria: a reviewer can't judge the outcome."))
    if not final_screenshot:
        out.append(flag("no_final_screenshot", "warn", "No end-state screenshot to verify the outcome."))
    if not segments:
        out.append(flag("no_narration", "warn", "No transcript: every step lacks reasoning."))
    loose = [g for g in segments if g.get("step_id") is None]
    if loose:
        out.append(flag("unassigned_narration", "info",
                        f"{len(loose)} transcript segment(s) came before any action."))
    return out


def redact(steps: list[dict]) -> int:
    """Replace typed text flagged as email/secret. Returns how many steps changed."""
    n = 0
    for s in steps:
        codes = {f["code"] for f in s["flags"]}
        if s["action"]["type"] == "type" and codes & {"possible_email", "possible_secret"}:
            s["action"]["text"] = REDACTED
            s["action"]["redacted"] = True
            s["flags"].append(flag(
                "redacted_value_on_screen", "high",
                "The typed value was removed from the action, but the field (and so the after-state and later "
                "screenshots) may still show it. Check the images before sharing; run with --ocr to scan for emails."))
            n += 1
    return n


def ocr_emails(steps: list[dict], session_dir) -> int:
    """Stretch goal: OCR each screenshot once and flag steps showing an email."""
    import pytesseract
    from PIL import Image

    found: dict[str, list[str]] = {}
    def shots(s):
        obs = s.get("observations") or {}
        files = {(obs.get(k) or {}).get("file") for k in ("before", "after")} | {s.get("screenshot")}
        return {f for f in files if f}

    for shot in {f for s in steps for f in shots(s)}:
        text = pytesseract.image_to_string(Image.open(session_dir / shot))
        found[shot] = sorted(set(EMAIL_RE.findall(text)))
    n = 0
    for s in steps:
        emails = sorted({e for f in shots(s) for e in found.get(f, [])})
        if emails:
            s["flags"].append(flag("email_in_screenshot", "high",
                                   f"{len(emails)} email address(es) visible in screenshot."))
            n += 1
    return n


def summarize(steps: list[dict], session_flags: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for f in [f for s in steps for f in s["flags"]] + session_flags:
        counts[f["code"]] = counts.get(f["code"], 0) + 1
    labels = [
        ("missing_reasoning", "missing reasoning"),
        ("idle_gap", "idle gap"),
        ("possible_secret", "possible secret"),
        ("possible_email", "typed email"),
        ("redacted_value_on_screen", "redacted value on screen"),
        ("email_in_screenshot", "email on screen"),
        ("missing_after_state", "missing after-state"),
        ("masked_input", "masked input"),
        ("missing_success_criteria", "no success criteria"),
        ("no_final_screenshot", "no final screenshot"),
    ]
    parts = [f"{len(steps)} step{'' if len(steps) == 1 else 's'}"] + [
        f"{counts[c]} {label}{'s' if counts[c] > 1 and label.endswith(('gap', 'secret', 'email')) else ''}"
        for c, label in labels if counts.get(c)]
    return {"summary": ", ".join(parts), "counts": counts,
            "high_severity": sum(f["severity"] == "high" for s in steps for f in s["flags"])}
