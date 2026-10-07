"""
matching.py -- keyword gate, relevance score and date normalisation.

Scoring is a transparent weighted model rather than an LLM call, so every
score is explainable and identical across runs:

    title match          5 pts per field term, 2 per broad term
    detail-only match    2 / 1 pts, capped at 3 in total
    services hint        +3  (consulting, study, grant ... in title/type)
    goods/works hint     -5  (supply, vehicles, civil works ... in title)

    score = 100 * (1 - e^(-points / 6))
    one field term in the title ~57, two ~81, plus a services hint ~90
"""
import math
import re
import unicodedata
from datetime import datetime

from dateutil import parser as _dateparser

import config


def fold(text) -> str:
    """Lower-case and strip accents, so 'Résilience' matches 'resilience'."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _terms_re(terms):
    # Longest first, so "disaster risk reduction" wins over "risk reduction".
    alts = sorted({fold(t).strip() for t in terms if str(t).strip()}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(a) for a in alts) + ")")


_CANONICAL = {fold(k).strip(): k for k in config.KEYWORDS}
_KEYWORD_RE = _terms_re(config.KEYWORDS)
_BROAD = {fold(k).strip() for k in config.BROAD_KEYWORDS}
_SERVICE_RE = _terms_re(config.SERVICE_HINTS)
_GOODS_RE = _terms_re(config.GOODS_HINTS)


def find_keywords(text) -> list[str]:
    """Distinct configured keywords found in text, in order of appearance."""
    found = []
    for m in _KEYWORD_RE.finditer(fold(text)):
        k = _CANONICAL.get(m.group(0), m.group(0))
        if k not in found:
            found.append(k)
    return found


def score_notice(title, detail="", kind="") -> tuple[int, list[str]]:
    """(score 0-100, matched keywords). No match means score 0 and []."""
    in_title = find_keywords(title)
    in_detail = [k for k in find_keywords(detail) if k not in in_title]
    if not in_title and not in_detail:
        return 0, []

    def broad(k):
        return fold(k) in _BROAD

    points = sum(2 if broad(k) else 5 for k in in_title)
    points += min(3, sum(1 if broad(k) else 2 for k in in_detail))
    if _SERVICE_RE.search(fold(f"{title} {kind}")):
        points += 3
    if _GOODS_RE.search(fold(title)):
        points -= 5
    return round(100 * (1 - math.exp(-max(points, 0) / 6))), in_title + in_detail


_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def norm_date(value) -> str:
    """Best-effort 'YYYY-MM-DD'; '' when the value holds no recognisable date.

    Handles the formats the sources actually use: ISO timestamps, RSS dates,
    '09-Oct-26 06:00 AM (New York time)', 'Friday, January 09, 2026',
    US-style '9/20/26 11:59 PM' and '01/31/2028'.
    """
    if not value:
        return ""
    s = str(value).strip()
    m = _ISO.match(s)
    if m:
        return "-".join(m.groups())
    s = re.sub(r"\([^)]*\)", " ", s)  # "(New York time)", "(GMT 5.00)"
    try:
        d = _dateparser.parse(s, fuzzy=True, ignoretz=True, default=datetime(datetime.now().year, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return ""
    return d.date().isoformat() if 2000 <= d.year <= 2100 else ""


def is_iso_date(s) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s or "")))
