"""Best-effort Windows UI Automation lookups for what the expert clicked or typed into.

Runs on its own thread (UIAWorker) so the input hooks never wait on another
process. Every UIA call is bounded by UIA's own connection/transaction timeouts,
and a request that sat in the queue too long is answered "skipped_stale"
instead of being looked up, so a click is never paired with whatever appeared
after it (e.g. the next page after a navigation).

Nothing is inferred: names, roles and rectangles come from UIA; a URL is only
reported when the enclosing Document element exposes a value that parses as an
http(s)/file URL. Otherwise the field is absent and `url_status` says why.
"""
from __future__ import annotations

import queue
import threading
import time
from urllib.parse import urlparse

CONTROL_TYPES = {
    50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox", 50004: "edit",
    50005: "link", 50006: "image", 50007: "list item", 50008: "list", 50009: "menu",
    50010: "menu bar", 50011: "menu item", 50012: "progress bar", 50013: "radio button",
    50014: "scroll bar", 50015: "slider", 50016: "spinner", 50017: "status bar", 50018: "tab",
    50019: "tab", 50020: "text", 50021: "toolbar", 50022: "tooltip", 50023: "tree",
    50024: "tree item", 50025: "custom", 50026: "group", 50027: "thumb", 50028: "data grid",
    50029: "data item", 50030: "document", 50031: "split button", 50032: "window",
    50033: "pane", 50034: "header", 50035: "header item", 50036: "table", 50037: "title bar",
    50038: "separator", 50039: "semantic zoom", 50040: "app bar",
}
CONTROL_TYPES[50019] = "tab item"
INTERACTIVE = {50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50015, 50016,
               50019, 50024, 50029, 50031, 50035}
DOCUMENT = 50030
VALUE_PATTERN = 10002
MAX_TARGET_HOPS = 4     # text/image inside a button: climb at most this far
MAX_DOCUMENT_HOPS = 40  # climbing to the page's Document element for the URL
CONTAINERS = {50025, 50026, 50030, 50032, 50033}  # custom, group, document, window, pane
DESCEND_BUDGET_S = 0.15
DESCEND_MAX_DEPTH = 20
DESCEND_MAX_NODES = 400


def _rect(r) -> list[int] | None:
    try:
        return [int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)]
    except Exception:
        return None


class UIA:
    """Thin comtypes wrapper. Construct on the thread that will use it."""

    def __init__(self, timeout_ms: int = 600):
        import sys

        # comtypes initializes COM for the importing thread at import time; ask for
        # the multithreaded apartment (recommended for UIA clients) before that.
        sys.coinit_flags = 0  # COINIT_MULTITHREADED
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # this thread already has a COM apartment; UIA works in either
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        self.mod = mod
        try:
            self.auto = comtypes.client.CreateObject(mod.CUIAutomation8, interface=mod.IUIAutomation2)
            self.auto.ConnectionTimeout = timeout_ms
            self.auto.TransactionTimeout = timeout_ms
            self.bounded = True
        except Exception:  # pre-Windows 8: no timeouts available
            self.auto = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
            self.bounded = False
        self.walker = self.auto.ControlViewWalker

    def _props(self, el) -> dict:
        def get(attr, default=None):
            try:
                return getattr(el, attr)
            except Exception:
                return default

        ct = get("CurrentControlType")
        return {
            "role": CONTROL_TYPES.get(ct, get("CurrentLocalizedControlType") or "unknown"),
            "control_type_id": ct,
            "name": get("CurrentName") or "",
            "automation_id": get("CurrentAutomationId") or "",
            "class_name": get("CurrentClassName") or "",
            "framework": get("CurrentFrameworkId") or "",
            "rect": _rect(get("CurrentBoundingRectangle")),
            "is_password": bool(get("CurrentIsPassword", False)),
            "pid": get("CurrentProcessId"),
        }

    def _parent(self, el):
        try:
            p = self.walker.GetParentElement(el)
            return p if p else None  # comtypes returns a NULL pointer, not None, at the root
        except Exception:
            return None

    def _document_url(self, el) -> tuple[str | None, str]:
        cur, hops = el, 0
        while cur is not None and hops < MAX_DOCUMENT_HOPS:
            try:
                if cur.CurrentControlType == DOCUMENT:
                    pat = cur.GetCurrentPattern(VALUE_PATTERN)
                    if not pat:
                        return None, "document_has_no_value"
                    value = pat.QueryInterface(self.mod.IUIAutomationValuePattern).CurrentValue or ""
                    u = urlparse(value.strip())
                    if u.scheme in ("http", "https", "file") and (u.netloc or u.scheme == "file"):
                        return value.strip(), "ok"
                    return None, "document_value_not_a_url"
            except Exception:
                return None, "error"
            cur, hops = self._parent(cur), hops + 1
        return None, "no_document"

    def _descend(self, el, x: int, y: int):
        """Chromium sometimes answers a hit test with its Document root (seen right after the
        window was activated). Walk down by bounding rectangle to the deepest on-screen element
        containing the point. Bounded by time and node count; returns None if nothing deeper."""
        deadline = time.perf_counter() + DESCEND_BUDGET_S
        cur, nodes = el, 0
        for _depth in range(DESCEND_MAX_DEPTH):
            best, best_area = None, None
            try:
                child = self.walker.GetFirstChildElement(cur)
            except Exception:
                break
            while child and nodes < DESCEND_MAX_NODES and time.perf_counter() < deadline:
                nodes += 1
                try:
                    r = child.CurrentBoundingRectangle
                    if not child.CurrentIsOffscreen and r.left <= x < r.right and r.top <= y < r.bottom:
                        area = (r.right - r.left) * (r.bottom - r.top)
                        if best is None or area <= best_area:
                            best, best_area = child, area
                    child = self.walker.GetNextSiblingElement(child)
                except Exception:
                    break
            if best is None:
                break
            cur = best
        return cur if cur is not el else None

    def at_point(self, x: int, y: int) -> dict:
        el = self.auto.ElementFromPoint(self.mod.tagPOINT(int(x), int(y)))
        if not el:
            return {"status": "not_found"}
        hit = self._props(el)
        method = "point"
        if hit["control_type_id"] in CONTAINERS:
            deeper = self._descend(el, int(x), int(y))
            if deeper is not None:
                el, hit, method = deeper, self._props(deeper), "descend"
        target_el, target = el, hit
        cur, hops = el, 0
        while hit["control_type_id"] not in INTERACTIVE and hops < MAX_TARGET_HOPS:
            cur = self._parent(cur)
            if cur is None:
                break
            hops += 1
            p = self._props(cur)
            if p["control_type_id"] in INTERACTIVE:
                target_el, target = cur, p
                break
        url, url_status = self._document_url(target_el)
        out = {"status": "ok", **target, "hit": hit if target_el is not el else None,
               "hit_method": method, "url_status": url_status}
        if url:
            out["url"] = url
        return out

    def focused(self) -> dict:
        el = self.auto.GetFocusedElement()
        if not el:
            return {"status": "not_found"}
        return {"status": "ok", **self._props(el)}


