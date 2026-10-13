"""Checks the emoji, shell-history, browser-history and open-window sources with the real embedder.

Only synthetic fixtures are read: history paths and browser roots are overridden, and window
titles are never printed. Run from the project root:
    .venv\\Scripts\\python.exe -m tests.test_extra_sources
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from glimpse.embedder import Embedder
from glimpse.sources.browser_history import EPOCH, BrowserHistorySource
from glimpse.sources.emoji import SYNONYMS, EmojiSource, _characters
from glimpse.sources.shell_history import ShellHistorySource
from glimpse.sources.windows import WindowsSource, _windows
from glimpse.store import Store
from glimpse.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery

FAILS = []

SHELL_LINES = [
    "cd C:\\projects\\demo",
    "pip install numpy",
    "winget install --id Git.Git",
    "Restart-Service docker",
    "docker restart web-api",
    '$env:API_TOKEN = "abc"',                                    # secret: token
    "git push origin main",
    "pip install numpy",                                         # duplicate
    'curl -H "Authorization: Bearer xyz" https://api.example.com',  # secret: authorization
    "ssh -p 2222 deploy@server",                                 # secret pattern: -p
    "az account show --query abcdefghijklmnopqrstuvwxyz0123456789ABCD",  # long blob
    "ls",                                                        # too short
    'Set-Content .\\notes.txt "hello"',
    "python -m venv .venv",
]
SHELL_KEPT = 8  # unique, safe, >= 4 chars

BROWSER_ROWS = [  # url, title, days ago
    ("https://sleepfoundation.org/sleep-and-caffeine", "How Caffeine Affects Your Sleep - Sleep Foundation", 3),
    ("https://www.example-news.com/science/coffee-timing?utm=x", "Why the timing of your coffee matters", 10),
    ("https://github.com/numpy/numpy", "numpy/numpy: The fundamental package for scientific computing", 1),
    ("https://www.youtube.com/watch?v=abc", "Caffeine and sleep explained (video)", 4),
    ("https://accounts.example.com/signin?next=/home", "Sign in", 3),            # login path
    ("https://bank.example.com/login", "My bank", 5),                            # login path
    ("https://mail.google.com/mail/u/0/", "Inbox - Gmail", 1),                   # mail host
    ("http://localhost:3000/dashboard", "Local dev dashboard", 2),               # localhost
    ("https://shop.example.com/cart?session=abc123", "Your cart", 2),            # session param
    ("ftp://files.example.com/readme", "Old FTP readme", 6),                     # scheme
    ("https://www.example-news.com/science/coffee-timing?utm=y", "Why the timing of your coffee matters", 11),
    ("https://example.com/no-title", "", 7),                                     # empty title
    ("https://oauth.example.com/callback?code=xyz", "Callback", 2),              # callback path, code param
]
BROWSER_KEPT = 4  # plus one Brave profile page below
BRAVE_ROWS = [("https://en.wikipedia.org/wiki/Caffeine", "Caffeine - Wikipedia", 20)]

EMOJI_Q = ["celebration", "laughing so hard", "temperature symbol", "rupee sign", "right arrow", "thumbs up",
           "quarterly tax spreadsheet", "database migration rollback"]
SHELL_Q = ["how did I install numpy", "restart docker", "list files", "birthday party invitation"]
BROWSER_Q = ["that article about sleep and caffeine", "numpy library on github", "tax return forms"]
WINDOW_Q = ["browser", "chat app"]


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def progress(msg: str, done: int, total: int) -> None:
    pass


def write_shell(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def make_history_db(path: str, rows: list[tuple[str, str, int]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR, "
                "visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER NOT NULL, hidden INTEGER NOT NULL)")
    now = datetime.now(timezone.utc)
    for i, (url, title, days) in enumerate(rows, 1):
        when = now - timedelta(days=days)
        us = int((when - EPOCH).total_seconds() * 1_000_000)
        con.execute("INSERT INTO urls VALUES (?,?,?,?,0,?,0)", (i, url, title, 1, us))
    con.commit()
    con.close()


def show(source, emb: Embedder, q: str, k: int = 3) -> None:
    print(f"  q={q!r}")
    for r in source.search(emb, emb.query(q), q, k=k):
        print(f"     {r.score:.3f}  {r.icon or ' '}  {r.title[:72]}   [{r.subtitle}] {r.snippet}".rstrip())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="glimpse-test-")
    store = Store(os.path.join(tmp, "test.sqlite"))
    t0 = time.perf_counter()
    emb = Embedder(backend="cpu")
    print(f"embedder ready in {time.perf_counter() - t0:.1f}s")

    # ---- emoji -----------------------------------------------------------------------
    names = {name for _, _, name in _characters()}
    missing = [n for n in SYNONYMS if n not in names]
    check(not missing, f"all synonym keys are real Unicode names (missing: {missing})")
    emoji = EmojiSource(store)
    t = time.perf_counter()
    emoji.index(emb, progress)
    n_emoji = store.counts().get("emoji", 0)
    print(f"emoji index: {n_emoji} items in {time.perf_counter() - t:.1f}s")
    check(n_emoji > 500, "emoji items indexed")
    t = time.perf_counter()
    emoji.index(emb, progress)
    check(store.counts().get("emoji") == n_emoji and time.perf_counter() - t < 2.0,
          f"emoji re-index skipped when unchanged ({time.perf_counter() - t:.2f}s)")
    for q in EMOJI_Q:
        show(emoji, emb, q)
    first = emoji.search(emb, emb.query("celebration"), "celebration", k=1)[0]
    print("  run() copy result:", emoji.run(first.id, "open"))

    # ---- shell history ---------------------------------------------------------------
    shell_file = os.path.join(tmp, "ConsoleHost_history.txt")
    write_shell(shell_file, SHELL_LINES)
    shell = ShellHistorySource(store, history_path=shell_file)
    shell.index(emb, progress)
    check(store.counts().get("shell_history") == SHELL_KEPT,
          f"shell: {store.counts().get('shell_history')} indexed, expected {SHELL_KEPT}")
    write_shell(shell_file, SHELL_LINES + ["npm run build", "kubectl get pods -n prod"])
    shell.index(emb, progress)
    check(store.counts().get("shell_history") == SHELL_KEPT + 2, "shell: appended lines indexed incrementally")
    for q in SHELL_Q:
        show(shell, emb, q)
    hit = shell.search(emb, emb.query("restart docker"), "restart docker", k=1)[0]
    print("  run() copy:", shell.run(hit.id, "copy"))

    # ---- browser history -------------------------------------------------------------
    chrome_ud = os.path.join(tmp, "Chrome", "User Data")
    brave_ud = os.path.join(tmp, "Brave", "User Data")
    make_history_db(os.path.join(chrome_ud, "Default", "History"), BROWSER_ROWS)
    make_history_db(os.path.join(brave_ud, "Profile 1", "History"), BRAVE_ROWS)
    browser = BrowserHistorySource(store, roots={"Chrome": chrome_ud, "Brave": brave_ud})
    browser.index(emb, progress)
    n_browser = store.counts().get("browser_history")
    check(n_browser == BROWSER_KEPT + len(BRAVE_ROWS), f"browser: {n_browser} indexed, expected 5")
    browser.index(emb, progress)  # watermark: nothing new
    check(store.counts().get("browser_history") == n_browser, "browser: second pass adds nothing")
    for q in BROWSER_Q:
        show(browser, emb, q)
    hit = browser.search(emb, emb.query("numpy library on github"), "q", k=1)[0]
    print("  run() copy:", browser.run(hit.id, "copy"))

    # ---- open windows (counts and scores only, no titles) ----------------------------
    wins = _windows()
    print(f"windows: {len(wins)} app windows found")
    win = WindowsSource(store)
    bogus = win.run("windows:1234567", "open")
    check(bogus.get("ok") is False, "windows: run() on a missing window returns ok:False")
    for q in WINDOW_Q:
        scores = [round(r.score, 3) for r in win.search(emb, emb.query(q), q, k=3)]
        print(f"  q={q!r} top scores (titles hidden): {scores}")

    # ---- shell trim: a rewritten, shorter history replaces what was indexed ---------
    trim_store = Store(os.path.join(tmp, "trim.sqlite"))
    trim_file = os.path.join(tmp, "trim_history.txt")
    write_shell(trim_file, ["git status", "npm test", "cargo build --release"])
    trim = ShellHistorySource(trim_store, history_path=trim_file)
    trim.index(emb, progress)
    write_shell(trim_file, ["git status", "npm test"])
    trim.index(emb, progress)
    check(trim_store.counts().get("shell_history") == 2, "shell: trimmed history re-indexed from scratch")

    print("FAILURES: " + "; ".join(FAILS) if FAILS else "ALL CHECKS PASSED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
