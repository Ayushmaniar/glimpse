"""Time-aware search end to end: dates in queries, recency tiebreak, browse ordering (runs the real model).

  .venv\\Scripts\\python.exe -m tests.test_time
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="ss_time_")
os.environ["GLIMPSE_DATA"] = os.path.join(TMP, "data")

from glimpse import config, timeq  # noqa: E402
from glimpse.sources import walk as _walk  # noqa: E402
_walk.on_battery = lambda: False  # index even when the laptop runs on battery

DOCS = os.path.join(TMP, "docs")
NOW = datetime.now()


def mid(q: str) -> float:
    """A timestamp in the middle of the window a phrase means right now."""
    t = timeq.parse(q)
    return (t.start + min(t.end, time.time())) / 2


FILES = {  # name: (text, timestamp)
    "budget_2024.md": ("Budget review notes. Travel spend went over by 12 percent, marketing under plan, "
                       "hiring frozen until Q3. Action items for finance team.",
                       datetime(2024, 5, 10, 12).timestamp()),
    "budget_lastweek.md": ("Budget review notes. Cloud spend over plan, travel on track, two hires approved. "
                           "Action items for the finance team.", mid("last week")),
    "trip_yesterday.md": ("Packing list for the Goa trip: sunscreen, swimsuit, charger, passport copy, sandals.",
                          mid("yesterday")),
    "recipe_2023.md": ("Grandma's dal recipe: toor dal, turmeric, tempering with cumin and garlic, simmer 30 min.",
                       datetime(2023, 2, 1, 12).timestamp()),
    "standup_today.md": ("Standup notes: finished the search ranking change, next is the preview overlay.",
                         time.time() - 600),
    "resize_helper.py": ("def resize(img, px):\n    '''Shrink an image so its longest side is px.'''\n"
                         "    w, h = img.size\n    s = px / max(w, h)\n    return img.resize((int(w * s), int(h * s)))\n",
                         time.time() - 3 * 86400),
    "old_tool.py": ("import csv\n\ndef load(path):\n    '''Read a CSV of expenses into a list of dicts.'''\n"
                    "    with open(path) as f:\n        return list(csv.DictReader(f))\n",
                    datetime(2023, 6, 1, 12).timestamp()),
}


def names(res: dict, source: str = "files") -> list[str]:
    return [r["title"] for s in res["sections"] if s["source"] == source for r in s["results"]]


def main() -> None:
    os.makedirs(DOCS)
    for name, (text, ts) in FILES.items():
        p = os.path.join(DOCS, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(p, (ts, ts))
    config.update({"folders": [DOCS], "sources": {k: k == "files" or k == "actions" for k in config.get("sources")}})

    from glimpse import core, embedder
    eng = core.Engine()
    eng.emb = embedder.get()
    eng._intent_vecs = {n: eng.emb.text(embedder.doc(s.intent, s.label)) for n, s in eng.sources.items() if s.intent}
    eng.ready.set()
    eng.sources["files"].index(eng.emb, lambda *a: None)

    checks = []

    def check(q: str, cond, why: str) -> None:
        res = eng.search_text(q)
        got = names(res)
        ok = cond(got)
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {q!r:36} -> {got}  ({why}; time={res.get('time') and res['time']['label']})")

    check("budget review notes", lambda g: g[:2] == ["budget_lastweek.md", "budget_2024.md"],
          "near-tie goes to the newer file")
    check("budget review notes from 2024", lambda g: g == ["budget_2024.md"], "year window")
    check("budget notes last week", lambda g: g == ["budget_lastweek.md"], "last-week window")
    check("notes from yesterday", lambda g: g == ["trip_yesterday.md"], "browse: everything from yesterday")
    check("what did I work on today", lambda g: g == ["standup_today.md"], "browse today")
    check("latest notes", lambda g: g[:1] == ["standup_today.md"] and "recipe_2023.md" not in g,
          "newest first, last month only")
    check("recent", lambda g: g[:4] == ["standup_today.md", "trip_yesterday.md", "resize_helper.py", "budget_lastweek.md"],
          "bare time word lists by date")
    check("dal recipe", lambda g: g[:1] == ["recipe_2023.md"], "no time words: meaning wins over age")
    check("dal recipe from last month", lambda g: g == [], "nothing in that window")
    check("my python scripts", lambda g: g == ["resize_helper.py", "old_tool.py"], "type filter, newest first")
    check("python code from this year", lambda g: g == ["resize_helper.py"], "type + year window")
    check("python code that reads expenses", lambda g: g[:1] == ["old_tool.py"] and all(x.endswith(".py") for x in g),
          "type filter, meaning still ranks")
    check("markdown notes from 2024", lambda g: g == ["budget_2024.md"], "markdown + year")
    res = eng.search_text("budget review notes")
    ts = [r["meta"].get("ts") for s in res["sections"] if s["source"] == "files" for r in s["results"]]
    checks.append(all(ts))
    print("PASS results carry meta.ts" if all(ts) else "FAIL missing meta.ts")
    eng.emb.close()
    print(f"{sum(checks)}/{len(checks)} passed")
    assert all(checks)
    print("OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
