"""Emoji & symbols: named emoji and symbol code points, embedded once and copied on click."""
from __future__ import annotations

import hashlib
import json
import unicodedata

from ..embedder import Embedder, doc
from ..store import Item
from .base import Progress, Result, Source

VERSION_KEY = "emoji_version"
CHUNK = 256
# Emoji, misc symbols and dingbats, arrows, math operators.
RANGES = [(0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x2190, 0x21FF), (0x2200, 0x22FF)]
# Everyday symbols that sit outside those blocks.
EXTRA = [0x0024, 0x00A3, 0x00A5, 0x20AC, 0x20B9, 0x00B0, 0x2103, 0x2109, 0x00B1, 0x00D7,
         0x00F7, 0x00A9, 0x00AE, 0x2122, 0x2026, 0x2022, 0x03C0]
# Extra words people search for, keyed by the real Unicode character name.
SYNONYMS = {
    "PARTY POPPER": "celebration, congratulations, party",
    "FACE WITH TEARS OF JOY": "laughing, lol, funny, lmao",
    "ROLLING ON THE FLOOR LAUGHING": "laughing so hard, rofl, lol",
    "HEAVY BLACK HEART": "love, heart, red heart",
    "SMILING FACE WITH HEART-SHAPED EYES": "love, adore, crush",
    "THUMBS UP SIGN": "ok, approve, like, yes",
    "THUMBS DOWN SIGN": "dislike, no, bad",
    "DEGREE SIGN": "temperature, degrees, degree symbol",
    "DEGREE CELSIUS": "temperature, celsius",
    "DEGREE FAHRENHEIT": "temperature, fahrenheit",
    "GRINNING FACE": "happy, smile, grin",
    "SMILING FACE WITH SMILING EYES": "happy, smile, blush",
    "WINKING FACE": "wink, joke",
    "THINKING FACE": "hmm, wondering, think",
    "CRYING FACE": "sad, cry, tear",
    "LOUDLY CRYING FACE": "sobbing, very sad, crying",
    "ANGRY FACE": "angry, mad, annoyed",
    "POUTING FACE": "angry, mad, rage",
    "SLEEPING FACE": "tired, sleep, zzz",
    "FACE WITH MEDICAL MASK": "sick, ill, covid",
    "SMILING FACE WITH SUNGLASSES": "cool, sunglasses",
    "FACE SCREAMING IN FEAR": "shocked, omg, scared",
    "CONFUSED FACE": "confused, unsure",
    "SMIRKING FACE": "smirk, sly",
    "FACE WITH STUCK-OUT TONGUE AND WINKING EYE": "playful, joking, silly",
    "SHRUG": "idk, whatever, shrug",
    "PERSON WITH FOLDED HANDS": "please, thanks, pray, namaste",
    "CLAPPING HANDS SIGN": "applause, bravo, well done",
    "PERSON RAISING BOTH HANDS IN CELEBRATION": "hooray, celebrate, yay",
    "WAVING HAND SIGN": "hello, hi, bye, wave",
    "OK HAND SIGN": "ok, fine, perfect",
    "VICTORY HAND": "peace, win",
    "FLEXED BICEPS": "strong, gym, workout",
    "FIRE": "hot, lit, burning",
    "HUNDRED POINTS SYMBOL": "perfect, 100, score",
    "SPARKLES": "magic, new, shiny",
    "ROCKET": "launch, ship it, fast, startup",
    "GLOWING STAR": "star, favorite",
    "CHECK MARK": "tick, done, correct",
    "WHITE HEAVY CHECK MARK": "done, ok, complete",
    "CROSS MARK": "wrong, no, delete, incorrect",
    "WARNING SIGN": "caution, alert, danger",
    "PENCIL": "write, edit",
    "MEMO": "note, notes, write",
    "CALENDAR": "date, schedule, day",
    "EYES": "look, see, watching",
    "BRAIN": "smart, think, mind",
    "ELECTRIC LIGHT BULB": "idea, tip, insight",
    "PERSONAL COMPUTER": "laptop, computer, pc",
    "MOBILE PHONE": "phone, cell, smartphone",
    "ENVELOPE": "email, mail, letter",
    "PAPERCLIP": "attach, attachment",
    "LINK SYMBOL": "url, hyperlink, chain",
    "KEY": "password, login",
    "LOCK": "secure, private, password",
    "HOUSE BUILDING": "home, house",
    "CAT FACE": "cat, kitten",
    "DOG FACE": "dog, puppy",
    "SLICE OF PIZZA": "food, lunch, dinner",
    "HAMBURGER": "food, burger, lunch",
    "BEER MUG": "beer, drink, cheers",
    "HOT BEVERAGE": "coffee, tea, drink",
    "BIRTHDAY CAKE": "birthday, cake",
    "WRAPPED PRESENT": "gift, birthday, present",
    "BALLOON": "party, birthday",
    "CONFETTI BALL": "celebration, party",
    "TROPHY": "win, winner, award",
    "CROWN": "king, queen, royal",
    "GEM STONE": "diamond, jewel",
    "MONEY BAG": "money, cash, rich",
    "CREDIT CARD": "payment, card",
    "SKULL": "dead, death",
    "GHOST": "boo, halloween",
    "AIRPLANE": "travel, flight, plane",
    "AUTOMOBILE": "car, drive, vehicle",
    "BLACK SUN WITH RAYS": "sunny, weather, sun",
    "CLOUD": "weather, rain",
    "SNOWFLAKE": "winter, cold, snow",
    "RIGHTWARDS ARROW": "right arrow, next, forward, go",
    "LEFTWARDS ARROW": "left arrow, back, previous",
    "UPWARDS ARROW": "up arrow",
    "DOWNWARDS ARROW": "down arrow",
    "BULLET": "bullet point, dot",
    "HORIZONTAL ELLIPSIS": "dots, more, ellipsis",
    "MULTIPLICATION SIGN": "times, multiply, cross",
    "DIVISION SIGN": "divide, divided by",
    "PLUS-MINUS SIGN": "plus minus, error margin",
    "INFINITY": "infinity, forever, endless",
    "SQUARE ROOT": "root, math, sqrt",
    "NOT EQUAL TO": "not equal, different",
    "DOLLAR SIGN": "dollar, usd, us money",
    "POUND SIGN": "pound, sterling, gbp, uk money",
    "EURO SIGN": "euro, money",
    "INDIAN RUPEE SIGN": "rupee, rs, inr, india money",
    "YEN SIGN": "yen, yuan, japan money",
    "COPYRIGHT SIGN": "copyright",
    "REGISTERED SIGN": "registered trademark",
    "TRADE MARK SIGN": "trademark",
    "GREEK SMALL LETTER PI": "pi, math, circle",
}


