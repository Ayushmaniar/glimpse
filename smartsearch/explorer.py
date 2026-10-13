"""Which folder is open in the File Explorer window you were in when you pressed the hotkey.

Opening SmartSearch from Explorer offers "search this folder", like Explorer's own search box.
The desktop counts as the Desktop folder. Explorer pages that aren't folders (Home, This PC,
Gallery, Network) give None.

Windows 11 puts several tabs in one Explorer window and every tab reports the same window handle,
so the active tab is found by asking each tab's shell browser for its own tab window and comparing
it with the tab window on top (falling back to the tab whose folder name is the window title).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os

from . import config

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
EXPLORER = {"CabinetWClass", "ExploreWClass"}
DESKTOP = {"Progman", "WorkerW"}


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def is_explorer(hwnd: int) -> bool:
    return bool(hwnd) and _class(hwnd) in EXPLORER | DESKTOP


def folder_of(hwnd: int) -> str | None:
    """The file-system folder shown in this Explorer window (or the Desktop), else None.
    Talks to Explorer over COM, so call it off the UI thread (~20-80 ms)."""
    if not hwnd:
        return None
    cls = _class(hwnd)
    if cls not in EXPLORER | DESKTOP:
        return None
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        if cls in DESKTOP:
            return _fs(shell.Namespace(0).Self.Path)  # ssfDESKTOP
        wins = shell.Windows()
        tabs = [w for w in (_safe(lambda i=i: wins.Item(i)) for i in range(int(wins.Count)))
                if w is not None and _safe(lambda w=w: int(w.HWND)) == hwnd]
        if not tabs:
            return None
        tab = _active(tabs, hwnd)
        return _fs(tab.Document.Folder.Self.Path)
    except Exception:
        log.debug("explorer folder lookup failed", exc_info=True)
        return None
    finally:
        comtypes.CoUninitialize()


def _active(tabs: list, hwnd: int):
    if len(tabs) == 1:
        return tabs[0]
    top = user32.FindWindowExW(hwnd, None, "ShellTabWindowClass", None)  # the visible tab is first in z-order
    if top:
        for t in tabs:
            if _tab_window(t) == top:
                return t
    title = _title(hwnd).split(" - ")[0].strip().lower()
    return next((t for t in tabs if str(_safe(lambda t=t: t.LocationName) or "").lower() == title), tabs[0])


def _tab_window(tab) -> int | None:
    """The tab's own window, via IServiceProvider -> IShellBrowser::GetWindow (IOleWindow's first method)."""
    try:
        from comtypes import COMMETHOD, GUID, HRESULT, IServiceProvider, IUnknown

        class IShellBrowser(IUnknown):  # only the IOleWindow part is needed
            _iid_ = GUID("{000214E2-0000-0000-C000-000000000046}")
            _methods_ = [COMMETHOD([], HRESULT, "GetWindow", (["out"], ctypes.POINTER(wt.HWND), "phwnd")),
                         COMMETHOD([], HRESULT, "ContextSensitiveHelp", (["in"], wt.BOOL, "fEnterMode"))]

        sp = tab._comobj.QueryInterface(IServiceProvider)
        sid = GUID("{4C96BE40-915C-11CF-99D3-00AA004AE837}")  # SID_STopLevelBrowser
        browser = sp.QueryService(sid, IShellBrowser)
        return int(browser.GetWindow() or 0)
    except Exception:
        log.debug("tab window lookup failed", exc_info=True)
        return None


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _fs(path) -> str | None:
    path = str(path or "")
    return os.path.normpath(path) if path and not path.startswith("::") and os.path.isdir(path) else None


def coverage(folder: str) -> str:
    """'full' if SmartSearch indexes this folder, 'partial' if it contains indexed folders
    (e.g. your user folder), 'none' otherwise (only file names are searched there, live)."""
    from .sources import walk
    if walk.wanted(os.path.join(folder, "x")):
        return "full"
    f = os.path.normcase(folder).rstrip(os.sep) + os.sep
    roots = [os.path.normcase(os.path.abspath(r)) for r in config.get("folders")]
    return "partial" if any(r.startswith(f) for r in roots) else "none"
