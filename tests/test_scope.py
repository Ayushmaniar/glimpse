"""Search this folder (opened from File Explorer): only files inside it, any file type by name (real model).

  .venv\\Scripts\\python.exe -m tests.test_scope
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="ss_scope_")
os.environ["GLIMPSE_DATA"] = os.path.join(TMP, "data")
HOME = os.path.join(TMP, "home")
TRIP, WORK, OUTSIDE = (os.path.join(HOME, "trip"), os.path.join(HOME, "work"), os.path.join(TMP, "elsewhere"))

from glimpse import config  # noqa: E402
from glimpse.core import Engine  # noqa: E402
from glimpse.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery

FILES = {
    os.path.join(TRIP, "itinerary.md"): "Day 2: hike to Vernal Fall, then dinner in Yosemite Valley. Budget for the trip.",
    os.path.join(TRIP, "packing.txt"): "Tent, headlamp, bear canister, rain jacket for the camping trip.",
    os.path.join(WORK, "budget_q3.md"): "Quarterly budget review: cloud costs up 14 percent, travel budget cut.",
    os.path.join(WORK, "standup.md"): "Standup notes: deploy the embedding service, fix the flaky test.",
    os.path.join(TRIP, "photos_backup.zip"): "PK not really a zip",
    os.path.join(OUTSIDE, "trip_receipts.zip"): "PK not really a zip",
    os.path.join(TRIP, "day3", "maps", "offline", "glacier_point_trail.md"):
        "Trail notes: Four Mile Trail climbs 3,200 feet to Glacier Point; start early to avoid the heat.",
    os.path.join(TRIP, "day3", "maps", "offline", "topo_tiles.zip"): "PK not really a zip",
}


def main() -> None:
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    config.update({"folders": [HOME], "pause_on_battery": False, "recent_first_days": 0,
                   "sources": {"windows": False, "actions": False, "emoji": False}})
    eng = Engine()
    eng.start()
    eng.ready.wait(120)
    while eng.status.get("state") != "idle":
        import time
        time.sleep(0.5)
    fails = 0

    def files_in(res):
        return sorted({os.path.relpath(r["id"] and _path(eng, r), TMP) for s in res["sections"] for r in s["results"]})

    def check(name, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    q = "budget"
    everywhere = files_in(eng.search_text(q))
    trip = files_in(eng.search_text(q, scope=TRIP))
    check("unscoped finds both folders", any("work" in p for p in everywhere) and any("trip" in p for p in everywhere),
          str(everywhere))
    check("scoped to trip/ stays in trip/", trip and all(p.startswith(os.path.join("home", "trip")) for p in trip),
          str(trip))
    sections = {s["source"] for s in eng.search_text("wifi not working", scope=TRIP)["sections"]}
    check("scoped search shows only file sections", sections <= {"files", "scans", "images", "audio", "video", "names"},
          str(sections))
    z = files_in(eng.search_text("photos backup", scope=TRIP))
    check("any file type found by name in the folder (zip)", os.path.join("home", "trip", "photos_backup.zip") in z, str(z))
    deep = os.path.join("home", "trip", "day3", "maps", "offline")
    nested = files_in(eng.search_text("hiking trail to glacier point", scope=TRIP))
    check("nested folders: found by meaning 3 levels down", os.path.join(deep, "glacier_point_trail.md") in nested,
          str(nested))
    nested = files_in(eng.search_text("topo tiles", scope=TRIP))
    check("nested folders: any file type by name 3 levels down", os.path.join(deep, "topo_tiles.zip") in nested,
          str(nested))
    out = files_in(eng.search_text("receipts", scope=OUTSIDE))
    check("unindexed folder: names found live", out == [os.path.join("elsewhere", "trip_receipts.zip")], str(out))
    print("ALL PASS" if not fails else f"{fails} FAILED")
    shutil.rmtree(TMP, ignore_errors=True)
    os._exit(1 if fails else 0)


def _path(eng, r) -> str:
    src, _, rid = r["id"].partition(":")
    if src == "names":
        s = eng.sources["names"]
        if rid.startswith("live"):
            return s._live[rid]
        return eng.store._db.execute("SELECT path FROM files WHERE rowid=?", (int(rid),)).fetchone()[0]
    return eng.store.get(int(rid)).path


if __name__ == "__main__":
    sys.exit(main())
