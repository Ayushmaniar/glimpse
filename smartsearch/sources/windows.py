"""Open windows: live search over the top-level app windows on screen (Windows only)."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes

import numpy as np

from ..embedder import Embedder, doc
from .base import Result, Source

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
GW_OWNER, DWMWA_CLOAKED = 4, 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE, VK_MENU, KEYEVENTF_KEYUP = 9, 0x12, 0x0002
OWN_TITLE = "SmartSearch"  # never list the launcher's own window
CACHE_MAX = 500

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _cloaked(hwnd: int) -> bool:
    """UWP apps leave cloaked ghost windows behind; they are not on screen."""
    val = ctypes.c_int(0)
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return hr == 0 and val.value != 0


def _is_app_window(hwnd: int) -> bool:
    if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
        return False
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    app_window = bool(ex & WS_EX_APPWINDOW)
    if (ex & WS_EX_TOOLWINDOW) and not app_window:
        return False
    return app_window or not user32.GetWindow(hwnd, GW_OWNER)


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def _app_label(hwnd: int) -> str:
    """'chrome.exe' -> 'Chrome', taken from the owning process's image path."""
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        stem = os.path.splitext(os.path.basename(buf.value))[0]
        return stem[:1].upper() + stem[1:]
    finally:
        kernel32.CloseHandle(handle)


def _windows() -> list[tuple[int, str, str]]:
    """(hwnd, title, app) for each visible top-level app window that has a title."""
    out: list[tuple[int, str, str]] = []

    def visit(hwnd, _lparam):
        try:
            if _is_app_window(hwnd):
                title = _title(hwnd)
                if title and title != OWN_TITLE:
                    out.append((int(hwnd), title, _app_label(hwnd)))
        except Exception:  # a window that vanishes mid-enumeration is skipped
            pass
        return True

    callback = EnumProc(visit)  # keep a reference alive for the duration of the call
    user32.EnumWindows(callback, 0)
    return out


class WindowsSource(Source):
    name = "windows"
    label = "Open windows"
    panel = "list"
    intent = ("Switch to a window or app that is already open on screen, like the browser tab, chat, "
              "email or document I was just working in")
    indexed = False  # live: windows change too fast to index
    supports_image_query = False
    floor = 0.72   # unrelated titles reached 0.73 in testing
    strong = 0.85

    def __init__(self, store):
        super().__init__(store)
        self._cache: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    def search(self, emb: Embedder, qvec: np.ndarray, text: str | None, k: int = 8) -> list[Result]:
        wins = _windows()
        if not wins:
            return []
        texts = [f"{title} — {app}" if app else title for _, title, app in wins]
        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
            if missing:
                for t, v in zip(missing, emb.texts([doc(t, None) for t in missing], urgent=True)):
                    self._cache[t] = v
            mat = np.vstack([self._cache[t] for t in texts])
            while len(self._cache) > CACHE_MAX:  # evict the oldest; current titles were just used
                self._cache.pop(next(iter(self._cache)))
        scores = mat @ qvec[: mat.shape[1]]
        order = np.argsort(-scores)[:k]
        return [Result(id=f"{self.name}:{wins[i][0]}", source=self.name, title=wins[i][1],
                       subtitle=wins[i][2], score=float(scores[i]), icon="🗔", kind="window") for i in order]

    def run(self, result_id: str, op: str = "open") -> dict:
        try:
            hwnd = int(result_id.split(":", 1)[1])
        except ValueError:
            return {"ok": False, "error": "bad window id"}
        if not user32.IsWindow(hwnd):
            return {"ok": False, "error": "window is gone"}
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # Windows only lets a background process take focus soon after it has sent input.
        # A tap of Alt counts as input, so it is sent just before SetForegroundWindow.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        if not user32.SetForegroundWindow(hwnd):
            return {"ok": False, "error": "Windows did not allow switching to this window"}
        return {"ok": True}
