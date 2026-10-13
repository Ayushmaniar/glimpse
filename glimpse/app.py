"""Glimpse launcher: local server + frameless window + global hotkey.

  .venv\\Scripts\\python.exe -m glimpse.app            launcher window (Alt+Space toggles)
  .venv\\Scripts\\python.exe -m glimpse.app --browser  no window; prints a URL to open in a browser
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import sys
import threading
import time

from . import autostart, config, explorer, server, tray, voice
from .core import Engine

log = logging.getLogger("glimpse")

WIDTH, BAR_HEIGHT = 780, 68

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0x4000, 0  # MOD_NOREPEAT
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in KEYS:
            vk = KEYS[part]
        elif len(part) == 1:
            vk = ord(part.upper())
    if not vk:
        raise ValueError(f"bad hotkey {spec!r}")
    return mods, vk


def hotkey_loop(specs: str | list[str], on_press, on_registered=lambda spec: None) -> None:
    """Register the first free hotkey. RegisterHotKey must run on the thread that pumps its messages."""
    user32 = ctypes.windll.user32
    for spec in [specs] if isinstance(specs, str) else specs:
        mods, vk = parse_hotkey(spec)
        if user32.RegisterHotKey(None, 1, mods, vk):
            log.info("hotkey: %s", spec)
            on_registered(spec)
            break
        log.warning("hotkey %s is taken by another app", spec)
    else:
        log.error("no free hotkey among %s; set 'hotkey' in .data/settings.json", specs)
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            on_press()


def own_windows(title: str) -> list[int]:
    """Top-level windows of this process with that title."""
    user32, found, pid = ctypes.windll.user32, [], os.getpid()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.InternalGetWindowText(hwnd, buf, 256)  # reads the title without messaging the window
            if buf.value == title:
                found.append(hwnd)
        return True
    user32.EnumWindows(each, 0)
    return found


def as_tool_window(hwnd: int) -> None:
    """A launcher, not an app: no taskbar button (it showed as a generic "python" icon) and not in Alt+Tab.
    The tray icon and the hotkey are the way back. Windows applies the change when the window is next shown."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    visible = user32.IsWindowVisible(hwnd)
    user32.ShowWindowAsync(hwnd, 0)  # SW_HIDE, then back: the taskbar re-reads the style (async: no cross-thread wait)
    if visible:
        user32.ShowWindowAsync(hwnd, 5)  # SW_SHOW


def single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\GlimpseLauncher")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true", help="don't open a window; print the URL")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-index", action="store_true", help="search the existing index without re-scanning")
    args = ap.parse_args()

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # model downloads on Windows without symlinks
    os.makedirs(config.DATA_DIR, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(os.path.join(config.DATA_DIR, "glimpse.log"), encoding="utf-8")]
    if sys.stderr:  # None under pythonw.exe
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)  # malformed-PDF chatter, logged as ERROR but harmless
    # pythonw has no stderr, so a crashing background thread would otherwise vanish without a trace.
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    if not single_instance():
        print("Glimpse is already running — press its hotkey (see .data/glimpse.log)")
        return
    autostart.migrate()  # a "Start with Windows" entry left over from the SmartSearch name

    engine = Engine()
    if args.no_index:
        engine.index_all = lambda: setattr(engine, "status", {**engine.status, "state": "idle",
                                                              "message": "Indexing paused (--no-index)"})
    engine.start()
    httpd, app = server.serve(engine, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    voice.warm_up_in_background()

    if args.browser:
        print(f"Glimpse running at {url}", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    import webview

    # Starts as just the search bar; the page asks for more height as results appear (POST /api/window).
    # Drag by the bar's empty space, the grip and the footer (class pywebview-drag-region). Only the element
    # itself counts, so selecting text in the search box or clicking a result never moves the window.
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    win = webview.create_window("Glimpse", url, width=WIDTH, height=BAR_HEIGHT, frameless=True, on_top=True,
                                easy_drag=False, resizable=True, background_color="#14161b")
    state = {"visible": True}

    def place() -> None:
        """Where the user last dragged it, if that is still on a screen; else centred near the top."""
        try:
            pos = config.get("window_pos")
            if pos and any(s.x <= pos[0] <= s.x + s.width - 80 and s.y <= pos[1] <= s.y + s.height - BAR_HEIGHT
                           for s in webview.screens):
                win.move(int(pos[0]), int(pos[1]))
                return
            s = webview.screens[0]
            win.move(int(s.x + (s.width - WIDTH) / 2), int(s.y + s.height * 0.16))
        except Exception:
            log.debug("place failed", exc_info=True)

    def show(from_window: int = 0) -> None:
        """`from_window`: the window that was in front when the hotkey was pressed. If it is File Explorer
        (or the desktop), the launcher offers to search that folder, like Explorer's own search box."""
        engine.warm()  # live status cards (Wi-Fi, battery, disk) are ready before the first keystroke
        place()
        win.show()
        win.restore()
        win.evaluate_js("window.ssFocus && window.ssFocus(); window.ssScope && window.ssScope(null)")
        state["visible"] = True
        if explorer.is_explorer(from_window):
            threading.Thread(target=send_scope, args=(from_window,), name="explorer-scope", daemon=True).start()

    def send_scope(hwnd: int) -> None:
        folder = explorer.folder_of(hwnd)  # COM round trip to Explorer, ~50-150 ms
        if folder and config.source_enabled("names"):
            from .sources import names
            names.prefetch(folder)  # list every file in its subfolders while the user types (~1 s for 200k)
        if folder and state["visible"]:
            info = {"folder": folder, "name": os.path.basename(folder.rstrip("\\")) or folder,
                    "coverage": explorer.coverage(folder)}
            win.evaluate_js(f"window.ssScope && window.ssScope({json.dumps(info)})")

    def moved() -> None:
        config.update({"window_pos": [win.x, win.y]})

    def recenter() -> None:
        config.update({"window_pos": None})
        place()

    app.on_moved, app.on_recenter = moved, recenter

    def hide() -> None:
        win.hide()
        state["visible"] = False

    def toggle() -> None:
        hide() if state["visible"] else show(explorer.foreground())

    app.on_hide = hide
    size = {"h": BAR_HEIGHT}

    def resize(h: int) -> None:
        if h != size["h"]:
            size["h"] = h
            win.resize(WIDTH, h)  # grows downward; the top edge (search bar) stays put

    app.on_resize = resize

    hotkey: dict = {}

    def quit_app() -> None:
        log.info("quit from tray")
        try:
            win.destroy()
        finally:
            os._exit(0)  # daemon threads (indexer, server) end with the process

    def started() -> None:
        place()
        win.events.shown.wait(15)
        for hwnd in own_windows("Glimpse"):
            as_tool_window(hwnd)
        threading.Thread(target=hotkey_loop, args=(config.get("hotkey"), toggle, lambda spec: hotkey.update(spec=spec)),
                         name="hotkey", daemon=True).start()
        tray.start(engine, show, quit_app, hotkey)
        log.info("Glimpse ready")

    webview.start(started, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(config.DATA_DIR, "webview"))


if __name__ == "__main__":
    main()
