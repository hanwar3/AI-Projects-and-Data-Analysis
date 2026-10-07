"""Scoring: how well does this posting fit you, and can you actually apply?

Deliberately **not** an LLM call. Every job would cost a token round-trip, the
result would drift between runs, and you could not see why anything ranked
where it did. Instead this is a transparent weighted model over signals that
matter for a research/policy/hazards career, and every score carries a
breakdown you can read.

Score is 0-100, from six components:

    title      30  does the job title live in one of your clusters
    skills     25  how many of your skills the posting actually asks for
    domain     20  subject-matter overlap (disaster, resilience, floodplain...)
    seniority  10  is this pitched at your level
    location   10  DC metro / remote / Texas
    bonus       5  PhD-shaped signals: publications, IRB, mixed methods

Then **eligibility flags** are applied. These are not score penalties, they are
warnings, because "requires US citizenship" is not a slightly worse job - it is
a different question entirely, and you decide what to do about it.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .profile import Profile

# ---------------------------------------------------------------- weights
WEIGHTS = {
    "title": 30.0,
    "skills": 25.0,
    "domain": 20.0,
    "seniority": 10.0,
    "location": 10.0,
    "bonus": 5.0,
}

# --------------------------------------------------------- eligibility rules
# Ordered most-specific first so a narrow rule wins over a loose one.
CITIZENSHIP_PATTERNS = [
    r"must be a (?:us|u\.s\.) citizen",
    r"(?:us|u\.s\.) citizenship (?:is )?(?:is )?required",
    r"requires? (?:us|u\.s\.) citizenship",
    r"only (?:us|u\.s\.) citizens",
    r"restricted to (?:us|u\.s\.) citizens",
    r"citizenship:?\s*(?:us|u\.s\.|united states)",
    r"must be a citizen of the united states",
]

CLEARANCE_PATTERNS = [
    r"active\s+(?:ts/sci|top secret|secret|dod)\s+(?:security\s+)?clearance",
    r"(?:must (?:possess|have)|requires?)\s+(?:an?\s+)?active\s+.{0,20}clearance",
    r"current\s+(?:ts/sci|top secret|secret)\s+clearance",
    r"active security clearance",
    r"ability to obtain (?:and maintain )?a?\s*(?:security )?clearance",
]

NO_SPONSORSHIP_PATTERNS = [
    r"(?:will|do|does|can)\s*not\s+(?:be able to\s+)?(?:provide|offer|consider)\s+(?:visa\s+)?sponsorship",
    r"no (?:visa )?sponsorship",
    r"sponsorship is not (?:available|offered|provided)",
    r"unable to sponsor",
    r"not able to sponsor",
    r"without (?:the need for )?(?:current or future )?sponsorship",
    r"we do not sponsor",
]

# Federal postings restricted to existing federal employees - a common and
# easily-missed dead end on USAJOBS.
STATUS_ONLY_PATTERNS = [
    r"status candidates",
    r"current federal employees",
    r"merit promotion",
    r"internal to the agency",
]


def _norm(text: str | None) -> str:
    if not text:
        return ""
    text = text.lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def _phrase_hits(haystack: str, phrase: str) -> int:
    """Count whole-word occurrences of a phrase."""
    if not phrase:
        return 0
    pattern = r"\b" + r"\s+".join(re.escape(w) for w in phrase.lower().split()) + r"\b"
    return len(re.findall(pattern, haystack))


# ------------------------------------------------------------------ components
def score_title(job_title: str, profile: Profile) -> tuple[float, list[str]]:
    """Fraction of a profile title cluster that the posting's title matches.

    Full phrase match scores 1.0. Otherwise credit is the share of the
    profile title's words present in the job title, so "Senior Research
    Analyst, Climate" still scores well against "Research Analyst".
    """
    jt = _norm(job_title)
    if not jt:
        return 0.0, []

    best, matched = 0.0, []
    for candidate in profile.all_titles():
        cand = _norm(candidate)
        if not cand:
            continue
        if _phrase_hits(jt, cand):
            if best < 1.0:
                matched = [candidate]
            best = 1.0
            break
        words = [w for w in cand.split() if len(w) > 2]
        if not words:
            continue
        hit = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", jt))
        ratio = hit / len(words)
        # A single generic word ("analyst") is not evidence of a match.
        if hit >= 2 or (len(words) == 1 and ratio == 1.0):
            if ratio > best:
                best, matched = ratio * 0.85, [candidate]

    # A title that matches an exclusion is disqualifying regardless.
    for bad in profile.exclude_titles:
        if _phrase_hits(jt, _norm(bad)):
            return 0.0, [f"-{bad}"]

    return best, matched


def score_skills(text: str, profile: Profile) -> tuple[float, list[str], list[str]]:
    """Weighted share of your skills that the posting asks for.

    Diminishing returns: a posting naming eight of your skills is a strong
    match, but one naming twenty is not three times better - it is just a long
    job description.
    """
    hay = _norm(text)
    matched: list[tuple[str, float]] = []
    missing: list[str] = []

    for skill in profile.all_skills():
        if _phrase_hits(hay, _norm(skill)):
            matched.append((skill, profile.skill_weight(skill)))
        else:
            missing.append(skill)

    if not matched:
        return 0.0, [], missing[:25]

    earned = sum(w for _, w in matched)
    # Saturating curve: ~0.63 at 5 weighted hits, ~0.86 at 10, ~0.95 at 15.
    normalised = 1 - math.exp(-earned / 5.0)
    top = [s for s, _ in sorted(matched, key=lambda kv: -kv[1])]
    return normalised, top, missing[:25]


def score_domain(text: str, profile: Profile) -> tuple[float, list[str]]:
    hay = _norm(text)
    hits = [d for d in profile.domains if _phrase_hits(hay, _norm(d))]
    if not hits:
        return 0.0, []
    # Four distinct domain terms is already a confident subject-matter match.
    return min(1.0, len(hits) / 4.0), hits


def score_seniority(job: dict[str, Any], profile: Profile) -> tuple[float, str]:
    title = _norm(job.get("title"))
    text = _norm((job.get("description") or "")[:2500])

    junior = bool(re.search(r"\b(intern|internship|student|entry[- ]level|apprentice|trainee|assistant to)\b", title))
    exec_level = bool(re.search(r"\b(chief|vice president|vp|director of|head of|executive director|partner)\b", title))
    senior = bool(re.search(r"\b(senior|sr\.?|lead|principal|iii|iv)\b", title))

    wants = {s.lower() for s in profile.seniority} or {"mid", "senior"}

    if junior:
        return (0.35, "junior posting") if "entry" in wants else (0.1, "junior posting")
    if exec_level:
        return 0.25, "executive posting"

    # Years of experience demanded, if stated.
    m = re.search(r"(\d{1,2})\+?\s*(?:-|to)?\s*(\d{1,2})?\s*years?(?:'|’)?\s+(?:of\s+)?experience", text)
    if m:
        low = int(m.group(1))
        if low >= 12:
            return 0.35, f"{low}+ yrs wanted"
        if low >= 8:
            return 0.7, f"{low}+ yrs wanted"
        if low <= 1:
            return 0.6, f"{low}+ yrs wanted"
        return 1.0, f"{low}+ yrs wanted"

    if senior and "senior" in wants:
        return 1.0, "senior"
    return 0.8, "unspecified"


def score_location(job: dict[str, Any], profile: Profile,
                   region_key: str | None = None) -> tuple[float, str]:
    """Score how well a posting's location fits the active region.

    The metro list comes from the region, not from hardcoded city names, so
    switching to ``--region uk`` makes London score the way Washington does
    for ``us_dc``.
    """
    loc = _norm(job.get("location"))
    remote_field = _norm(job.get("remote"))
    text = f"{loc} {remote_field}"

    if not text.strip():
        return 0.5, "unstated"

    try:
        region = profile.region(region_key)
    except KeyError:
        region = None

    remote_ok = profile.remote_ok and (region.remote_ok if region else True)
    if remote_ok and ("remote" in text or "anywhere" in text
                      or "telework" in text or "worldwide" in text):
        return 1.0, "remote"

    # Exact metro match is the best possible non-remote outcome.
    for term in (region.metro_terms if region else []):
        if _phrase_hits(text, _norm(term)):
            return 1.0, term

    for i, want in enumerate(profile.search_locations(region_key)):
        w = _norm(want)
        if w == "remote":
            continue
        if _phrase_hits(text, w):
            # Earlier entries in the location list rank higher.
            return max(0.7, 1.0 - i * 0.07), want

    if "hybrid" in text:
        return 0.45, "hybrid, elsewhere"
    return 0.15, (loc[:40] or "elsewhere")


def score_bonus(text: str, profile: Profile) -> tuple[float, list[str]]:
    hay = _norm(text)
    hits = [b for b in profile.bonus_terms if _phrase_hits(hay, _norm(b))]
    if not hits:
        return 0.0, []
    return min(1.0, len(hits) / 3.0), hits


# ------------------------------------------------------------------ eligibility
def eligibility_flags(text: str, profile: Profile) -> dict[str, Any]:
    """Detect hard constraints. Reported, never silently subtracted."""
    hay = _norm(text)
    flags: dict[str, Any] = {}
    evidence: list[str] = []

    def _check(patterns: list[str], key: str, label: str) -> None:
        for pat in patterns:
            m = re.search(pat, hay)
            if m:
                flags[key] = True
                start = max(0, m.start() - 45)
                evidence.append(f"{label}: ...{hay[start:m.end() + 45].strip()}...")
                return
        flags[key] = False

    _check(CITIZENSHIP_PATTERNS, "needs_citizenship", "citizenship")
    _check(CLEARANCE_PATTERNS, "needs_clearance", "clearance")
    _check(NO_SPONSORSHIP_PATTERNS, "no_sponsorship", "sponsorship")
    _check(STATUS_ONLY_PATTERNS, "status_only", "federal status")

    if evidence:
        flags["evidence"] = evidence[:4]
    return flags


# ----------------------------------------------------------------- salary parse
_SALARY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d)?\s?[kK])\s*(?:-|to|–)\s*\$?\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d)?\s?[kK])"
)


def parse_salary(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None

    def _to_num(s: str) -> float:
        s = s.strip().replace(",", "")
        if s.lower().endswith("k"):
            return float(s[:-1].strip()) * 1000
        return float(s)

    m = _SALARY_RE.search(text)
    if m:
        try:
            return _to_num(m.group(1)), _to_num(m.group(2))
        except ValueError:
            return None, None
    return None, None


# ------------------------------------------------------------------- top level
def score_job(job: dict[str, Any], profile: Profile,
              region_key: str | None = None) -> dict[str, Any]:
    """Score one posting. Returns score, per-component breakdown, and flags."""
    title = job.get("title") or ""
    description = job.get("description") or ""
    company = job.get("company") or ""
    # Titles carry the most signal per word, so they are weighted into the
    # blob that skill and domain matching reads.
    blob = f"{title} {title} {company} {description}"

    t_score, t_matched = score_title(title, profile)
    s_score, s_matched, s_missing = score_skills(blob, profile)
    d_score, d_matched = score_domain(blob, profile)
    sen_score, sen_note = score_seniority(job, profile)
    loc_score, loc_note = score_location(job, profile, region_key)
    b_score, b_matched = score_bonus(blob, profile)

    breakdown = {
        "title": round(t_score * WEIGHTS["title"], 1),
        "skills": round(s_score * WEIGHTS["skills"], 1),
        "domain": round(d_score * WEIGHTS["domain"], 1),
        "seniority": round(sen_score * WEIGHTS["seniority"], 1),
        "location": round(loc_score * WEIGHTS["location"], 1),
        "bonus": round(b_score * WEIGHTS["bonus"], 1),
        "_notes": {"seniority": sen_note, "location": loc_note},
    }
    total = sum(v for k, v in breakdown.items() if not k.startswith("_"))

    # An excluded company is a hard zero.
    for bad in profile.exclude_companies:
        if bad and _norm(bad) in _norm(company):
            total = 0.0
            breakdown["_notes"]["excluded"] = f"company on exclude list: {bad}"

    # A posting whose title matches nothing you do is noise no matter how many
    # of your keywords appear somewhere in a long description.
    if t_score == 0.0:
        total *= 0.45
        breakdown["_notes"]["title"] = "no title-cluster match (score damped)"

    # Subject-matter gate. A generic title plus transferable skills plus
    # "remote" is otherwise enough to clear 60 - which is how a voice-security
    # firm's "Research Scientist II" outranked actual hazards roles, on the
    # strength of Python, statistics, and the word "mitigation" used to mean
    # fraud mitigation. Work in this field names several domain terms; one
    # stray hit is coincidence, not evidence.
    if d_score == 0.0:
        total *= 0.50
        breakdown["_notes"]["domain"] = "no subject-matter overlap (score halved)"
    elif d_score <= 0.25:
        total *= 0.70
        breakdown["_notes"]["domain"] = "only one domain term (score damped)"

    flags = eligibility_flags(f"{title} {description}", profile)

    # Salary floor, if you set one.
    smin, smax = parse_salary(job.get("salary_raw") or description[:4000])
    if smin:
        flags["salary_min_seen"] = smin
    if profile.min_salary and smax and smax < profile.min_salary:
        flags["below_salary_floor"] = True

    matched_terms = (
        [f"title:{t}" for t in t_matched]
        + [f"skill:{s}" for s in s_matched[:18]]
        + [f"domain:{d}" for d in d_matched[:10]]
        + [f"bonus:{b}" for b in b_matched[:6]]
    )

    return {
        "score": round(min(100.0, max(0.0, total)), 2),
        "breakdown": breakdown,
        "matched": matched_terms,
        "missing": s_missing,
        "flags": flags,
        "salary_min": smin,
        "salary_max": smax,
    }


def rescore_all(db, profile: Profile, only_new: bool = False,
                region_key: str | None = None) -> int:
    """Rescore stored jobs. Run after editing the profile or changing region."""
    sql = "SELECT id, title, company, description, location, remote, salary_raw FROM jobs"
    if only_new:
        sql += " WHERE score = 0"
    rows = db.conn.execute(sql).fetchall()
    n = 0
    for row in rows:
        job = dict(row)
        result = score_job(job, profile, region_key)
        db.update_score(row["id"], result["score"], result["breakdown"],
                        result["matched"], result["missing"], result["flags"])
        n += 1
    db.conn.commit()
    return n


def explain(job_row, profile: Profile, region_key: str | None = None) -> str:
    """Human-readable justification for one job's score."""
    import json

    job = dict(job_row)
    result = score_job(job, profile, region_key)
    b = result["breakdown"]
    notes = b.get("_notes", {})

    lines = [
        f"{job.get('title')} @ {job.get('company')}",
        f"SCORE {result['score']}/100",
        "",
        f"  title      {b['title']:>5} / {WEIGHTS['title']:<4}",
        f"  skills     {b['skills']:>5} / {WEIGHTS['skills']:<4}",
        f"  domain     {b['domain']:>5} / {WEIGHTS['domain']:<4}",
        f"  seniority  {b['seniority']:>5} / {WEIGHTS['seniority']:<4}  ({notes.get('seniority','')})",
        f"  location   {b['location']:>5} / {WEIGHTS['location']:<4}  ({notes.get('location','')})",
        f"  bonus      {b['bonus']:>5} / {WEIGHTS['bonus']:<4}",
    ]
    if "title" in notes:
        lines.append(f"  ! {notes['title']}")
    if "excluded" in notes:
        lines.append(f"  ! {notes['excluded']}")

    lines += ["", "MATCHED: " + ", ".join(result["matched"][:22])]
    if result["missing"]:
        lines.append("NOT ASKED FOR: " + ", ".join(result["missing"][:10]))

    warn = [k for k in ("needs_citizenship", "needs_clearance", "no_sponsorship",
                        "status_only", "below_salary_floor") if result["flags"].get(k)]
    if warn:
        lines += ["", "FLAGS: " + ", ".join(warn)]
        for ev in result["flags"].get("evidence", []):
            lines.append(f"   {ev}")
    return "\n".join(lines)
