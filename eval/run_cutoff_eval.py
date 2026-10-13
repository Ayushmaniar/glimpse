"""Which results to show: compare cutoff rules on the live index (prints counts only, never file names).

  .venv\\Scripts\\python.exe -m eval.run_cutoff_eval [--n 60] [--seed 7]

Needs the app running (reads port + token from the page). Queries come from the index itself:
  known-item  a document's title words ("contoso award letter"), or a 7-word phrase from its text;
              the right answer is that document (or a copy with the same file name)
  absent      everyday topics whose specific words appear in no indexed title or text (checked in the DB)
For every query the app returns the documents section's top 200 raw candidates plus its score distribution
(debug mode), and each rule is replayed offline: a known-item query succeeds if its document is among the
first 8 distinct files kept; an absent query fails if anything is kept.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, ".data", "index.sqlite")

ABSENT = [
    "sourdough bread starter recipe", "knitting patterns for wool socks", "medieval castle siege tactics",
    "submarine sandwich franchise", "volcano eruption iceland 1783", "dinosaur fossil excavation mongolia",
    "beekeeping honey harvest", "origami crane folding", "bonsai tree pruning", "saxophone reed maintenance",
    "llama farming in peru", "ice hockey goalie pads", "glacier calving antarctica", "falconry hawk training",
    "pottery kiln temperature", "scuba diving certification", "gothic cathedral architecture",
    "vintage typewriter repair", "chess opening sicilian defense", "lighthouse keeper diary",
    "taxidermy squirrel mount", "maple syrup tapping", "jellyfish sting treatment", "wedding cake fondant",
    "horseshoe crab blood", "accordion polka festival", "submarine sonar operator", "cactus watering schedule",
    "tarot card spread", "rodeo bull riding", "sauerkraut fermentation crock", "kayak roll technique",
    "stained glass window", "crossword puzzle constructor", "beetle collection pinning", "tuba mouthpiece",
    "pirate treasure map", "yodeling lessons switzerland", "lumberjack axe throwing", "quilting fabric squares",
    "blorptastic frumble", "xylophone quantum burrito", "zebra accordion taxidermy", "purple elephant spaceship",
]
GENERIC = {"untitled", "document", "copy", "new", "final", "draft", "file", "image", "download", "scan", "doc",
           "page", "notes", "note", "temp", "test", "readme", "index", "main", "output", "data", "report"}


def server():
    page = None
    for port in _ports():
        try:
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read().decode()
            return port, re.search(r'TOKEN="([^"]+)"', page).group(1)
        except Exception:
            continue
    raise SystemExit("SmartSearch is not running")


def _ports():
    import subprocess
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    pids = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
                          capture_output=True, text=True).stdout
    pid_set = {line.split('","')[1] for line in pids.splitlines() if '","' in line}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[4] in pid_set and parts[1].startswith("127.0.0.1:"):
            yield int(parts[1].rsplit(":", 1)[1])


def words(s: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return [w.lower() for w in re.findall(r"[A-Za-z]{3,}", s)]


def make_queries(db, n: int, rng: random.Random):
    paths = [p for p, in db.execute("SELECT DISTINCT path FROM items WHERE source='files'")]
    rng.shuffle(paths)
    title_q, text_q = [], []
    for p in paths:
        stem = os.path.splitext(os.path.basename(p))[0]
        w = [x for x in words(stem) if x not in GENERIC]
        if len(title_q) < n and 2 <= len(w) <= 6:
            title_q.append((" ".join(w), p))
        if len(text_q) < n:
            rows = db.execute("SELECT snippet FROM items WHERE source='files' AND path=? LIMIT 5", (p,)).fetchall()
            for (snip,) in rows:
                toks = re.findall(r"[A-Za-z][A-Za-z']+", snip or "")
                if len(toks) >= 20:
                    i = rng.randrange(0, len(toks) - 7)
                    text_q.append((" ".join(toks[i:i + 7]).lower(), p))
                    break
        if len(title_q) >= n and len(text_q) >= n:
            break
    absent = []
    for q in ABSENT:
        specific = sorted(q.split(), key=len, reverse=True)[:2]
        hit = db.execute("SELECT 1 FROM items WHERE source='files' AND (" +
                         " AND ".join("(lower(snippet) LIKE ? OR lower(title) LIKE ?)" for _ in specific) + ") LIMIT 1",
                         [x for w in specific for x in (f"%{w}%", f"%{w}%")]).fetchone()
        if not hit:
            absent.append(q)
    return title_q, text_q, absent


def live(port, token, q) -> list[int]:
    """Item ids in the documents section the app actually shows for q (no debug, all sources)."""
    body = json.dumps({"q": q, "literal": True}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/search", body,
                                 {"X-Token": token, "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=120))
    f = next((s for s in d["sections"] if s["source"] == "files"), None)
    return [int(r["id"].split(":", 1)[1]) for r in (f["results"] if f else [])]


def ask(port, token, q):
    body = json.dumps({"q": q, "debug": True, "literal": True, "sources": ["files"]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/search", body,
                                 {"X-Token": token, "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=120))
    return next((g for g in d["diag"] if g["source"] == "files"), None)


RULES = {
    "fixed floor (current)": [None],
    "top-p999 >= d": [i / 1000 for i in range(0, 121, 3)],
    "top-p99 >= d": [i / 1000 for i in range(30, 201, 3)],
    "z >= d": [i / 10 for i in range(30, 81, 2)],
}


def cutoff(rule: str, d, g) -> float:
    x = g["dist"]
    if rule.startswith("fixed"):
        return g["floor"]
    if rule.startswith("top-p999"):
        return x["p999"] + d
    if rule.startswith("top-p99"):
        return x["p99"] + d
    return x["mean"] + d * x["std"]


def kept_paths(g, cut, path_of, k=8):
    out = []
    for i, s in g["dist"]["cands"]:
        if s < cut:
            break
        p = path_of(i)
        if p not in out:
            out.append(p)
        if len(out) >= k:
            break
    return out


def sweep_hybrid(port, token, title_q, text_q, absent, path_of, same) -> None:
    """Replay core's keep rule on the documents section's fused candidates: embedding floor OR keyword evidence
    (coverage >= c and information >= i nats)."""
    def pre(q):
        g = ask(port, token, q)
        return g, [(int(i.split(":")[1]), sc, cov, info) for i, sc, cov, info in g.get("pre", [])]
    runs = {"title": [(t, *pre(q)) for q, t in title_q], "phrase": [(t, *pre(q)) for q, t in text_q],
            "absent": [(None, *pre(q)) for q in absent]}

    def kept(g, cands, c, i):
        top = max((sc for _, sc, _, _ in cands), default=0.0)
        return [iid for iid, sc, cov, info in cands
                if sc >= max(g["floor"], top - g["keep"]) or (c is not None and cov >= c and info >= i)]
    print(f"queries: {len(title_q)} title, {len(text_q)} phrase, {len(absent)} absent (verified)")
    print(f"{'rule':34} {'title hit@8':>11}  {'phrase hit@8':>12}  {'absent shown':>12}")
    for c, i in [(None, None)] + [(c / 10, i) for c in range(3, 11) for i in (0.0, 1.0, 2.3, 4.0, 6.0)]:
        r = {k: 0 for k in runs}
        for kind, rs in runs.items():
            for target, g, cands in rs:
                ids = kept(g, cands, c, i)
                r[kind] += bool(ids) if kind == "absent" else any(same(path_of(x), target) for x in ids)
        label = "hybrid ranking, embedding floor only" if c is None else f"+ keywords: cover>={c:.1f} info>={i}"
        print(f"{label:34} {r['title']:>5}/{len(title_q):<5}  {r['phrase']:>6}/{len(text_q):<5}  "
              f"{r['absent']:>6}/{len(absent):<5}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live", action="store_true", help="score what the app shows now (no rule replay)")
    ap.add_argument("--hybrid", action="store_true", help="sweep the keyword keep rule over the fused candidates")
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rng = random.Random(a.seed)
    title_q, text_q, absent = make_queries(db, a.n, rng)
    port, token = server()
    cache: dict[int, str] = {}

    def path_of(i):
        if i not in cache:
            r = db.execute("SELECT path FROM items WHERE id=?", (i,)).fetchone()
            cache[i] = r[0] if r else ""
        return cache[i]

    def same(p, target):
        return p == target or os.path.basename(p).lower() == os.path.basename(target).lower()

    if a.live:
        def ok(ids, t):
            return any(same(path_of(i), t) for i in ids)
        t_hit = sum(ok(live(port, token, q), t) for q, t in title_q)
        p_hit = sum(ok(live(port, token, q), t) for q, t in text_q)
        ab = sum(bool(live(port, token, q)) for q in absent)
        print(f"queries: {len(title_q)} title, {len(text_q)} phrase, {len(absent)} absent (verified)")
        print(f"live app: title {t_hit}/{len(title_q)}  phrase {p_hit}/{len(text_q)}  absent shown {ab}/{len(absent)}")
        return
    if a.hybrid:
        sweep_hybrid(port, token, title_q, text_q, absent, path_of, same)
        return
    runs = {"title": [(q, t, ask(port, token, q)) for q, t in title_q],
            "phrase": [(q, t, ask(port, token, q)) for q, t in text_q],
            "absent": [(q, None, ask(port, token, q)) for q in absent]}
    print(f"queries: {len(title_q)} title, {len(text_q)} phrase, {len(absent)} absent (verified)")
    found_any = {kind: sum(any(same(path_of(i), t) for i, _ in g["dist"]["cands"][:200]) for _, t, g in rs)
                 for kind, rs in runs.items() if kind != "absent"}
    print(f"retrievable (target in raw top 200): title {found_any['title']}/{len(title_q)}, "
          f"phrase {found_any['phrase']}/{len(text_q)}\n")
    print(f"{'rule':24} {'param':>6}  {'title hit@8':>11}  {'phrase hit@8':>12}  {'absent shown':>12}")
    for rule, params in RULES.items():
        best = None
        for d in params:
            res = {}
            for kind, rs in runs.items():
                if kind == "absent":
                    res[kind] = sum(bool(kept_paths(g, cutoff(rule, d, g), path_of)) for _, _, g in rs)
                else:
                    res[kind] = sum(any(same(p, t) for p in kept_paths(g, cutoff(rule, d, g), path_of))
                                    for _, t, g in rs)
            row = (rule, d, res["title"], res["phrase"], res["absent"])
            if rule.startswith("fixed"):
                best = row
                break
            # best = most known items found while showing something for at most 5% of absent queries
            if res["absent"] <= 0.05 * len(absent) and (best is None or res["title"] + res["phrase"] > best[2] + best[3]):
                best = row
        if best:
            r, d, t, ph, ab = best
            print(f"{r:24} {'' if d is None else d:>6}  {t:>5}/{len(title_q):<5}  {ph:>6}/{len(text_q):<5}  "
                  f"{ab:>6}/{len(absent):<5}")


if __name__ == "__main__":
    main()
