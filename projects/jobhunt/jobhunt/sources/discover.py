"""Work out which ATS an employer uses, and under what slug.

Guessing board slugs by hand does not work - `worldresourcesinstitute` is a
404 while `wri` is live. So instead of shipping a watchlist of hopeful strings,
this module derives candidate slugs from a company name and probes each ATS
until something answers. One call per employer, cached in the DB afterwards.
"""

from __future__ import annotations

import re
from typing import Iterator

from .ats import PROVIDERS, board_identity, probe

# Ordered by how commonly each ATS shows up in the research / nonprofit /
# consulting world, so the likely hit comes first.
ATS_ORDER = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee"]

_NOISE = {
    "the", "inc", "llc", "ltd", "corp", "corporation", "company", "co",
    "group", "international", "global", "usa", "us", "america", "american",
}


def slug_candidates(name: str) -> list[str]:
    """Plausible board slugs for a company name, best guess first."""
    clean = re.sub(r"[^\w\s&-]", " ", name.lower())
    clean = clean.replace("&", " and ")
    words = [w for w in clean.split() if w]
    meaningful = [w for w in words if w not in _NOISE] or words

    joined = "".join(words)
    joined_meaningful = "".join(meaningful)
    hyphenated = "-".join(words)
    hyphen_meaningful = "-".join(meaningful)
    acronym = "".join(w[0] for w in meaningful if w)

    out = [
        joined_meaningful,
        joined,
        hyphen_meaningful,
        hyphenated,
        acronym if len(acronym) >= 2 else "",
        meaningful[0] if meaningful else "",
        f"{joined_meaningful}careers",
        f"{joined_meaningful}jobs",
    ]

    seen, result = set(), []
    for cand in out:
        cand = cand.strip("-")
        if cand and len(cand) >= 2 and cand not in seen:
            seen.add(cand)
            result.append(cand)
    return result


def _tokens(text: str) -> set[str]:
    clean = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return {w for w in clean.split() if w and w not in _NOISE and len(w) > 2}


def _flatten(text: str, keep: set[str]) -> str:
    """Distinctive words of ``text`` concatenated in their original order.

    Order matters: building this from a set would be non-deterministic and
    "Mercy Corps" would flatten to "corpsmercy" half the time.
    """
    words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
    return "".join(w for w in words if w in keep)


def _flatten_all(text: str) -> str:
    """Every word of text concatenated - filler words included."""
    return "".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _acronym(text: str) -> str:
    words = [w for w in re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
             if w and w not in _NOISE]
    return "".join(w[0] for w in words)


def identity_matches(wanted: str, declared: str | None, slug: str) -> bool:
    """Does a board that calls itself ``declared`` belong to ``wanted``?

    Deliberately strict. A wrong board silently poisons the pipeline with
    irrelevant jobs, which is worse than missing an employer you can add by
    hand.
    """
    want_tokens = _tokens(wanted)
    if not want_tokens:
        return False

    if not declared:
        # Nothing to verify against. Only trust a long, unambiguous slug that
        # spells out the whole name - a short one like "abt" or "cc" collides
        # with unrelated boards far too often.
        full = "".join(
            w for w in re.sub(r"[^\w\s]", " ", wanted.lower()).split() if w in want_tokens
        )
        return len(slug) >= 5 and slug.lower().replace("-", "") == full

    low_declared = declared.lower()
    if "do not use" in low_declared or low_declared.strip() in {"test", "demo"}:
        return False

    got_tokens = _tokens(declared)
    if not got_tokens:
        return False

    # Strict containment only. "Tetra Tech" vs "Tetra Tech Inc" passes;
    # "Climate Central" vs "Climate Corps" and "Natural Resources Defense
    # Council" vs "Natural Jobs" both share a word but neither contains the
    # other, so both are correctly rejected.
    if want_tokens <= got_tokens or got_tokens <= want_tokens:
        return True

    # Some ATSs report the name unspaced ("mercycorps"), which defeats token
    # comparison. Fall back to squashed strings, long enough not to collide.
    # Compare both with and without filler words, since one side may keep the
    # "the" that the other drops ("savechildren" vs "savethechildren").
    for want_flat, got_flat in (
        (_flatten(wanted, want_tokens), _flatten(declared, got_tokens)),
        (_flatten_all(wanted), _flatten_all(declared)),
    ):
        if len(got_flat) >= 6 and len(want_flat) >= 6:
            if want_flat in got_flat or got_flat in want_flat:
                return True

    # Acronym slug against a name that spells it out: "wri" -> World
    # Resources Institute. The declared name must still contain every
    # distinctive word we were looking for.
    if slug.lower() == _acronym(wanted) and want_tokens <= got_tokens:
        return True
    return False


