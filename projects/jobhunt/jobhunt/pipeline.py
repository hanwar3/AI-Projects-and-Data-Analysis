"""Run a search: pull from every enabled source, dedupe, score, store.

Sources run cheapest-first so that when a budget cap or a network problem cuts
a run short, you still got the free results. Every source is isolated - one
failing board never stops the rest.

Which sources run at all depends on the active region. USAJOBS is meaningless
outside the US, Arbeitnow outside Europe, Adzuna outside the twenty countries
it serves. Rather than calling them and discarding the results, the region
declares what is worth trying.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Callable

from . import match
from .config import load_env
from .db import DB, bulk_upsert
from .profile import Profile
from .regions import Region
from .sources import apify, ats, intl, reliefweb, remotive, usajobs


@dataclass
class SourceResult:
    source: str
    found: int = 0
    new: int = 0
    cost: float = 0.0
    errors: list[str] = field(default_factory=list)
    skipped: str = ""


@dataclass
class RunReport:
    region: str = ""
    results: list[SourceResult] = field(default_factory=list)
    scored: int = 0

    @property
    def total_found(self) -> int:
        return sum(r.found for r in self.results)

    @property
    def total_new(self) -> int:
        return sum(r.new for r in self.results)

    @property
    def total_cost(self) -> float:
        return sum(r.cost for r in self.results)

    @property
    def errors(self) -> list[str]:
        return [f"{r.source}: {e}" for r in self.results for e in r.errors]


Logger = Callable[[str], None]


def _noop(msg: str) -> None:  # pragma: no cover
    pass


def _harvest(db: DB, res: SourceResult, jobs, label: str, log: Logger) -> None:
    total, new = bulk_upsert(db, jobs)
    res.found += total
    res.new += new
    if new:
        log(f"  {label}: {new} new ({total} found)")


# --------------------------------------------------------------- free sources
def run_ats(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    """Poll every watchlisted company board. Free and parallelisable."""
    res = SourceResult("ats")
    boards = db.watchlist(active_only=True)
    if not boards:
        log("  ats: watchlist is empty (jobhunt watch add-url <careers-url>)")
        return res

    def fetch(row):
        try:
            return row, ats.fetch_board(row["ats"], row["slug"], row["name"]), None
        except Exception as exc:
            return row, [], f"{row['name']}: {type(exc).__name__}: {exc}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for row, jobs, err in pool.map(fetch, boards):
            if err:
                res.errors.append(err)
                db.mark_watch(row["id"], ok=False, err=err)
                continue
            _harvest(db, res, jobs, f"ats/{row['name']}", log)
            db.mark_watch(row["id"], ok=True)

    db.log_run("ats", f"{len(boards)} boards", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_usajobs(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("usajobs")
    cfg = profile.sources.get("usajobs", {})
    if not cfg.get("enabled", True):
        return res
    if not usajobs.available():
        log("  usajobs: skipped (no USAJOBS_KEY - free at developer.usajobs.gov/apirequest)")
        res.skipped = "no key"
        return res

    queries = profile.all_titles()[: int(cfg.get("keyword_limit", 8))]
    per_page = int(cfg.get("results_per_page", 50))

    for q in queries:
        try:
            _harvest(db, res, list(usajobs.search(q, results_per_page=per_page)),
                     f"usajobs/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("usajobs", f"{len(queries)} queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_reliefweb(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    """UN OCHA's humanitarian board - the key source for DRR work worldwide."""
    res = SourceResult("reliefweb")
    cfg = profile.sources.get("reliefweb", {})
    if not cfg.get("enabled", True):
        return res
    if not reliefweb.available():
        log("  reliefweb: skipped (no RELIEFWEB_APPNAME - free at apidoc.reliefweb.int)")
        res.skipped = "no appname"
        return res

    queries = cfg.get("queries") or profile.all_titles()[:6]
    limit = int(cfg.get("limit_per_query", 40))
    country = region.reliefweb_country if cfg.get("use_region_country", True) else ""

    for q in queries:
        try:
            _harvest(db, res, list(reliefweb.search(q, country=country, limit=limit)),
                     f"reliefweb/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("reliefweb", f"{len(queries)} queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_adzuna(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("adzuna")
    cfg = profile.sources.get("adzuna", {})
    if not cfg.get("enabled", True):
        return res
    if not intl.adzuna_available():
        log("  adzuna: skipped (no ADZUNA_APP_ID/KEY - free at developer.adzuna.com/signup)")
        res.skipped = "no key"
        return res
    country = region.adzuna_country
    if not country:
        log(f"  adzuna: skipped (does not cover {region.label})")
        res.skipped = "region not covered"
        return res

    queries = profile.all_titles()[: int(cfg.get("max_queries", 8))]
    location = next((l for l in profile.search_locations() if l.lower() != "remote"), "")
    limit = int(cfg.get("limit_per_query", 50))

    for q in queries:
        try:
            _harvest(db, res,
                     list(intl.adzuna(q, country=country, location=location, limit=limit)),
                     f"adzuna/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("adzuna", f"{len(queries)} queries in {country}", res.found, res.new,
               0.0, ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_remotive(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("remotive")
    cfg = profile.sources.get("remotive", {})
    if not cfg.get("enabled", True):
        return res
    for q in cfg.get("queries", ["research analyst"]):
        try:
            _harvest(db, res, list(remotive.search(q)), f"remotive/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("remotive", "queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_jobicy(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("jobicy")
    cfg = profile.sources.get("jobicy", {})
    if not cfg.get("enabled", True):
        return res
    for q in cfg.get("queries", ["research", "data", "management"]):
        try:
            _harvest(db, res, list(intl.jobicy(q, geo=cfg.get("geo", ""))),
                     f"jobicy/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("jobicy", "queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_arbeitnow(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("arbeitnow")
    cfg = profile.sources.get("arbeitnow", {})
    if not cfg.get("enabled", True):
        return res
    for q in cfg.get("queries", ["research", "analyst", "climate"]):
        try:
            _harvest(db, res, list(intl.arbeitnow(q)), f"arbeitnow/{q}", log)
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("arbeitnow", "queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_themuse(db: DB, profile: Profile, region: Region, log: Logger) -> SourceResult:
    res = SourceResult("themuse")
    cfg = profile.sources.get("themuse", {})
    # Off by default: The Muse indexes big-brand corporate employers, which is
    # mostly noise for research and humanitarian searches.
    if not cfg.get("enabled", False):
        return res
    location = next((l for l in profile.search_locations() if l.lower() != "remote"), "")
    pages = int(cfg.get("pages", 2))
    categories = cfg.get("categories") or [""]
    for q in cfg.get("queries", ["analyst", "research", "policy"]):
        for page in range(pages):
            try:
                jobs = []
                for cat in categories:
                    jobs.extend(intl.themuse(q, location=location, page=page,
                                             category=cat))
                _harvest(db, res, jobs, f"themuse/{q}", log)
            except Exception as exc:
                res.errors.append(f"{q} p{page}: {type(exc).__name__}: {exc}")
                break
    db.log_run("themuse", "queries", res.found, res.new, 0.0,
               ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


# --------------------------------------------------------------- paid sources
def run_apify_linkedin(db: DB, profile: Profile, region: Region, log: Logger,
                       budget_left: float) -> SourceResult:
    res = SourceResult("linkedin")
    cfg = profile.sources.get("linkedin", {})
    if not cfg.get("enabled", False):
        return res
    if not apify.available():
        log("  linkedin: skipped (no APIFY_TOKEN)")
        res.skipped = "no token"
        return res

    actor = cfg.get("actor", "valig/linkedin-jobs-scraper")
    limit = int(cfg.get("limit_per_query", 40))
    queries = profile.all_titles()[: int(cfg.get("max_queries", 10))]
    location = region.linkedin_location or next(
        (l for l in profile.search_locations() if l.lower() != "remote"), "")

    for q in queries:
        cost = apify.estimate_cost(actor, limit)
        if cost > budget_left:
            msg = f"budget cap reached (${budget_left:.2f} left); stopped before '{q}'"
            log(f"  linkedin: {msg}")
            res.errors.append(msg)
            break
        try:
            jobs = list(apify.linkedin(
                q, location=location, limit=limit,
                date_posted=cfg.get("date_posted", "r604800"), actor=actor,
                title_exclude=profile.exclude_titles or None))
            total, new = bulk_upsert(db, jobs)
            actual = apify.estimate_cost(actor, total)
            res.found += total
            res.new += new
            res.cost += actual
            budget_left -= actual
            log(f"  linkedin/{q}: {new} new ({total} found, ~${actual:.3f})")
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("linkedin", f"{len(queries)} queries in {location}", res.found,
               res.new, res.cost, ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


def run_apify_indeed(db: DB, profile: Profile, region: Region, log: Logger,
                     budget_left: float) -> SourceResult:
    res = SourceResult("indeed")
    cfg = profile.sources.get("indeed", {})
    if not cfg.get("enabled", False):
        return res
    if not apify.available():
        res.skipped = "no token"
        return res

    actor = cfg.get("actor", "borderline/indeed-scraper")
    limit = int(cfg.get("limit_per_query", 25))
    queries = profile.all_titles()[: int(cfg.get("max_queries", 4))]
    country = region.country or "us"
    location = next((l for l in profile.search_locations() if l.lower() != "remote"), "")

    for q in queries:
        cost = apify.estimate_cost(actor, limit)
        if cost > budget_left:
            msg = f"budget cap reached (${budget_left:.2f} left); stopped before '{q}'"
            log(f"  indeed: {msg}")
            res.errors.append(msg)
            break
        try:
            jobs = list(apify.indeed(q, location=location, limit=limit, country=country,
                                     from_days=str(cfg.get("from_days", "7")), actor=actor))
            total, new = bulk_upsert(db, jobs)
            actual = apify.estimate_cost(actor, total)
            res.found += total
            res.new += new
            res.cost += actual
            budget_left -= actual
            log(f"  indeed/{q}: {new} new ({total} found, ~${actual:.3f})")
        except Exception as exc:
            res.errors.append(f"{q}: {type(exc).__name__}: {exc}")
    db.log_run("indeed", f"{len(queries)} queries in {country}", res.found, res.new,
               res.cost, ok=not res.errors, detail="; ".join(res.errors[:3]))
    return res


FREE_RUNNERS = {
    "ats": run_ats,
    "usajobs": run_usajobs,
    "reliefweb": run_reliefweb,
    "adzuna": run_adzuna,
    "remotive": run_remotive,
    "jobicy": run_jobicy,
    "arbeitnow": run_arbeitnow,
    "themuse": run_themuse,
}
PAID_RUNNERS = {
    "linkedin": run_apify_linkedin,
    "indeed": run_apify_indeed,
}
FREE_SOURCES = list(FREE_RUNNERS)


# ------------------------------------------------------------------- top level
def run_search(db: DB, profile: Profile, only: list[str] | None = None,
               region_key: str | None = None, log: Logger = _noop) -> RunReport:
    """Run every source the region enables, then score everything new."""
    load_env()
    region = profile.region(region_key)
    report = RunReport(region=region.key)

    budget_cap = float(profile.raw.get("budget_usd_per_month", 4.0))
    spent = db.spend_this_month()
    budget_left = max(0.0, budget_cap - spent)
    if spent > 0:
        log(f"Apify spend this month: ${spent:.2f} of ${budget_cap:.2f} cap")

    wanted = set(only) if only else None

    def should_run(name: str) -> bool:
        if wanted is not None:
            # An explicit --only overrides the region's source list.
            return name in wanted
        return region.enabled(name)

    for name, runner in FREE_RUNNERS.items():
        if should_run(name):
            report.results.append(runner(db, profile, region, log))
        elif wanted is None and name in FREE_SOURCES:
            report.results.append(
                SourceResult(name, skipped=f"not used in {region.key}"))

    for name, runner in PAID_RUNNERS.items():
        if should_run(name):
            r = runner(db, profile, region, log, budget_left)
            budget_left -= r.cost
            report.results.append(r)

    report.scored = match.rescore_all(db, profile, only_new=True, region_key=region_key)
    return report
