"""Date words that need no index: weekends and seasons, at fixed dates.

  .venv\\Scripts\\python.exe -m tests.test_timeq_words
"""
from __future__ import annotations

import sys
from datetime import datetime

from glimpse import timeq

CASES = [  # (now, query, expected start, expected end, remaining text)
    (datetime(2026, 10, 13), "photos from last weekend", datetime(2026, 10, 10), datetime(2026, 10, 12), "photos"),
    (datetime(2026, 10, 11), "photos from last weekend", datetime(2026, 10, 3), datetime(2026, 10, 5), "photos"),
    (datetime(2026, 10, 11), "pics this weekend", datetime(2026, 10, 10), datetime(2026, 10, 12), "pics"),
    (datetime(2026, 10, 13), "beach photos from last summer", datetime(2026, 6, 1), datetime(2026, 9, 1), "beach photos"),
    (datetime(2026, 7, 20), "beach photos from last summer", datetime(2025, 6, 1), datetime(2025, 9, 1), "beach photos"),
    (datetime(2026, 7, 20), "this summer", datetime(2026, 6, 1), datetime(2026, 9, 1), ""),
    (datetime(2026, 10, 13), "snow in winter 2024", datetime(2024, 12, 1), datetime(2025, 3, 1), "snow"),
    (datetime(2026, 1, 15), "snow photos from last winter", datetime(2024, 12, 1), datetime(2025, 3, 1), "snow photos"),
    (datetime(2026, 10, 13), "videos from the spring", datetime(2026, 3, 1), datetime(2026, 6, 1), "videos"),
    (datetime(2026, 10, 13), "summer dress", None, None, "summer dress"),        # a season word that is content
]


def main() -> None:
    fails = 0
    for now, q, start, end, text in CASES:
        r = timeq.parse(q, now)
        got = (datetime.fromtimestamp(r.start) if r.start else None, datetime.fromtimestamp(r.end) if r.end else None)
        ok = got == (start, end) and r.text == text
        fails += not ok
        print(("ok  " if ok else "FAIL"), f"{q!r} @ {now:%Y-%m-%d} -> {got[0]} .. {got[1]} [{r.label}] text={r.text!r}")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
