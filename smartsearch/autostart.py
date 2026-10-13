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
NAME = "SmartSearch"


def command() -> str:
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    # A Run entry has no working directory, so start the module from the project folder explicitly.
    code = (f"import os,sys,runpy;os.chdir(r'{config.ROOT}');sys.path.insert(0,r'{config.ROOT}');"
            "runpy.run_module('smartsearch.app',run_name='__main__')")
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
