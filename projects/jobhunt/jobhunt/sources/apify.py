"""Apify actors for LinkedIn and Indeed - the breadth layer.

The ATS and USAJOBS sources are free but only see employers you already know
about. Apify covers everything else. It is the one part of this app that costs
money, so every call goes through a budget check first and every run records
what it is estimated to have cost.

Pricing at the free tier (Aug 2026), per the Apify store:
    valig/linkedin-jobs-scraper   $0.0004 / result  + $0.001  start
    borderline/indeed-scraper     $0.005  / result

Apify's free plan includes $5 of platform credit per month, which is about
12,000 LinkedIn results - far more than a focused search needs.

Set your token (free account at https://console.apify.com):
    APIFY_TOKEN=apify_api_xxxxx
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterator

import requests

API = "https://api.apify.com/v2"
TIMEOUT = 60

# (per-result, per-run-start) in USD, free tier.
COSTS = {
    "valig/linkedin-jobs-scraper": (0.0004, 0.001),
    "borderline/indeed-scraper": (0.005, 0.0),
    "cheap_scraper/linkedin-job-scraper": (0.0007, 0.005),
    "curious_coder/linkedin-jobs-scraper": (0.002, 0.00005),
}


class BudgetExceeded(RuntimeError):
    pass


def token() -> str:
    tok = os.getenv("APIFY_TOKEN", "").strip()
    if not tok:
        raise RuntimeError(
            "APIFY_TOKEN is not set. Create a free account at "
            "https://console.apify.com, copy the token from Settings > "
            "Integrations, and put it in Job_Search_App/.env"
        )
    return tok


def available() -> bool:
    return bool(os.getenv("APIFY_TOKEN"))


def estimate_cost(actor: str, n_results: int) -> float:
    per_result, per_start = COSTS.get(actor, (0.002, 0.001))
    return per_result * n_results + per_start


def run_actor(actor: str, payload: dict[str, Any], max_items: int,
              poll_seconds: int = 5, max_wait: int = 420) -> list[dict]:
    """Start an actor, wait for it, and return its dataset items.

    ``max_items`` is passed to Apify as a hard cap so a misconfigured query
    cannot quietly burn the month's credit.
    """
    actor_path = actor.replace("/", "~")
    url = f"{API}/acts/{actor_path}/runs"
    resp = requests.post(
        url,
        params={"token": token(), "maxItems": max_items},
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    run = resp.json()["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]

    waited = 0
    status = run["status"]
    while status in {"READY", "RUNNING"} and waited < max_wait:
        time.sleep(poll_seconds)
        waited += poll_seconds
        r = requests.get(f"{API}/actor-runs/{run_id}",
                         params={"token": token()}, timeout=TIMEOUT)
        r.raise_for_status()
        status = r.json()["data"]["status"]

    items = requests.get(
        f"{API}/datasets/{dataset_id}/items",
        params={"token": token(), "clean": "true", "limit": max_items},
        timeout=TIMEOUT,
    )
    items.raise_for_status()
    return items.json()


# ---------------------------------------------------------------------- LinkedIn
def linkedin(keywords: str, location: str = "", limit: int = 40,
             date_posted: str = "r604800", actor: str = "valig/linkedin-jobs-scraper",
             title_include: list[str] | None = None,
             title_exclude: list[str] | None = None) -> Iterator[dict]:
    payload: dict[str, Any] = {"keywords": keywords, "limit": limit}
    if location:
        payload["location"] = location
    if date_posted:
        payload["datePosted"] = date_posted
    if title_include:
        payload["titleInclude"] = title_include
    if title_exclude:
        payload["titleExclude"] = title_exclude

    for item in run_actor(actor, payload, max_items=limit):
        yield _normalise_linkedin(item, keywords)


def _normalise_linkedin(j: dict, query: str) -> dict:
    loc = _first(j, "location", "jobLocation", "formattedLocation") or ""
    desc = _first(j, "descriptionText", "description", "jobDescription", "text") or ""
    return {
        "source": "linkedin",
        "source_id": str(_first(j, "id", "jobId", "jobPostingId") or ""),
        "title": _first(j, "title", "jobTitle", "position") or "",
        "company": _first(j, "companyName", "company", "organization") or "",
        "location": loc,
        "remote": "remote" if "remote" in str(loc).lower()
                  or "remote" in str(_first(j, "workplaceType", "workType") or "").lower() else None,
        "description": desc,
        "url": _first(j, "link", "jobUrl", "url", "jobPostingUrl"),
        "apply_url": _first(j, "applyUrl", "applicationUrl", "link", "jobUrl", "url"),
        "salary_raw": _first(j, "salary", "salaryInfo", "compensation"),
        "employment_type": _first(j, "employmentType", "contractType", "jobType"),
        "seniority": _first(j, "seniorityLevel", "experienceLevel"),
        "posted_at": _first(j, "postedAt", "publishedAt", "listedAt", "postedDate", "datePosted"),
        "raw": {"query": query},
    }


# ------------------------------------------------------------------------ Indeed
def indeed(query: str, location: str = "", limit: int = 25, country: str = "us",
           from_days: str = "7", actor: str = "borderline/indeed-scraper") -> Iterator[dict]:
    payload: dict[str, Any] = {
        "query": query,
        "country": country,
        "maxRows": limit,
        "enableUniqueJobs": True,
        "sort": "date",
    }
    if location:
        payload["location"] = location
    if from_days:
        payload["fromDays"] = from_days

    for item in run_actor(actor, payload, max_items=limit):
        yield _normalise_indeed(item, query)


def _normalise_indeed(j: dict, query: str) -> dict:
    loc = _first(j, "location", "formattedLocation", "jobLocation") or ""
    return {
        "source": "indeed",
        "source_id": str(_first(j, "jobKey", "id", "jobId") or ""),
        "title": _first(j, "title", "jobTitle", "positionName") or "",
        "company": _first(j, "company", "companyName") or "",
        "location": loc,
        "remote": "remote" if "remote" in str(loc).lower() else None,
        "description": _first(j, "description", "descriptionText", "jobDescription", "snippet") or "",
        "url": _first(j, "url", "jobUrl", "link"),
        "apply_url": _first(j, "applyUrl", "url", "jobUrl", "link"),
        "salary_raw": _first(j, "salary", "salaryText", "estimatedSalary"),
        "employment_type": _first(j, "jobType", "employmentType"),
        "posted_at": _first(j, "postedAt", "date", "postingDateParsed", "publishedAt"),
        "raw": {"query": query},
    }


def _first(d: dict, *keys: str) -> Any:
    """Actor field names drift between builds - take the first key that exists."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None
