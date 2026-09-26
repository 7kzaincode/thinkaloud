"""Quality checks on a processed trajectory.

Every flag is {"code", "severity", "detail", "source"} where severity is
"high" (privacy: must be fixed before the data leaves the machine),
"warn" (probably lowers training value) or "info". Privacy flags also carry
"subject": a hash of the content they are about (taken before redaction), so a
reviewer's dismissal is only carried over to a reprocessed recording when the flag
is about the same content.

Step checks
  missing_reasoning   no narration was aligned to this step
  idle_gap            > IDLE_GAP seconds of no actions before this step
                      (warn if silent, info if the expert was talking: thinking aloud)
  possible_email      typed text contains an email address (including text typed and then
                      deleted, and an address typed in two bursts in the same window)
  possible_secret     typed text looks like a password (see looks_like_password)
  email_in_narration  (high) the transcript of the narration contains an email address (redacted)
  drag_not_represented (warn) a drag with other input in between, or a double-click drag
  email_in_screenshot OCR found an email address in the step's screenshot (--ocr)
  masked_input        (info) the recorder masked typed characters in a password field
  redacted_value_on_screen (high) a redacted typed value may still be visible in screenshots
  email_in_context    (high) an email address in the URL, window title or element name (redacted)
  sensitive_url       (high) the page URL carries token/key/password-like parameters in the query or
                      fragment, or a token in a reset/verify/magic-link path (stripped)
  missing_after_state (warn) no screen was captured between this action and the next
  stale_before_state  (info) the "before" screen is older than expected

Session checks
  missing_task, missing_success_criteria, no_final_screenshot,
  unassigned_narration, no_narration, legacy_recording (info), recording_incomplete (warn: no end
  marker), recorder_errors (warn), audio_gaps (info: overflow padding), masked_unknown_focus (warn),
  password_masking_off (warn: UI Automation off or unavailable, password fields not detected)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path

IDLE_GAP = 20.0
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SECRET_WORDS = re.compile(r"\b(password|passcode|passphrase|pin|secret|api key|token)\b", re.I)
REDACTED = "[REDACTED]"


_subject_key: bytes = b""


def use_subject_key(session) -> None:
    """Privacy-flag subjects are keyed hashes (HMAC-SHA256). The key is random per recording and
    lives only in the recording folder (.subject-key): a subject can be compared between
    processing runs of that recording, but it can't be used to guess the typed value (a plain
    hash of an email or password could be brute-forced). Exports never contain subjects."""
    global _subject_key
    p = Path(session) / ".subject-key"
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, secrets.token_bytes(32))
        os.close(fd)
    except FileExistsError:
        pass
    _subject_key = p.read_bytes()


def _subject(text: str) -> str:
    return hmac.new(_subject_key or secrets.token_bytes(32), text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def flag(code: str, severity: str, detail: str, subject: str | None = None) -> dict:
    f = {"code": code, "severity": severity, "detail": detail, "source": "qc"}
    if subject is not None:
        f["subject"] = _subject(subject)
    return f


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


def deleted_runs(keystrokes: str) -> list[str]:
    """The pieces of text removed by Backspace, in the order they were typed ("abc⌫⌫d" -> ["bc"])."""
    buf, runs, cur = [], [], ""
    for ch in keystrokes:
        if ch == "⌫":
            if buf:
                cur = buf.pop() + cur
        else:
            if cur:
                runs.append(cur)
                cur = ""
            buf.append(ch)
    if cur:
        runs.append(cur)
    return runs


def typed_texts(action: dict) -> list[str]:
    """Every text a type step exposed: the final text, all characters typed, and each deleted run.
    Text that was typed and deleted again was still typed (and visible on screen)."""
    out = [action.get("text") or ""]
    ks = action.get("keystrokes")
    if ks:
        out.append(ks.replace("⌫", ""))
        out += deleted_runs(ks)
    return out


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

        if a.get("drag_problem"):
            s["flags"].append(flag("drag_not_represented", "warn",
                                   f"A drag ({a['drag_problem']}) ended at ({a['release']['x']}, "
                                   f"{a['release']['y']}); the Claude export can't represent it faithfully."))

        if a["type"] == "type" and a.get("masked_chars"):
            s["flags"].append(flag("masked_input", "info",
                                   f"{a['masked_chars']} character(s) typed into a password field were "
                                   "masked by the recorder and never stored."))
            unmasked = a["text"].replace("•", "")
            if not unmasked.strip():
                last_typed_email = False
                continue

        if a["type"] == "type":
            texts = typed_texts(a)
            subject = "\n".join(texts)
            if any(EMAIL_RE.search(x) for x in texts):
                s["flags"].append(flag("possible_email", "high", "Typed text contains an email address"
                                       + ("" if EMAIL_RE.search(a["text"]) else " (typed, then deleted)") + ".",
                                       subject))
                last_typed_email = True
                continue
            neighbours = " ".join(x.get("reasoning", "") for x in steps[max(0, i - 1): i + 2])
            reason = next((r for r in (looks_like_password(x, neighbours, after_email=last_typed_email)
                                       for x in texts if x) if r), None)
            if reason:
                s["flags"].append(flag("possible_secret", "high", f"Typed text may be a password: {reason}.",
                                       subject))
            last_typed_email = False
    check_split_emails(steps)


def _window_key(s: dict):
    ctx = s.get("context") or {}
    return ctx.get("hwnd") or ctx.get("window_title") or ctx.get("process")


SPLIT_WINDOW_S = 60.0


def check_split_emails(steps: list[dict]) -> None:
    """An address typed in pieces (a pause over type_gap_s splits typing into steps; a click or
    an arrow key in between) is still an address: the typing steps in one window, each within
    SPLIT_WINDOW_S of the previous one, are checked as one text."""
    run: list[dict] = []

    def scan():
        if len(run) < 2:
            return
        joined, spans, pos = "", [], 0
        for st in run:
            spans.append((pos, pos + len(st["action"]["text"]), st))
            joined += st["action"]["text"]
            pos += len(st["action"]["text"])
        for m in EMAIL_RE.finditer(joined):
            parts = [st for a, b, st in spans if a < m.end() and b > m.start()]
            if len(parts) < 2:
                continue  # inside one step: already checked
            for st in parts:
                if not any(f["code"] == "possible_email" for f in st["flags"]):
                    st["flags"].append(flag("possible_email", "high",
                                            "Typed text is part of an email address typed in more than one burst.",
                                            m.group(0)))

    for st in steps:
        a = st["action"]
        if any(f["code"] == "possible_email" for f in st["flags"]):
            # a complete address of its own: already flagged; joining it to later typing would only
            # "find" the same address again and flag unrelated text after it
            scan()
            run = []
            continue
        if a["type"] != "type" or a.get("masked_chars"):
            if run and _window_key(st) not in (None, _window_key(run[-1])):
                scan()
                run = []
            continue  # other input in the same window doesn't end the run
        if run and _window_key(run[-1]) == _window_key(st) and st["t_start"] - run[-1]["t_end"] <= SPLIT_WINDOW_S:
            run.append(st)
        else:
            scan()
            run = [st]
    scan()


def check_narration(steps: list[dict], segments: list[dict]) -> None:
    """Narration is exported verbatim and sent to the AI check: an address said aloud (and
    transcribed as one) is private data like a typed one."""
    by_id = {g["id"]: g for g in segments}
    for s in steps:
        texts = [by_id[i]["text"] for i in s.get("transcript_ids", []) if i in by_id] + [s.get("reasoning") or ""]
        if any(EMAIL_RE.search(x) for x in texts):
            s["flags"].append(flag("email_in_narration", "high",
                                   "The narration for this step contains an email address (redacted in the "
                                   "transcript and reasoning).", "\n".join(texts)))


def redact_narration(steps: list[dict], segments: list[dict]) -> int:
    n = 0
    for g in segments:
        new = EMAIL_RE.sub(REDACTED, g.get("text") or "")
        if new != g.get("text"):
            g["text"], n = new, n + 1
    for s in steps:
        if s.get("reasoning"):
            s["reasoning"] = EMAIL_RE.sub(REDACTED, s["reasoning"])
    return n


def check_session(meta: dict, steps: list[dict], segments: list[dict],
                  final_screenshot: str | None, legacy: bool = False) -> list[dict]:
    out = []
    ev_end = meta.get("_has_end_marker")
    if not legacy and ev_end is False:
        out.append(flag("recording_incomplete", "warn",
                        "No end marker: the recorder was stopped abnormally and the last events may be missing."))
    if meta.get("errors"):
        out.append(flag("recorder_errors", "warn", "; ".join(map(str, meta["errors"]))[:300]))
    audio = meta.get("audio") or {}
    if audio.get("overflows"):
        out.append(flag("audio_gaps", "info", f"{audio['overflows']} audio buffer overflow(s); "
                                              f"{audio.get('overflow_padding_s', 0)} s of silence inserted to keep sync."))
    if (meta.get("input") or {}).get("masked_focus_unknown"):
        out.append(flag("masked_unknown_focus", "warn",
                        f"{meta['input']['masked_focus_unknown']} typed character(s) masked because the focused "
                        "field could not be checked; the typed text for those steps is unknown."))
    masking = (meta.get("input") or {}).get("password_masking")
    typed = any(s["action"]["type"] == "type" and (s["action"].get("text") or "").strip("•") for s in steps)
    if not legacy and masking in ("off", "unavailable"):
        out.append(flag("password_masking_off", "high" if typed else "warn",
                        "Password fields could not be detected (UI Automation "
                        f"{'was turned off' if masking == 'off' else 'did not start'}): anything typed into a password "
                        "field was stored as typed. QC redacts likely passwords, but check the typed text."))
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


# parameter names are compared lowercased with separators removed (accessToken == access_token);
# a name is secret-like when it ENDS with one of these (so "sessionTitle" and "tokenizer" are not)
SENSITIVE_ENDINGS = ("token", "secret", "password", "passwd", "pwd", "apikey", "accesskey", "privatekey",
                     "sessionid", "sessid", "session", "jwt", "signature", "credential", "credentials", "verifier",
                     "authcode", "authorization", "samlresponse", "samlrequest", "rlkey", "secretkey", "clientkey")
SENSITIVE_NAMES = {"auth", "code", "sig", "key", "pin", "otp", "sid", "pass", "hash", "ticket"}
TOKEN_PATH_BEFORE = {"reset", "resetpassword", "passwordreset", "verify", "verification", "confirm", "activate",
                     "activation", "magic", "magiclink", "invite", "invitation", "token", "unsubscribe", "auth", "login"}
JWT_RE = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]*$")


def _sensitive_name(name: str) -> bool:
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    return n in SENSITIVE_NAMES or n.endswith(SENSITIVE_ENDINGS)


def _token_like(seg: str) -> bool:
    """Random-looking: letters and digits mixed (a UUID, a hex or base64 token, "7F3K9Q"), not words."""
    return (len(seg) >= 6 and re.fullmatch(r"[A-Za-z0-9_-]+", seg) is not None
            and any(c.isdigit() for c in seg) and any(c.isalpha() for c in seg))


def sanitize_url(url: str, _depth: int = 0) -> tuple[str, bool]:
    """(url with secrets removed, whether anything sensitive was found). Query and fragment
    parameters with secret-like names, tokens in magic-link style paths, and JWTs anywhere in
    the path are replaced."""
    from urllib.parse import parse_qsl, urlsplit, urlunsplit

    u = urlsplit(url)
    found = False
    netloc = u.netloc
    if "@" in netloc:  # user:password@host
        netloc, found = netloc.rsplit("@", 1)[1], True
    params = parse_qsl(u.query, keep_blank_values=True) + parse_qsl(u.fragment, keep_blank_values=True)
    if any(_sensitive_name(k) or JWT_RE.match(v or "") for k, v in params):
        found = True
    elif _depth < 2 and any(("?" in v or "#" in v or "@" in v) and sanitize_url(v, _depth + 1)[1] for _, v in params):
        found = True  # e.g. ?next=/account?token=... (a redirect target carrying a secret)
    segs = u.path.split("/")
    for i, seg in enumerate(segs):
        prev = re.sub(r"[^a-z0-9]", "", segs[i - 1].lower()) if i else ""
        if JWT_RE.match(seg) or (prev in TOKEN_PATH_BEFORE and _token_like(seg)):
            segs[i], found = REDACTED, True
    if not found:
        return url, False
    return urlunsplit((u.scheme, netloc, "/".join(segs), REDACTED if u.query else "",
                       REDACTED if u.fragment else "")), True


def check_context(steps: list[dict]) -> None:
    """URLs, window titles and element names end up in exports too."""
    for s in steps:
        tgt, ctx = s.get("target") or {}, s.get("context") or {}
        texts = [tgt.get("url") or "", tgt.get("name") or "", ctx.get("window_title") or ""]
        if any(EMAIL_RE.search(x) for x in texts):
            s["flags"].append(flag("email_in_context", "high",
                                   "An email address appears in the page URL, window title or element name.",
                                   "\n".join(texts)))
        url = tgt.get("url")
        if url and sanitize_url(url)[1]:
            s["flags"].append(flag("sensitive_url", "high",
                                   "The page URL has token/key/password-like parameters or a token in its path.",
                                   url))


def redact(steps: list[dict]) -> int:
    """Replace typed text flagged as email/secret. Returns how many steps changed."""
    n = 0
    for s in steps:
        codes = {f["code"] for f in s["flags"]}
        tgt, ctx = s.get("target") or {}, s.get("context") or {}
        if "email_in_context" in codes:
            for d, k in ((tgt, "url"), (tgt, "name"), (ctx, "window_title")):
                if d.get(k):
                    d[k] = EMAIL_RE.sub(REDACTED, d[k])
        if "sensitive_url" in codes and tgt.get("url"):
            tgt["url"] = sanitize_url(tgt["url"])[0]
        if s["action"]["type"] == "type" and codes & {"possible_email", "possible_secret"}:
            subject = next(f.get("subject") for f in s["flags"] if f["code"] in ("possible_email", "possible_secret"))
            s["action"]["text"] = REDACTED
            s["action"]["redacted"] = True
            s["action"].pop("keystrokes", None)       # the raw sequence would give the value back
            f = flag("redacted_value_on_screen", "high",
                     "The typed value was removed from the action, but the field (and so the after-state and later "
                     "screenshots) may still show it. Check the images before sharing; run with --ocr to scan for "
                     "emails.")
            if subject:
                f["subject"] = subject
            s["flags"].append(f)
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
                                   f"{len(emails)} email address(es) visible in screenshot.", "\n".join(emails)))
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
        ("email_in_context", "email in URL/title"),
        ("sensitive_url", "sensitive URL"),
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