def discover(name: str, max_probes: int = 40, require_jobs: bool = False,
             on_probe=None) -> tuple[str, str, str] | None:
    """Find ``(ats, slug, detail)`` for an employer, or ``None``.

    A board that answers with zero jobs still counts as found on the ATSs that
    404 correctly - the employer simply has nothing open today, and that is
    exactly the kind of board worth watching. Pass ``require_jobs=True`` to
    only accept boards with live postings.

    Keeps scanning after a zero-job hit so that a board with actual openings
    wins over an empty one for the same employer.
    """
    candidates = slug_candidates(name)
    tried = 0
    empty_hit: tuple[str, str, str] | None = None

    for slug in candidates:
        for ats in ATS_ORDER:
            if ats not in PROVIDERS:
                continue
            if tried >= max_probes:
                return None if require_jobs else empty_hit
            tried += 1
            if on_probe:
                on_probe(ats, slug, 'probing')
            ok, detail, n = probe(ats, slug)
            if not ok:
                continue

            declared = board_identity(ats, slug)
            if not identity_matches(name, declared, slug):
                if on_probe:
                    on_probe(ats, slug, f"rejected: board is {declared!r}")
                continue

            detail = f"{detail} [{declared}]" if declared else detail
            if n > 0:
                return ats, slug, detail
            if empty_hit is None:
                empty_hit = (ats, slug, detail)
    return None if require_jobs else empty_hit


# Careers URLs carry the ATS and the slug explicitly, so parsing one is exact
# where name-guessing is a coin flip. This is the recommended way to add an
# employer: open their careers page, copy the URL, paste it.
_URL_PATTERNS = [
    (r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", "greenhouse"),
    (r"greenhouse\.io/embed/job_board/?\?for=([\w-]+)", "greenhouse"),
    (r"jobs\.lever\.co/([\w-]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([\w-]+)", "ashby"),
    (r"apply\.workable\.com/([\w-]+)", "workable"),
    (r"([\w-]+)\.workable\.com", "workable"),
    (r"jobs\.smartrecruiters\.com/([\w-]+)", "smartrecruiters"),
    (r"careers\.smartrecruiters\.com/([\w-]+)", "smartrecruiters"),
    (r"([\w-]+)\.recruitee\.com", "recruitee"),
    (r"([\w.-]+)\.wd\d+\.myworkdayjobs\.com", "workday"),
]


def from_url(url: str) -> tuple[str, str] | None:
    """Extract ``(ats, slug)`` from a careers URL, or ``None`` if unrecognised."""
    url = url.strip()
    for pattern, ats in _URL_PATTERNS:
        m = re.search(pattern, url, re.I)
        if m:
            slug = m.group(1)
            if slug.lower() in {"www", "jobs", "careers", "apply", "boards"}:
                continue
            return ats, slug
    return None


def discover_many(names: list[str], on_result=None) -> dict[str, tuple[str, str, str] | None]:
    results: dict[str, tuple[str, str, str] | None] = {}
    for name in names:
        found = discover(name)
        results[name] = found
        if on_result:
            on_result(name, found)
    return results


# Employers worth watching in the hazards / resilience / policy world. These
# are *names*, not slugs - `jobhunt watch discover` resolves them at runtime so
# the list never goes stale.
SUGGESTED_EMPLOYERS = [
    # Research institutes & think tanks
    "RAND Corporation", "Urban Institute", "Brookings Institution",
    "Resources for the Future", "Pew Charitable Trusts", "MITRE",
    "Aspen Institute", "World Resources Institute", "Rocky Mountain Institute",
    # Climate / hazard analytics
    "First Street Foundation", "Jupiter Intelligence", "Climate Central",
    "Verisk", "Moody's",
    # Consulting / federal contractors
    "ICF", "Abt Global", "Cadmus Group", "Guidehouse", "Booz Allen Hamilton",
    "Tetra Tech", "Dewberry", "Michael Baker International", "Stantec",
    "Hagerty Consulting", "IEM", "CDM Smith", "AECOM", "Jacobs", "WSP", "Arcadis",
    # NGO / humanitarian / development
    "Mercy Corps", "International Rescue Committee", "Save the Children",
    "CARE", "Oxfam America", "Direct Relief", "Chemonics", "DAI",
    "Catholic Relief Services", "World Vision", "American Red Cross",
    # Conservation / environment
    "The Nature Conservancy", "Environmental Defense Fund",
    "Natural Resources Defense Council", "Conservation International",
    "Wildlife Conservation Society", "World Wildlife Fund",
]
