"""Start with Windows (tray menu toggle): a per-user Run entry, no admin rights, off by default.

The folder watcher only sees changes while the app runs; starting at logon keeps the index current
from the first minute of the day instead of after a catch-up scan.
"""
from __future__ import annotations

import os
import sys
import winreg

from . import config

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Glimpse"
OLD_NAME = "SmartSearch"  # the app's name before the rename; its Run entry would start a copy that no longer exists


def command() -> str:
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    # A Run entry has no working directory, so start the module from the project folder explicitly.
    code = (f"import os,sys,runpy;os.chdir(r'{config.ROOT}');sys.path.insert(0,r'{config.ROOT}');"
            "runpy.run_module('glimpse.app',run_name='__main__')")
    return f'"{exe}" -c "{code}"'


def enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass


def migrate() -> None:
    """Replace a stale "SmartSearch" Run entry with this app's own: on if the user had it on."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, OLD_NAME)
            except OSError:
                return
            winreg.DeleteValue(k, OLD_NAME)
        if not enabled():
            set_enabled(True)
    except OSError:
        pass