def _skip(ch: str, name: str) -> bool:
    """Control, format and combining characters, skin-tone modifiers and regional indicators."""
    if unicodedata.category(ch)[0] in "CM":
        return True
    return name.startswith(("EMOJI MODIFIER", "REGIONAL INDICATOR"))


def _characters() -> list[tuple[int, str, str]]:
    cps = sorted({cp for lo, hi in RANGES for cp in range(lo, hi + 1)} | set(EXTRA))
    out = []
    for cp in cps:
        ch = chr(cp)
        name = unicodedata.name(ch, None)
        if name and not _skip(ch, name):
            out.append((cp, ch, name))
    return out


def _text(name: str) -> str:
    syn = SYNONYMS.get(name)
    return f"{name.lower()}, {syn}" if syn else name.lower()


def _version(chars: list[tuple[int, str, str]]) -> str:
    blob = json.dumps([[cp, name] for cp, _, name in chars] + sorted(SYNONYMS.items()))
    return "v1-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


class EmojiSource(Source):
    name = "emoji"
    label = "Emoji & symbols"
    panel = "emoji"
    intent = ("Emoji, symbols and special characters to copy and paste, like a smiley, heart, arrow, "
              "degree sign or currency symbol")
    indexed = True
    supports_image_query = False
    floor = 0.80   # generic symbol matches sit at 0.75-0.80
    strong = 0.92

    def index(self, emb: Embedder, progress: Progress) -> None:
        chars = _characters()
        version = _version(chars)
        if self.store.kv_get(VERSION_KEY) == version and self.store.counts().get(self.name):
            progress("up to date", len(chars), len(chars))
            return
        self.store.clear_source(self.name)
        texts = [doc(_text(name), None) for _, _, name in chars]
        for start in range(0, len(chars), CHUNK):
            part = chars[start:start + CHUNK]
            vecs = emb.texts(texts[start:start + CHUNK])
            for (cp, ch, name), vec in zip(part, vecs):
                try:
                    self.store.add(self.name, f"emoji:U+{cp:04X}", vec, title=name.title(),
                                   meta={"char": ch}, commit=False)
                except Exception:  # one bad row never kills indexing
                    continue
            progress("embedding symbols", min(start + CHUNK, len(chars)), len(chars))
        self.store.kv_set(VERSION_KEY, version)
        self.store.commit()

    def to_result(self, it: Item, score: float) -> Result:
        ch = it.meta.get("char", "")
        return Result(id=f"{self.name}:{it.id}", source=self.name, title=it.title, score=score, icon=ch, kind="emoji")

    def run(self, result_id: str, op: str = "open") -> dict:
        it = self.store.get(int(result_id.split(":", 1)[1]))
        if it is None:
            return {"ok": False, "error": "item no longer exists"}
        ch = it.meta.get("char", "")
        return {"ok": True, "copy": ch, "toast": f"Copied {ch}"}
