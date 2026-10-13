"""Notification-area icon: show the launcher, see indexing progress, pause, start with Windows, open the log, quit."""
from __future__ import annotations

import logging
import os
import threading
import time

from PIL import Image, ImageDraw

from . import autostart, config
from .sources import walk

log = logging.getLogger(__name__)


def _icon_image(px: int = 64) -> Image.Image:
    """The app icon (glimpse/assets/icon.png, drawn by tools/make_icon.py); a plain magnifier if it is missing."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    try:
        return Image.open(path).convert("RGBA").resize((px, px), Image.LANCZOS)
    except OSError:
        pass
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = px * 0.30
    cx = cy = px * 0.40
    w = max(3, px // 10)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(122, 162, 255, 255), width=w)
    d.line([cx + r * 0.7, cy + r * 0.7, px * 0.88, px * 0.88], fill=(122, 162, 255, 255), width=w + 2)
    return img


def start(engine, show, quit_app, hotkey: dict) -> object | None:
    """Run the tray icon on its own thread. Returns the pystray Icon (or None if unavailable)."""
    try:
        import pystray
    except ImportError:
        log.warning("pystray not installed; no tray icon")
        return None

    def status_text(_item=None) -> str:
        reason = walk.pause_reason()
        if reason:
            return f"Indexing {reason}"
        st = engine.status
        msg = engine.idle_message() if st.get("state") == "idle" else (st.get("message") or "")
        return (msg[:60] + "…") if len(msg) > 61 else (msg or "Starting…")

    def toggle_pause(icon, _item) -> None:
        walk.set_paused(not walk._paused.is_set())
        icon.update_menu()

    def toggle_autostart(icon, _item) -> None:
        try:
            autostart.set_enabled(not autostart.enabled())
        except OSError:
            log.exception("could not change Start with Windows")
        icon.update_menu()

    def open_log(_icon, _item) -> None:
        os.startfile(os.path.join(config.DATA_DIR, "glimpse.log"))  # noqa: S606 — user clicked it

    def do_quit(icon, _item) -> None:
        icon.stop()
        quit_app()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _i: f"Open Glimpse ({hotkey.get('spec', 'Alt+Space').title()})",
                         lambda _icon, _item: show(), default=True),
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Pause indexing", toggle_pause, checked=lambda _i: walk._paused.is_set()),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda _i: autostart.enabled()),
        pystray.MenuItem("Open log", open_log),
        pystray.MenuItem("Quit Glimpse", do_quit),
    )
    icon = pystray.Icon("Glimpse", _icon_image(), "Glimpse", menu)
    icon.run_detached()

    def refresh() -> None:  # keep the status line and tooltip current
        while True:
            time.sleep(5)
            try:
                icon.title = f"Glimpse: {status_text()}"
                icon.update_menu()
            except Exception:
                return

    threading.Thread(target=refresh, name="tray", daemon=True).start()
    return icon
