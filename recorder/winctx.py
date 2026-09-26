"""Cheap Win32 context lookups: which window/app an event happened in, display geometry.

Everything here is best effort and never raises: on other platforms, or when a
call fails, fields come back as None. Calls are chosen so they don't send
window messages (InternalGetWindowText instead of GetWindowText), so a hung
application can't stall the recorder.
"""
from __future__ import annotations

import ctypes
import os
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetDoubleClickTime.restype = wintypes.UINT
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _user32.MonitorFromPoint.restype = wintypes.HMONITOR
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    GA_ROOT = 2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    MONITOR_DEFAULTTONEAREST = 2

_process_cache: dict[int, str | None] = {}


def _title(hwnd) -> str | None:
    buf = ctypes.create_unicode_buffer(512)
    n = _user32.InternalGetWindowText(hwnd, buf, 512)
    return buf.value if n else ""


def _process_name(pid: int) -> str | None:
    if pid in _process_cache:
        return _process_cache[pid]
    name = None
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            _kernel32.CloseHandle(h)
    if len(_process_cache) > 512:
        _process_cache.clear()
    _process_cache[pid] = name
    return name


def _describe(hwnd) -> dict | None:
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = int(hwnd.value if isinstance(hwnd, wintypes.HWND) else hwnd)
    return {"hwnd": h, "title": _title(hwnd), "process": _process_name(pid.value),
            "pid": int(pid.value)}


def foreground_hwnd() -> int | None:
    """Cheap enough for an input hook: no window messages."""
    if not IS_WINDOWS:
        return None
    try:
        h = _user32.GetForegroundWindow()
        return int(h) if h else None
    except Exception:
        return None


def describe_hwnd(hwnd: int | None) -> dict | None:
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        return _describe(wintypes.HWND(hwnd))
    except Exception:
        return None


def double_click_size_px() -> list[int]:
    """The system double-click rectangle (SM_CXDOUBLECLK, SM_CYDOUBLECLK)."""
    if not IS_WINDOWS:
        return [4, 4]
    try:
        return [int(_user32.GetSystemMetrics(36)), int(_user32.GetSystemMetrics(37))]
    except Exception:
        return [4, 4]


def foreground_window() -> dict | None:
    """Top-level window with keyboard focus."""
    if not IS_WINDOWS:
        return None
    try:
        return _describe(_user32.GetForegroundWindow())
    except Exception:
        return None


def window_at(x: int, y: int) -> dict | None:
    """Top-level window under a physical screen point (where the wheel scrolls)."""
    if not IS_WINDOWS:
        return None
    try:
        hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        root = _user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
        return _describe(root or hwnd)
    except Exception:
        return None


def double_click_time_s() -> float:
    if not IS_WINDOWS:
        return 0.5
    try:
        return _user32.GetDoubleClickTime() / 1000.0
    except Exception:
        return 0.5


def monitor_scale(x: int, y: int) -> float | None:
    """Effective DPI scale (1.0 = 96 DPI) of the monitor containing a point."""
    if not IS_WINDOWS:
        return None
    try:
        shcore = ctypes.WinDLL("shcore")
        hmon = _user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        dx, dy = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            return round(dx.value / 96.0, 3)
    except Exception:
        pass
    return None


def process_dpi_awareness() -> str | None:
    if not IS_WINDOWS:
        return None
    try:
        shcore = ctypes.WinDLL("shcore")
        value = ctypes.c_int()
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(value)) == 0:
            return {0: "unaware", 1: "system", 2: "per_monitor"}.get(value.value, str(value.value))
    except Exception:
        pass
    return None
