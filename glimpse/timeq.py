"""Time words in a query: "screenshots from last week", "pdf I downloaded yesterday", "latest voice note".

Embeddings can't sort by date, so dates are pulled out of the query before it's embedded and become
a window on item timestamps (photo taken / file modified / page visited):

  range   explicit window: today, yesterday, this/last week|month|year, past 3 days, 2 weeks ago,
          in March, March 2025, in 2024, since 2023, on Monday, last weekend, last summer, winter 2024
  latest  "latest", "newest", "most recent", "recent", "the last screenshot": newest first
  browse  nothing left but generic words ("photos", "my screenshots", ""): list by date, not meaning
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
           r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}   # first month (northern hemisphere)
SEASON_RE = r"(spring|summer|fall|autumn|winter)"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "few": 3, "couple of": 2,
       "a few": 3, "a couple of": 2}
NUM_RE = r"(\d+|a couple of|couple of|a few|few|an?|one|two|three|four|five|six)"
UNIT_DAYS = {"day": 1, "week": 7, "month": 30.44, "year": 365.25}
MEDIA_NOUNS = r"(screenshots?|photos?|pictures?|pics?|images?|pdfs?|files?|documents?|docs?|videos?|clips?|" \
              r"recordings?|voice notes?|voice memos?|memos?|downloads?|scans?|notes?|slides?|presentations?)"
GENERIC = set("""photo photos picture pictures pic pics image images screenshot screenshots file files document
documents doc docs pdf pdfs video videos clip clips recording recordings voice note notes memo memos audio download
downloads scan scans code slide slides presentation presentations my the a an all i took taken made saved
downloaded recorded any show me from in on of that which what were was stuff things everything something
did do does doing done work worked working edit edited open opened change changed create created look looked
at see saw word powerpoint pptx docx python script scripts jupyter notebook notebooks ipynb markdown md csv
spreadsheet spreadsheets excel json deck decks""".split())

# File-type words become an extension filter on documents ("latest pdf", "python scripts from last week").
TYPES = [
    (r"\bpdfs?\b", {".pdf"}),
    (r"\b(?:word (?:docs?|documents?|files?)|docx)\b", {".docx"}),
    (r"\b(?:power ?points?|pptx?|slide ?decks?|decks?)\b", {".pptx"}),
    (r"\b(?:python (?:files?|scripts?|code)|\.py)\b", {".py"}),
    (r"\b(?:jupyter notebooks?|ipynb|notebooks?)\b", {".ipynb"}),
    (r"\b(?:markdown(?: files?| notes?)?|\.md)\b", {".md", ".markdown"}),
    (r"\b(?:csvs?|spreadsheets?|excel)\b", {".csv"}),
    (r"\bjson(?: files?)?\b", {".json"}),
]


def file_types(q: str) -> set[str]:
    exts: set[str] = set()
    for pattern, e in TYPES:
        if re.search(pattern, q, re.I):
            exts |= e
    return exts


@dataclass
class TimeQuery:
    text: str                   # the query with the time words removed ("" if nothing else was said)
    mode: str = "none"          # none | range | latest
    start: float | None = None  # unix seconds, inclusive
    end: float | None = None    # unix seconds, exclusive
    label: str = ""             # what was understood, shown to the user ("last week")
    browse: bool = False        # only generic words remain: order by date, not by meaning
    exts: frozenset = frozenset()  # file types named in the query ("pdf", "python scripts")

    def to_json(self) -> dict | None:
        if self.mode == "none" and not self.exts:
            return None
        return {"mode": self.mode, "label": self.label, "start": self.start, "end": self.end, "browse": self.browse,
                "exts": sorted(self.exts)}


def _day(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(year: int, month: int) -> datetime:
    return datetime(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _num(s: str) -> int:
    return int(s) if s.isdigit() else NUM.get(s, 1)


def parse(q: str, now: datetime | None = None) -> TimeQuery:
    now = now or datetime.now()
    today = _day(now)
    text = " " + q.strip() + " "
    found: tuple[datetime, datetime, str] | None = None
    latest = False

    def take(pattern: str, fn) -> None:
        nonlocal text, found
        if found:
            return
        m = re.search(pattern, text, re.I)
        if m:
            r = fn(m)
            if r:
                found = r
                text = text[: m.start()] + " " + text[m.end():]

    take(r"\b(today|this morning|this afternoon|this evening|tonight)\b",
         lambda m: (today, today + timedelta(days=1), "today"))
    take(r"\bday before yesterday\b", lambda m: (today - timedelta(days=2), today - timedelta(days=1),
                                                 "day before yesterday"))
    take(r"\b(yesterday|last night)\b", lambda m: (today - timedelta(days=1), today, "yesterday"))
    take(r"\b(?:on |over |during )?(?:the )?(this|last|past) weekend\b", lambda m: _weekend(today, m.group(1).lower()))
    take(rf"\b(?:in |during |from |of |last |this )?{SEASON_RE}[ ,]+((?:19|20)\d{{2}})\b",
         lambda m: _season(today, m.group(1).lower(), int(m.group(2)), None))
    take(rf"\b(?:in |during |from |of )?(?:the )?(this|last|past) {SEASON_RE}\b",
         lambda m: _season(today, m.group(2).lower(), None, m.group(1).lower()))
    take(rf"\b(?:in|during|from|of) (?:the )?{SEASON_RE}\b", lambda m: _season(today, m.group(1).lower(), None, None))
    take(rf"\b(?:in |over |during )?(?:the )?(?:last|past) {NUM_RE} (day|week|month|year)s?\b",
         lambda m: (now - timedelta(days=_num(m.group(1).lower()) * UNIT_DAYS[m.group(2).lower()]),
                    now + timedelta(days=1), f"past {_num(m.group(1).lower())} {m.group(2).lower()}s"))
    take(rf"\b{NUM_RE} (day|week|month|year)s? ago\b",
         lambda m: _ago(now, _num(m.group(1).lower()), m.group(2).lower()))
    take(r"\b(this|last|past) (week|month|year)(?:'s)?\b",lambda m: _calendar(today, m.group(1).lower(), m.group(2).lower()))
    take(rf"\b(?:in |during |from |of |on |last )?{MONTH_RE}[ ,]+((?:19|20)\d{{2}})\b",
         lambda m: _month(today, m.group(1), int(m.group(2))))
    take(rf"\b(in|during|from|of|last|since) {MONTH_RE}\b",
         lambda m: _month(today, m.group(2), None, since=m.group(1).lower() == "since"))
    take(r"\b(in|during|from|of|since) ((?:19|20)\d{2})\b",
         lambda m: (datetime(int(m.group(2)), 1, 1),
                    (today + timedelta(days=1)) if m.group(1).lower() == "since" else datetime(int(m.group(2)) + 1, 1, 1),
                    f"{'since ' if m.group(1).lower() == 'since' else ''}{m.group(2)}"))
    take(r"\b(?:on |last )(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
         lambda m: _weekday(today, m.group(1).lower()))

    if not found:
        m = re.search(rf"\b(latest|newest|most recent|recently|recent|the last|my last|last)\b(?=\s+{MEDIA_NOUNS}\b|\s*$)"
                      rf"|\b(latest|newest|most recent|recently|recent)\b", text, re.I)
        if m:
            latest = True
            text = text[: m.start()] + " " + text[m.end():]

    text = re.sub(r"\s+", " ", text).strip(" ,.?!")
    text = re.sub(r"^(?:from|in|on|of|since|during|that|which)\s+|\s+(?:from|in|on|of|since|during|that|which)$",
                  "", text, flags=re.I).strip()
    words = re.findall(r"[a-z']+", text.lower())
    browse = all(w in GENERIC for w in words)
    exts = frozenset(file_types(q))
    if found:
        start, end, label = found
        return TimeQuery(text, "range", start.timestamp(), end.timestamp(), label, browse, exts)
    if latest:
        return TimeQuery(text, "latest", None, None, "newest first", browse, exts)
    return TimeQuery(q.strip(), exts=exts, browse=bool(exts) and browse)


def _ago(now: datetime, n: int, unit: str):
    span = timedelta(days=UNIT_DAYS[unit])
    center = now - n * span
    half = span / 2 if unit != "day" else timedelta(hours=12)
    if unit == "day":
        d = _day(center)
        return d, d + timedelta(days=1), f"{n} day{'s' if n > 1 else ''} ago"
    return center - half, center + half, f"{n} {unit}{'s' if n > 1 else ''} ago"


def _calendar(today: datetime, which: str, unit: str):
    if which == "past":
        return today - timedelta(days=round(UNIT_DAYS[unit])), today + timedelta(days=1), f"past {unit}"
    if unit == "week":
        start = today - timedelta(days=today.weekday())
        if which == "last":
            return start - timedelta(days=7), start, "last week"
        return start, start + timedelta(days=7), "this week"
    if unit == "month":
        start = today.replace(day=1)
        if which == "last":
            prev = _month_start(start.year, start.month - 1)
            return prev, start, prev.strftime("%B %Y")
        return start, _month_start(start.year, start.month + 1), "this month"
    start = today.replace(month=1, day=1)
    if which == "last":
        return start.replace(year=start.year - 1), start, str(start.year - 1)
    return start, start.replace(year=start.year + 1), str(start.year)


def _month(today: datetime, name: str, year: int | None, since: bool = False):
    idx = next(i for i, m in enumerate(MONTHS) if m.startswith(name.lower()[:3])) + 1
    if year is None:  # the most recent such month
        year = today.year if idx <= today.month else today.year - 1
    start = datetime(year, idx, 1)
    end = today + timedelta(days=1) if since else _month_start(year, idx + 1)
    return start, end, ("since " if since else "") + start.strftime("%B %Y")


def _weekend(today: datetime, which: str):
    """The most recent Saturday-Sunday (today's, during a weekend); "last weekend" during a weekend is the one before."""
    sat = today - timedelta(days=(today.weekday() - 5) % 7)
    if which != "this" and today.weekday() >= 5:
        sat -= timedelta(days=7)
    return sat, sat + timedelta(days=2), f"weekend of {sat.day} {sat.strftime('%b')}"


def _season(today: datetime, name: str, year: int | None, which: str | None):
    """Three months from the season's first month. Without a year: the most recent one that has started
    ("last summer" while it is still summer is the year before). Winter runs December to February."""
    m0 = SEASONS[name]
    if year is None:
        year = today.year if datetime(today.year, m0, 1) <= today else today.year - 1
        if which == "last" and _month_start(year, m0 + 3) > today:
            year -= 1
    start, end = datetime(year, m0, 1), _month_start(year, m0 + 3)
    label = f"winter {year}–{str(year + 1)[2:]}" if m0 == 12 else f"{'autumn' if name == 'autumn' else name} {year}"
    return start, end, label


def _weekday(today: datetime, name: str):
    back = (today.weekday() - DAYS.index(name)) % 7 or 7
    d = today - timedelta(days=back)
    return d, d + timedelta(days=1), d.strftime("%A %d %b")
