"""Measure the settings router on eval/actions_eval.toml (queries written independently of the catalog).

Usage:  .venv\\Scripts\\python.exe -m eval.run_actions_eval
Compares how actions are represented, query prefixes, Matryoshka dims and int8 storage.
"""
from __future__ import annotations

import os
import time
import tomllib
from collections import defaultdict

import numpy as np

from smartsearch import embedder as E
from smartsearch.sources.actions import load_catalog

HERE = os.path.dirname(os.path.abspath(__file__))


def norm(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=-1, keepdims=True)


def int8(m: np.ndarray) -> np.ndarray:
    s = np.abs(m).max(1, keepdims=True) / 127
    return np.round(m / s).astype(np.int8).astype(np.float32) * s


def main() -> None:
    actions = load_catalog()
    with open(os.path.join(HERE, "actions_eval.toml"), "rb") as f:
        cases = tomllib.load(f)["case"]
    emb = E.Embedder(dim=768)
    t = time.perf_counter()

    # Representations: list of (action index, text) per scheme.
    schemes = {
        "about": [(i, E.doc(a["about"], a["title"])) for i, a in enumerate(actions)],
        "joined": [(i, E.doc(a["about"] + " Problems: " + "; ".join(a.get("phrases", [])), a["title"]))
                   for i, a in enumerate(actions)],
        "max": [(i, E.doc(a["about"], a["title"])) for i, a in enumerate(actions)]
               + [(i, E.doc(p, a["title"])) for i, a in enumerate(actions) for p in a.get("phrases", [])],
    }
    vecs = {k: emb.texts([t for _, t in v]) for k, v in schemes.items()}
    owners = {k: np.array([i for i, _ in v]) for k, v in schemes.items()}
    qtext = [c["q"] for c in cases]
    queries = {
        "search": emb.texts([E.QUERY.format(q) for q in qtext]),
        "classify": emb.texts([E.CLASSIFY.format(q) for q in qtext]),
    }
    print(f"embedded {sum(len(v) for v in schemes.values())} action texts + {2 * len(cases)} queries "
          f"in {time.perf_counter() - t:.1f}s on {emb.backend}\n")
    ids = [a["id"] for a in actions]
    pos = [c for c in cases if c["expect"]]

    def evaluate(scheme, qkind, dim=768, q8=False):
        A = norm(vecs[scheme][:, :dim])
        if q8:
            A = int8(A)
        Q = norm(queries[qkind][:, :dim])
        S = Q @ A.T
        best = np.full((len(cases), len(actions)), -1.0)
        for j, o in enumerate(owners[scheme]):
            best[:, o] = np.maximum(best[:, o], S[:, j])
        h1 = h3 = 0
        by_style = defaultdict(lambda: [0, 0])
        pos_top, neg_top, misses = [], [], []
        for ci, c in enumerate(cases):
            order = np.argsort(-best[ci])
            top = [ids[k] for k in order[:3]]
            if not c["expect"]:
                neg_top.append(best[ci, order[0]])
                continue
            pos_top.append(best[ci, order[0]])
            ok1, ok3 = top[0] in c["expect"], any(x in c["expect"] for x in top)
            h1 += ok1
            h3 += ok3
            by_style[c.get("style", "?")][0] += ok1
            by_style[c.get("style", "?")][1] += 1
            if not ok3:
                misses.append((c["q"], c["expect"][0], top))
        return h1 / len(pos), h3 / len(pos), by_style, np.array(pos_top), np.array(neg_top), misses

    print(f"{'scheme':8} {'query':9} {'dim':>4} {'int8':>5}   hit@1   hit@3")
    rows = []
    for scheme in schemes:
        for qk in queries:
            for dim in (768, 512, 256, 128):
                for q8 in (False, True):
                    if q8 and dim not in (768, 256):
                        continue
                    r = evaluate(scheme, qk, dim, q8)
                    rows.append(((scheme, qk, dim, q8), r))
                    print(f"{scheme:8} {qk:9} {dim:>4} {str(q8):>5}   {r[0]:.3f}   {r[1]:.3f}")

    (scheme, qk, dim, q8), (h1, h3, by_style, pt, nt, misses) = max(rows, key=lambda x: (x[1][0], x[1][1]))
    print(f"\nbest: scheme={scheme} query={qk} dim={dim} int8={q8}  hit@1={h1:.3f} hit@3={h3:.3f}")
    print("by style (hit@1):", {k: f"{a}/{n}" for k, (a, n) in sorted(by_style.items())})
    print(f"top score  relevant: p10={np.percentile(pt, 10):.3f} median={np.median(pt):.3f}   "
          f"unrelated ('none' cases): max={nt.max():.3f} median={np.median(nt):.3f}")
    print(f"\nmisses (not in top 3) for best config: {len(misses)}")
    for q, want, top in misses[:25]:
        print(f"  {q!r:55} want {want:22} got {top}")
    emb.close()
    production(cases)


def production(cases) -> None:
    """The ranker the app actually ships (ActionsSource: hybrid - hubness, 256-d int8 store)."""
    from smartsearch.sources.actions import ActionsSource
    from smartsearch.store import Store

    emb = E.Embedder()
    src = ActionsSource(Store())
    src.index(emb, lambda *a: None)
    pos = [c for c in cases if c["expect"]]
    h1 = h3 = 0
    misses = []
    for c in pos:
        top = [aid for aid, _, _ in src.rank(emb.query(c["q"]))[:3]]
        h1 += top[0] in c["expect"]
        h3 += any(x in c["expect"] for x in top)
        if not any(x in c["expect"] for x in top):
            misses.append((c["q"], c["expect"][0], top))
    print(f"\nPRODUCTION (hybrid - {src.HUB_WEIGHT} x hubness, {emb.dim}-d int8): "
          f"hit@1={h1 / len(pos):.3f} hit@3={h3 / len(pos):.3f}  misses={len(misses)}")
    emb.close()


if __name__ == "__main__":
    main()
