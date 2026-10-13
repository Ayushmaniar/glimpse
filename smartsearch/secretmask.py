"""API keys, tokens and passwords inside indexed text: found so result snippets can show them masked.

Files with keys are still indexed and found ("gemini api key" finds the .env or note that holds it);
the result row just shows `AIza••••••3kQ` instead of the key, so it isn't on screen during a screen
share. The full value is one click away ("Copy key") and in the Shift+Space preview.

Detection, most specific first:
  provider formats   Google AIza…, OpenAI sk-…, Anthropic sk-ant-…, GitHub ghp_/github_pat_, AWS AKIA…,
                     Slack xox?-, Stripe sk_live_…, Hugging Face hf_…, JWTs
  assignments        GEMINI_API_KEY=…, "password": "…", token: … (the value only, and only when it looks
                     like a literal: no os.environ[...], function calls or placeholders)
  bearer / URLs      Authorization: Bearer …, https://user:password@host
  private keys       -----BEGIN … PRIVATE KEY----- blocks
  long random runs   32+ chars that keep switching upper/lower/digit (hex hashes, CamelCase, URLs left alone)
"""
from __future__ import annotations

import re

PROVIDER = re.compile(
    r"AIza[0-9A-Za-z_\-]{35}"
    r"|sk-ant-[A-Za-z0-9_\-]{20,}"
    r"|sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|hf_[A-Za-z0-9]{30,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")
ASSIGN = re.compile(
    r"(?i)[\w.\-]*(?:api[_\-]?key|apikey|secret|token|passw(?:or)?d|pwd|access[_\-]?key|private[_\-]?key|auth[_\-]?key|"
    r"credential)[\w.\-]*[\"']?\s*[:=]\s*[\"']?([^\s\"',;`]{8,})")
BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{16,}=*)")
URL_PASS = re.compile(r"://[^/\s:@]+:([^/\s@]{4,})@")
PRIVATE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----([\s\S]*?)(?:-----END [A-Z ]*PRIVATE KEY-----|$)")
RANDOM = re.compile(r"(?<![A-Za-z0-9_\-+/.])[A-Za-z0-9_\-+]{32,}={0,2}(?![A-Za-z0-9_\-+/.])")  # not in URLs/paths
PLACEHOLDER = re.compile(r"(?i)your|xxxx|example|placeholder|changeme|here|<|>|\*\*\*|•")
NOT_LITERAL = re.compile(r"[()\[\]{}$%]|^(?:os|process|env|self|config|settings|request|args)\.", re.I)
DOT = "•"


def _literal(v: str) -> bool:
    """A value that is a key, not code that fetches one or a placeholder."""
    return not NOT_LITERAL.search(v) and not PLACEHOLDER.search(v) and (
        any(c.isdigit() for c in v) or len(v) >= 20)


def _random(v: str) -> bool:
    """Generated, not written: upper, lower and digits that keep switching, with digits scattered in
    several runs (CamelCaseWords2024 and AbstractFactoryBean2Builder have one digit run)."""
    kind = ["u" if c.isupper() else "l" if c.islower() else "d" if c.isdigit() else "o" for c in v]
    switches = sum(a != b for a, b in zip(kind, kind[1:]))
    digit_runs = len(re.findall(r"\d+", v))
    return ({"u", "l", "d"} <= set(kind) and switches >= len(v) / 4 and digit_runs >= 3
            and not re.fullmatch(r"[0-9a-fA-F]+", v) and not PLACEHOLDER.search(v))


def find(text: str) -> list[tuple[int, int]]:
    """Sorted, non-overlapping (start, end) spans of secret values in `text`."""
    spans = [m.span() for m in PROVIDER.finditer(text)]
    for rx in (ASSIGN, BEARER, URL_PASS):
        spans += [m.span(1) for m in rx.finditer(text) if _literal(m.group(1)) or rx is not ASSIGN]
    spans += [m.span(1) for m in PRIVATE.finditer(text) if m.group(1).strip()]
    spans += [m.span() for m in RANDOM.finditer(text) if _random(m.group())]
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def hide(v: str) -> str:
    if "\n" in v.strip():  # a private key body
        return f"{DOT * 6} (private key hidden) "
    v = v.strip()
    return v[:4] + DOT * 6 + v[-3:] if len(v) >= 16 else v[:2] + DOT * 6


def mask(text: str) -> str:
    spans = find(text)
    if not spans:
        return text
    parts, last = [], 0
    for s, e in spans:
        parts += [text[last:s], hide(text[s:e])]
        last = e
    return "".join(parts) + text[last:]


def values(text: str) -> list[str]:
    return [text[s:e].strip() for s, e in find(text)]