class UIAWorker(threading.Thread):
    """Answers lookups in order. `submit` never blocks; `result` waits (bounded)."""

    def __init__(self, clock, timeout_ms: int = 600, max_queue_age_s: float = 0.35):
        super().__init__(daemon=True, name="uia")
        self.clock = clock
        self.timeout_ms = timeout_ms
        self.max_queue_age_s = max_queue_age_s
        self.q: queue.Queue = queue.Queue()
        self.results: dict[int, dict] = {}
        self.cv = threading.Condition()
        self.available: bool | None = None
        self.error: str | None = None
        self.ready = threading.Event()

    def run(self) -> None:
        try:
            uia = UIA(self.timeout_ms)
            self.available = True
        except Exception as e:  # comtypes missing, not Windows, COM failure
            self.available, self.error = False, f"{type(e).__name__}: {e}"
            uia = None
        self.ready.set()
        while True:
            item = self.q.get()
            if item is None:
                return
            rid, kind, t_event, args = item
            started = self.clock()
            if uia is None:
                res = {"status": "unavailable", "error": self.error}
            elif started - t_event > self.max_queue_age_s:
                res = {"status": "skipped_stale"}
            else:
                try:
                    res = uia.at_point(*args) if kind == "point" else uia.focused()
                except Exception as e:
                    msg = str(e)
                    status = "timeout" if ("timeout" in msg.lower() or "0x80131505" in msg) else "error"
                    res = {"status": status, "error": f"{type(e).__name__}: {msg[:200]}"}
            done = self.clock()
            res["source"] = "uia"
            res["lookup_started_t"] = round(started, 4)
            res["lookup_done_t"] = round(done, 4)
            res["latency_ms"] = round((done - t_event) * 1000, 1)
            with self.cv:
                self.results[rid] = res
                self.cv.notify_all()

    _next = 0
    _lock = threading.Lock()

    def submit(self, kind: str, t_event: float, *args) -> int:
        with self._lock:
            UIAWorker._next += 1
            rid = UIAWorker._next
        self.q.put((rid, kind, t_event, args))
        return rid

    def result(self, rid: int, timeout_s: float = 1.5) -> dict:
        deadline = time.monotonic() + timeout_s
        with self.cv:
            while rid not in self.results:
                left = deadline - time.monotonic()
                if left <= 0:
                    return {"status": "timeout", "source": "uia", "error": "no answer within wait budget"}
                self.cv.wait(left)
            return self.results.pop(rid)

    def stop(self) -> None:
        self.q.put(None)
