"""Keyword index (BM25) and the keep rules that use it; no model needed (random vectors).

  .venv\\Scripts\\python.exe -m tests.test_ranking
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="ss_rank_")
os.environ["SMARTSEARCH_DATA"] = os.path.join(TMP, "data")

import numpy as np  # noqa: E402

from smartsearch import core  # noqa: E402
from smartsearch.sources.base import Result  # noqa: E402
from smartsearch.store import Store  # noqa: E402

DOCS = {
    r"C:\docs\Offer letter.docx": "Congratulations! You are the recipient of the Contoso Global Innovation Award for 2024.",
    r"C:\docs\intern_report.pdf": "During my internship at Contoso I built the pricing dashboard for the team.",
    r"C:\docs\ceremony.md": "Planning notes for the award ceremony: venue, catering and the guest list.",
    r"C:\docs\invoice.txt": "Invoice INV-20391 for consulting services, due within 30 days of the invoice date.",
    r"C:\docs\NorthwindGamesOffer2024.pdf": "Compensation summary and start date.",
}
FILLER = "Weekly notes for the team: the plan for the week and the status of the project. "


def main() -> None:
    store = Store()
    rng = np.random.default_rng(0)

    def add(path, text, i=0):
        v = rng.normal(size=store.dim).astype(np.float32)
        return store.add("files", f"files:{path}#{i}", v / np.linalg.norm(v), path=path,
                         title=os.path.basename(path), subtitle=os.path.dirname(path), snippet=text, ts=0)

    ids = {p: add(p, t) for p, t in DOCS.items()}
    for i in range(300):  # a corpus where "the", "notes", "team" are everywhere and "award" is common
        add(rf"C:\docs\weekly\w{i}.md", FILLER + ("Team award nominations are open." if i % 5 == 0 else ""))
    store.add("images", "images:x", np.ones(store.dim, np.float32) / np.sqrt(store.dim), path=r"C:\p\contoso.jpg",
              title="contoso.jpg", snippet="contoso award photo", ts=0)
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    lex = store.lexical("Contoso Award")
    offer, intern, ceremony = (ids[r"C:\docs\Offer letter.docx"], ids[r"C:\docs\intern_report.pdf"],
                               ids[r"C:\docs\ceremony.md"])
    check("all words: ranked first, full coverage", lex[offer][0] == 0 and lex[offer][1] > 0.99, str(lex[offer]))
    check("the rare word carries most of the information", lex[intern][1] > lex[ceremony][1],
          f"contoso-only {lex[intern][1]:.2f} vs award-only {lex[ceremony][1]:.2f}")
    common = store.lexical("the notes")
    check("words in most documents carry almost no information (no stop-word list)",
          max(v[2] for v in common.values()) < core.LEX_INFO, f"max {max(v[2] for v in common.values()):.2f} nats")
    inv = store.lexical("INV-20391")
    check("IDs and codes are found by their parts", ids[r"C:\docs\invoice.txt"] in inv)
    northwind = store.lexical("northwind games offer 2024")
    rid = ids[r"C:\docs\NorthwindGamesOffer2024.pdf"]
    check("camelCase file names are found by their words", rid in northwind and northwind[rid][1] > 0.99, str(northwind.get(rid)))
    check("...and by the name as written", rid in store.lexical("northwindgamesoffer2024"))
    check("only document sources are indexed", all(store.get(i).source == "files" for i in lex))
    check("absent words: nothing", store.lexical("sourdough starter") == {})
    check("punctuation-only query is safe", store.lexical('"*) OR (') == {})

    store.remove_path("files", r"C:\docs\intern_report.pdf")
    check("removed files leave the keyword index", intern not in store.lexical("contoso"))
    store.move_path(r"C:\docs\Offer letter.docx", r"C:\docs\Contoso offer.docx")
    check("renamed files are found by their new name", offer in store.lexical("contoso offer docx"))
    store.set_snippets([("Congratulations on the Spotify Wrapped Prize.", r"files:C:\docs\Contoso offer.docx#0")])
    check("updated text replaces the old words", offer in store.lexical("spotify") and
          store.lexical("global tech")  == {})

    def res(cover, info):
        r = Result(id="files:1", source="files", title="t")
        r.lexical, r.lexical_info = cover, info
        return r
    check("keep rule: most of a rare query's information", core._lexical_ok(res(0.8, 9.0)))
    check("keep rule: only common words", not core._lexical_ok(res(1.0, 0.4)))
    check("keep rule: a small part of the query", not core._lexical_ok(res(0.3, 9.0)))
    print("ALL PASS" if not fails else f"{fails} FAILED")
    store._db.close()
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
