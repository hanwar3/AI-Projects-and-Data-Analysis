"""Tests for the parts where a silent bug would waste real time.

Run:  python tests/test_jobhunt.py
No pytest required - it prints a pass/fail table and exits non-zero on failure.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunt.db import DB, fingerprint  # noqa: E402
from jobhunt.match import (  # noqa: E402
    eligibility_flags, parse_salary, score_job, score_title,
)
from jobhunt.profile import Profile, resume_bullets  # noqa: E402
from jobhunt import regions  # noqa: E402
from jobhunt.sources.ats import strip_html  # noqa: E402
from jobhunt.sources.discover import from_url, identity_matches  # noqa: E402
from jobhunt.sources.intl import ADZUNA_COUNTRIES  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, got, want, note: str = "") -> None:
    ok = got == want
    RESULTS.append((ok, name, note or f"got {got!r}, want {want!r}"))


def check_true(name: str, got, note: str = "") -> None:
    RESULTS.append((bool(got), name, note or f"got {got!r}"))


PROFILE = Profile.load(Path(__file__).parent.parent / "profiles" / "example.yaml")


# ----------------------------------------------------------------- dedupe
def test_fingerprint() -> None:
    # The same role from three sources must collapse to one row.
    a = fingerprint("Senior Research Analyst", "Urban Institute", "Washington, DC")
    b = fingerprint("Research Analyst", "Urban Institute", "Washington, DC, USA")
    check("dedupe: seniority prefix ignored", a, b)

    c = fingerprint("Research Analyst", "RAND Corporation", "Washington, DC")
    check_true("dedupe: different company stays distinct", a != c)

    d = fingerprint("Research Analyst (Hybrid)", "Urban Institute", "Washington, DC")
    check("dedupe: parenthetical ignored", a, d)


# ---------------------------------------------------------------- scoring
def test_scoring_ranks_correctly() -> None:
    good = {
        "title": "Hazard Mitigation Planner", "company": "Dewberry",
        "location": "Fairfax, VA",
        "description": ("FEMA hazard mitigation assistance, qualitative research, "
                        "stakeholder engagement, community resilience, flood risk, "
                        "disaster recovery, ArcGIS. PhD preferred. 3+ years."),
    }
    noise = {
        "title": "Senior Software Engineer", "company": "Stripe",
        "location": "San Francisco, CA",
        "description": "Go and Ruby payment APIs. 5+ years backend. Distributed systems.",
    }
    g = score_job(good, PROFILE)["score"]
    n = score_job(noise, PROFILE)["score"]
    check_true("scoring: strong match scores high", g >= 75, f"score={g}")
    check_true("scoring: irrelevant job scores low", n <= 15, f"score={n}")
    check_true("scoring: gap is decisive", g - n >= 55, f"{g} vs {n}")


def test_title_damping() -> None:
    # Keyword soup in a description must not fake a match on its own.
    stuffed = {
        "title": "Warehouse Associate", "company": "Acme",
        "location": "Remote",
        "description": " ".join(PROFILE.all_skills()) + " " + " ".join(PROFILE.domains),
    }
    s = score_job(stuffed, PROFILE)["score"]
    check_true("scoring: keyword stuffing is damped", s < 55, f"score={s}")


def test_domain_gate() -> None:
    """A real job in the field names several domain terms; one is coincidence.

    Without this gate a voice-security firm's "Research Scientist II" scored
    63 - beating actual hazards roles - on an exact title match, transferable
    Python/statistics skills, "remote", and the word "mitigation" used to mean
    fraud mitigation.
    """
    decoy = {
        "title": "Research Scientist II", "company": "Pindrop",
        "location": "Remote, USA",
        "description": ("Voice security and fraud mitigation research. Python, "
                        "statistics, machine learning, deep learning. PhD in ML."),
    }
    real = {
        "title": "Disaster Risk Reduction Specialist", "company": "UNDP",
        "location": "Islamabad, Pakistan",
        "description": ("Community-based disaster risk management, flood early "
                        "warning, resilience, climate adaptation, hazard mitigation."),
    }
    d = score_job(decoy, PROFILE)
    r = score_job(real, PROFILE)
    check_true("domain gate: single-term decoy is damped", d["score"] < 50, f"{d['score']}")
    check_true("domain gate: damping is recorded",
               "domain" in d["breakdown"].get("_notes", {}))
    check_true("domain gate: real field job outranks decoy even off-location",
               r["score"] > d["score"], f"real={r['score']} decoy={d['score']}")

    blank = {"title": "Research Analyst", "company": "Ruby Labs", "location": "Europe",
             "description": "Product analytics, SQL, dashboards, A/B testing."}
    b = score_job(blank, PROFILE)
    check_true("domain gate: zero-domain job scores low", b["score"] < 25, f"{b['score']}")


def test_signal_noise_separation() -> None:
    """The default `top --min 45` must cleanly divide real matches from noise."""
    good = [
        {"title": "Hazard Mitigation Planner", "company": "Dewberry",
         "location": "Fairfax, VA",
         "description": ("FEMA hazard mitigation assistance. Qualitative research, "
                         "stakeholder engagement, community resilience, flood risk, "
                         "disaster recovery, ArcGIS. PhD preferred. 3+ years.")},
        {"title": "Emergency Management Specialist", "company": "FEMA",
         "location": "Washington, DC",
         "description": ("Emergency operations center, incident command system, "
                         "preparedness planning, interagency disaster response.")},
        {"title": "Disaster Risk Reduction Specialist", "company": "UNDP",
         "location": "Islamabad, Pakistan",
         "description": ("Community-based disaster risk management, flood early "
                         "warning, resilience, climate adaptation, hazard mitigation.")},
    ]
    noise = [
        {"title": "Research Scientist II", "company": "Pindrop", "location": "Remote",
         "description": "Voice security, fraud mitigation, Python, machine learning."},
        {"title": "Senior Software Engineer", "company": "Stripe", "location": "SF",
         "description": "Go and Ruby payment APIs. Distributed systems."},
        {"title": "Registered Nurse - ICU", "company": "HCA", "location": "Austin, TX",
         "description": "Critical care nursing. BSN required."},
    ]
    good_scores = [score_job(j, PROFILE)["score"] for j in good]
    noise_scores = [score_job(j, PROFILE)["score"] for j in noise]
    check_true("separation: every real match clears 45", min(good_scores) >= 45,
               f"lowest={min(good_scores)}")
    check_true("separation: no noise clears 45", max(noise_scores) < 45,
               f"highest={max(noise_scores)}")


def test_excluded_title_zeroes() -> None:
    score, matched = score_title("Account Executive, Enterprise Sales", PROFILE)
    check("scoring: excluded title -> 0", score, 0.0)


# ------------------------------------------------------------ eligibility
def test_eligibility_flags() -> None:
    cases = [
        ("Must be a U.S. Citizen to apply.", "needs_citizenship"),
        ("US citizenship is required for this position.", "needs_citizenship"),
        ("Requires an active Secret clearance.", "needs_clearance"),
        ("Must possess an active TS/SCI clearance", "needs_clearance"),
        ("We are unable to sponsor or take over sponsorship of employment visas.",
         "no_sponsorship"),
        ("This role does not offer visa sponsorship.", "no_sponsorship"),
        ("Open to status candidates only.", "status_only"),
    ]
    for text, key in cases:
        flags = eligibility_flags(text, PROFILE)
        check_true(f"flag: {key} in {text[:36]!r}", flags.get(key))

    clean = eligibility_flags(
        "We welcome applicants of all backgrounds and support work authorization.",
        PROFILE)
    check_true("flag: no false positive on neutral text",
               not any(clean.get(k) for k in
                       ("needs_citizenship", "needs_clearance", "no_sponsorship")))


def test_salary_parsing() -> None:
    check("salary: comma form", parse_salary("$99,200 - $128,956 per year"),
          (99200.0, 128956.0))
    check("salary: k form", parse_salary("$85k-$105k"), (85000.0, 105000.0))
    check("salary: absent", parse_salary("competitive salary"), (None, None))


# --------------------------------------------------------------- discover
def test_url_parsing() -> None:
    cases = [
        ("https://boards.greenhouse.io/gitlab", ("greenhouse", "gitlab")),
        ("https://job-boards.greenhouse.io/wri/jobs/123", ("greenhouse", "wri")),
        ("https://jobs.lever.co/palantir/abc-def", ("lever", "palantir")),
        ("https://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),
        ("https://apply.workable.com/mercycorps/", ("workable", "mercycorps")),
        ("https://acme.recruitee.com/o/engineer", ("recruitee", "acme")),
    ]
    for url, want in cases:
        check(f"url: {url[:44]}", from_url(url), want)
    check("url: unrecognised returns None",
          from_url("https://careers.example.com/jobs"), None)


def test_identity_matching() -> None:
    # Slug guessing produces convincing collisions; these must be rejected.
    check_true("identity: rejects Climate Corps for Climate Central",
               not identity_matches("Climate Central", "Climate Corps", "cc"))
    check_true("identity: rejects Sands Capital for Save the Children",
               not identity_matches("Save the Children", "Sands Capital Horizons", "sc"))
    check_true("identity: rejects 'Do Not Use' board",
               not identity_matches("IEM", "Industrial Electric Manufacturing (Do Not Use)", "iem"))
    check_true("identity: accepts exact match",
               identity_matches("Tetra Tech", "Tetra Tech", "tetratech"))
    check_true("identity: accepts acronym slug with full name",
               identity_matches("World Resources Institute", "World Resources Institute", "wri"))
    check_true("identity: accepts unspaced name",
               identity_matches("Mercy Corps", "mercycorps", "mercycorps"))
    check_true("identity: rejects short unverifiable slug",
               not identity_matches("Abt Global", None, "abt"))


# ----------------------------------------------------------------- resume
def test_resume_reflow() -> None:
    if not PROFILE.resume_text():
        import pytest
        pytest.skip("no resume_path set in the profile")
    bullets = resume_bullets(PROFILE.resume_text())
    check_true("resume: bullets extracted", len(bullets) >= 10, f"{len(bullets)} found")
    # A bullet cut at a PDF line break would end mid-word; real ones are long
    # and complete.
    longest = max(bullets, key=len)
    check_true("resume: bullets are reflowed, not line-truncated",
               len(longest) > 200, f"longest={len(longest)} chars")
    check_true("resume: no contact-header leakage",
               not any("anwar_haider@tamu.edu" in b for b in bullets))


def test_html_stripping() -> None:
    check("html: double-encoded entities resolved",
          strip_html("<p>one of&amp;nbsp;Time100&amp;rsquo;s</p>"),
          "one of Time100’s")
    check("html: list becomes dashes",
          strip_html("<ul><li>alpha</li><li>beta</li></ul>"), "- alpha\n- beta")


# --------------------------------------------------------------------- db
def test_db_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = DB(Path(tmp) / "t.db")
        job = {"source": "test", "title": "Resilience Planner",
               "company": "City of Austin", "location": "Austin, TX",
               "description": "hazard mitigation and floodplain work"}
        jid, is_new = db.upsert_job(job)
        check_true("db: first insert is new", is_new)

        _, again = db.upsert_job(job)
        check_true("db: second insert deduped", not again)

        db.set_status(jid, "applied", detail="test")
        row = db.get_job(jid)
        check("db: status persisted", row["status"], "applied")
        check_true("db: applied_at stamped", bool(row["applied_at"]))
        check_true("db: event logged", len(db.events_for(jid)) >= 1)

        # A re-scrape must not clobber pipeline state.
        db.upsert_job(job)
        check("db: re-scrape preserves status", db.get_job(jid)["status"], "applied")

        db.log_run("linkedin", "q", 10, 5, 0.42)
        check("db: spend tracked", round(db.spend_this_month(), 2), 0.42)
        db.close()


# ---------------------------------------------------------------- regions
def test_regions_resolve() -> None:
    for key in regions.PRESETS:
        r = regions.get(key)
        check_true(f"region {key}: has a label", bool(r.label))
        check_true(f"region {key}: has sources", bool(r.sources))
        check_true(f"region {key}: sources are real",
                   set(r.sources) <= set(regions.ALL_SOURCES),
                   f"unknown: {set(r.sources) - set(regions.ALL_SOURCES)}")
        if r.adzuna_country:
            check_true(f"region {key}: adzuna country is served",
                       r.adzuna_country in ADZUNA_COUNTRIES, r.adzuna_country)

    try:
        regions.get("atlantis")
        RESULTS.append((False, "region: unknown key raises", "no exception"))
    except KeyError:
        RESULTS.append((True, "region: unknown key raises", ""))


def test_region_source_gating() -> None:
    # USAJOBS is US-only; it must not be offered elsewhere.
    for key in ("uk", "europe", "canada", "gulf", "south_asia", "africa",
                "australia_nz", "global_remote", "humanitarian_global"):
        check_true(f"region {key}: no USAJOBS",
                   not regions.get(key).enabled("usajobs"))
    for key in ("us", "us_dc", "us_texas"):
        check_true(f"region {key}: has USAJOBS", regions.get(key).enabled("usajobs"))

    # Regions Adzuna does not serve must not list it.
    for key in ("gulf", "global_remote", "humanitarian_global"):
        check_true(f"region {key}: no Adzuna", not regions.get(key).enabled("adzuna"))

    # Indeed needs a country anchor, so a worldwide-remote region skips it.
    check_true("region global_remote: no Indeed",
               not regions.get("global_remote").enabled("indeed"))


def test_region_overrides() -> None:
    override = {"uk": {"locations": ["Edinburgh"], "metro_terms": ["edinburgh"]}}
    r = regions.get("uk", override)
    check("region override: locations replaced", r.locations, ["Edinburgh"])
    check("region override: untouched field kept", r.country, "gb")

    custom = {"geneva": {"label": "Geneva", "country": "ch",
                         "metro_terms": ["geneva"], "sources": ["reliefweb"]}}
    g = regions.get("geneva", custom)
    check("region custom: label", g.label, "Geneva")
    check("region custom: sources", g.sources, ["reliefweb"])


def test_location_scoring_follows_region() -> None:
    from jobhunt.match import score_location

    london = {"title": "Research Analyst", "company": "ODI", "location": "London, UK"}
    dc = {"title": "Research Analyst", "company": "Urban", "location": "Washington, DC"}

    dc_in_us, _ = score_location(dc, PROFILE, "us_dc")
    london_in_us, _ = score_location(london, PROFILE, "us_dc")
    dc_in_uk, _ = score_location(dc, PROFILE, "uk")
    london_in_uk, _ = score_location(london, PROFILE, "uk")

    check_true("location: DC scores top in us_dc", dc_in_us == 1.0, f"{dc_in_us}")
    check_true("location: London scores low in us_dc", london_in_us < 0.5, f"{london_in_us}")
    check_true("location: London scores top in uk", london_in_uk == 1.0, f"{london_in_uk}")
    check_true("location: DC scores low in uk", dc_in_uk < 0.5, f"{dc_in_uk}")


def test_region_changes_job_score() -> None:
    london_job = {
        "title": "Disaster Risk Reduction Specialist", "company": "ODI",
        "location": "London, United Kingdom",
        "description": ("Disaster risk reduction, resilience, climate adaptation, "
                        "qualitative research and stakeholder engagement."),
    }
    us = score_job(london_job, PROFILE, "us_dc")["score"]
    uk = score_job(london_job, PROFILE, "uk")["score"]
    check_true("region: London job scores higher under uk than us_dc",
               uk > us, f"uk={uk} us={us}")


def main() -> int:
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((False, f"{fn.__name__} raised", f"{type(exc).__name__}: {exc}"))

    failed = [r for r in RESULTS if not r[0]]
    for ok, name, note in RESULTS:
        if not ok:
            print(f"  FAIL  {name}\n         {note}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
