"""Windows settings, tools and fixes from catalog/actions.toml, plus live status widgets and controls.

Scoring (chosen with eval/run_actions_eval.py on 189 queries written apart from the catalog):
  joined  one vector of description + all example phrases        hit@1 0.715 hit@3 0.866
  max     best of separate description / phrase vectors          hit@1 0.648 hit@3 0.894
  hybrid  mean of the two, minus 0.5 x the action's "hubness"     hit@1 0.743 hit@3 0.905
Hubness is how similar an action is to every *other* action's phrases; generic actions
like "Focus" otherwise show up for unrelated queries. All numbers at 256 dims, int8.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import tomllib

import numpy as np

from .. import config, controls
from ..embedder import QUERY, doc
from .base import Result, Source, open_path, run_command

log = logging.getLogger(__name__)

ICONS = {"network": "📶", "devices": "🔌", "sound": "🔊", "display": "🖥️", "power": "🔋", "storage": "💽",
         "apps": "🧩", "update": "🔄", "security": "🛡️", "accounts": "👤", "privacy": "🔒",
         "personalization": "🎨", "system": "⚙️", "accessibility": "♿", "gaming": "🎮"}
BADGES = {"settings": "Settings", "tool": "Tool", "command": "Command"}


def load_catalog(path: str | None = None) -> list[dict]:
    with open(path or config.CATALOG_PATH, "rb") as f:
        return tomllib.load(f)["action"]


class ActionsSource(Source):
    name = "actions"
    label = "Settings & fixes"
    panel = "cards"
    intent = ("Fix a computer problem or change a Windows setting: Wi-Fi, internet, Bluetooth, sound, display, "
              "battery, storage, updates, drivers, printers, accounts, privacy.")
    # Raw hybrid score: relevant queries p10 = 0.72, unrelated queries max = 0.66 (eval, 256-d).
    floor, strong = 0.69, 0.78
    HUB_WEIGHT = 0.5

    def __init__(self, store):
        super().__init__(store)
        self.actions = {a["id"]: a for a in load_catalog()}
        self._widget_cache: dict[str, tuple[float, dict]] = {}
        self._widget_lock = threading.Lock()
        self._hub: dict[str, float] = json.loads(self.store.kv_get("actions_hub") or "{}")
        self._parts: dict[int, tuple[str, str]] = {}

    # ---- indexing -------------------------------------------------------------------
    def index(self, emb, progress) -> None:
        with open(config.CATALOG_PATH, "rb") as f:
            h = hashlib.sha1(f.read()).hexdigest() + f":{self.store.dim}/{self.store.dtype}"
        if self.store.kv_get("actions_hash") == h:
            return
        self.store.clear_source(self.name)
        texts, keys = [], []
        for a in self.actions.values():
            phrases = a.get("phrases", [])
            texts.append(doc(a["about"] + " Problems: " + "; ".join(phrases), a["title"]))
            keys.append((a["id"], "joined"))
            texts.append(doc(a["about"], a["title"]))
            keys.append((a["id"], "about"))
            for i, p in enumerate(phrases):
                texts.append(doc(p, a["title"]))
                keys.append((a["id"], f"p{i}"))
        progress(f"embedding {len(texts)} descriptions", 0, len(texts))
        vecs = emb.texts(texts)
        for (aid, part), v in zip(keys, vecs):
            self.store.add(self.name, f"actions:{aid}:{part}", v, title=aid,
                           meta={"action": aid, "part": part}, commit=False)
        self.store.commit()
        self._parts.clear()

        # Hubness: average hybrid score of each action against other actions' phrases, used as queries.
        progress("calibrating", 0, 0)
        refs = [(a["id"], p) for a in self.actions.values() for p in a.get("phrases", [])]
        qv = emb.texts([QUERY.format(p) for _, p in refs])
        sums: dict[str, list[float]] = {aid: [0.0, 0] for aid in self.actions}
        for (owner, _), q in zip(refs, qv):
            for aid, s in self._hybrid(q).items():
                if aid != owner:
                    sums[aid][0] += s
                    sums[aid][1] += 1
        mean = {aid: s / max(n, 1) for aid, (s, n) in sums.items()}
        base = float(np.mean(list(mean.values())))
        self._hub = {aid: m - base for aid, m in mean.items()}
        self.store.kv_set("actions_hub", json.dumps(self._hub))
        self.store.kv_set("actions_hash", h)

    # ---- search ---------------------------------------------------------------------
    def _part(self, item_id: int) -> tuple[str, str] | None:
        if item_id not in self._parts:
            it = self.store.get(item_id)
            self._parts[item_id] = (it.meta["action"], it.meta.get("part", "")) if it else None
        return self._parts[item_id]

    def _hybrid(self, qvec: np.ndarray) -> dict[str, float]:
        """action id -> mean(joined score, best of description/phrase scores)."""
        ids, scores = self.store.scores(self.name, qvec)
        joined: dict[str, float] = {}
        best: dict[str, float] = {}
        for item_id, s in zip(ids.tolist(), scores.tolist()):
            p = self._part(item_id)
            if not p:
                continue
            aid, part = p
            if part == "joined":
                joined[aid] = s
            elif s > best.get(aid, -1.0):
                best[aid] = s
        return {aid: (joined.get(aid, best[aid]) + best[aid]) / 2 for aid in best}

    def rank(self, qvec: np.ndarray) -> list[tuple[str, float, float]]:
        """(action id, ranking score, raw hybrid score), best first."""
        out = [(aid, s - self.HUB_WEIGHT * self._hub.get(aid, 0.0), s) for aid, s in self._hybrid(qvec).items()]
        return sorted(out, key=lambda x: -x[1])

    def search(self, emb, qvec, text, k=8) -> list[Result]:
        out = []
        for aid, _, s in self.rank(qvec)[:k]:
            a = self.actions.get(aid)
            if not a:
                continue
            out.append(Result(
                id=f"actions:{aid}", source=self.name, title=a["title"], subtitle=a["about"], score=s,
                icon=ICONS.get(a["category"], "⚙️"), badge=BADGES.get(a["kind"], a["kind"]),
                kind={"settings": "setting"}.get(a["kind"], a["kind"]),
                actions=[{"label": "Copy command", "op": "copy"}] if a["kind"] == "command" else []))
        return out

    def run(self, result_id: str, op: str = "open") -> dict:
        a = self.actions.get(result_id.split(":", 1)[1])
        if not a:
            return {"ok": False, "error": "unknown action"}
        if op == "copy":
            return {"ok": True, "copy": a["open"], "toast": "Command copied"}
        if a["kind"] == "command":
            run_command(a["open"])
        elif a["open"].startswith("control "):
            subprocess.Popen(a["open"].split())
        else:
            open_path(a["open"])
        return {"ok": True, "toast": f"Opened {a['title']}"}

    # ---- live status widgets ----------------------------------------------------------
    def widget(self, result_ids: list[str]) -> dict | None:
        if not result_ids:
            return None
        top = self.actions.get(result_ids[0].split(":", 1)[1], {})
        w = self._cached(top.get("category"))
        # live controls (volume, brightness, dark mode) are read fresh every time: they change outside the app
        ctl = controls.controls_for(top.get("category"))
        if not ctl:
            return w
        w = dict(w) if w else {"title": "Quick controls", "rows": [], "tone": "ok"}
        w["controls"] = ctl
        return w

    def warm(self) -> None:
        """Fill the widget cache (the netsh / WMI calls take ~1 s) so a query typed right after opening is instant."""
        for key in WIDGETS:
            try:
                self._cached(key)
            except Exception:  # noqa: BLE001 — a failing probe just means no card later
                log.debug("widget warm-up %s failed", key, exc_info=True)

    def _cached(self, key: str | None) -> dict | None:
        fn = WIDGETS.get(key)
        if not fn:
            return None
        with self._widget_lock:
            hit = self._widget_cache.get(key)
        if hit and time.time() - hit[0] < 10:
            return hit[1]
        w = fn()
        with self._widget_lock:
            self._widget_cache[key] = (time.time(), w)
        return w


def _worst(rows: list[list]) -> str:
    tones = [r[2] for r in rows if len(r) > 2]
    return "bad" if "bad" in tones else "warn" if "warn" in tones else "ok"


def network_status() -> dict:
    rows: list[list] = []
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                             timeout=3, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        f = dict(re.findall(r"^\s*([A-Za-z][\w ()-]*?)\s*:\s(.*)$", out, re.M))
        state = f.get("State", "").strip()
        if not f:
            rows.append(["Wi-Fi", "No Wi-Fi adapter found", "warn"])
        elif state == "connected":
            sig = int(re.sub(r"\D", "", f.get("Signal", "0")) or 0)
            rows.append(["Wi-Fi", f"Connected to {f.get('SSID', '?').strip()}", "ok"])
            tone = "ok" if sig >= 60 else "warn" if sig >= 35 else "bad"
            hint = "" if sig >= 60 else " — weak, move closer to the router" if sig >= 35 else " — very weak"
            rows.append(["Signal", f"{sig}% ({f.get('Band', '').strip() or f.get('Radio type', '').strip()}){hint}", tone])
            if f.get("Receive rate (Mbps)"):
                rows.append(["Link speed", f"{f['Receive rate (Mbps)'].strip()} Mbps down / "
                                           f"{f.get('Transmit rate (Mbps)', '?').strip()} Mbps up"])
        else:
            rows.append(["Wi-Fi", state.capitalize() or "Off", "bad"])
    except Exception as e:
        rows.append(["Wi-Fi", f"Couldn't read status ({e.__class__.__name__})", "warn"])

    t = time.perf_counter()
    try:
        socket.setdefaulttimeout(1.5)
        socket.getaddrinfo("www.msftconnecttest.com", 443)
        dns_ms = (time.perf_counter() - t) * 1000
        with socket.create_connection(("1.1.1.1", 443), timeout=1.5):
            rtt = (time.perf_counter() - t) * 1000 - dns_ms
        rows.append(["Internet", f"Reachable · DNS {dns_ms:.0f} ms · latency {rtt:.0f} ms",
                     "ok" if rtt < 150 else "warn"])
    except socket.gaierror:
        rows.append(["Internet", "DNS lookups failing — try Flush DNS or change DNS server", "bad"])
    except OSError:
        rows.append(["Internet", "Not reachable", "bad"])
    finally:
        socket.setdefaulttimeout(None)
    return {"title": "Network right now", "rows": rows, "tone": _worst(rows)}


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def battery_status() -> dict:
    ps = _PowerStatus()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(ps))
    pct = ps.BatteryLifePercent & 0xFF
    if pct == 255:
        rows = [["Battery", "No battery detected"]]
    else:
        plugged = ps.ACLineStatus == 1
        rows = [["Battery", f"{pct}%", "ok" if pct > 30 or plugged else "warn" if pct > 15 else "bad"],
                ["Power", "Plugged in" if plugged else "On battery"]]
        if not plugged and ps.BatteryLifeTime != 0xFFFFFFFF:
            rows.append(["Time left", f"{ps.BatteryLifeTime // 3600} h {ps.BatteryLifeTime % 3600 // 60} min"])
        if ps.SystemStatusFlag & 1:
            rows.append(["Energy saver", "On"])
    return {"title": "Battery right now", "rows": rows, "tone": _worst(rows)}


def storage_status() -> dict:
    rows = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        root = f"{chr(65 + i)}:\\"
        if bitmask >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
            u = shutil.disk_usage(root)
            free_pct = u.free / u.total * 100
            rows.append([root[:2], f"{u.free / 1e9:.0f} GB free of {u.total / 1e9:.0f} GB",
                         "ok" if free_pct > 15 else "warn" if free_pct > 5 else "bad"])
    return {"title": "Disk space right now", "rows": rows, "tone": _worst(rows)}


WIDGETS = {"network": network_status, "power": battery_status, "storage": storage_status}
