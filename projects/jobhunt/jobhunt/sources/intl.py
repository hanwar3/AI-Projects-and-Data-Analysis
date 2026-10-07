"""Keyless international sources, plus Adzuna.

Four small feeds that cost nothing and cover ground the US-centric sources
miss:

* **Adzuna**    20 countries, needs a free app_id/app_key
* **Arbeitnow** Germany and wider Europe, no key
* **Jobicy**    remote roles worldwide, no key
* **The Muse**  US and international offices of large employers, no key

None of them is large on its own. Together they make a non-US search viable
without spending anything.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Iterator

import requests

TIMEOUT = 30
UA = {"User-Agent": "Mozilla/5.0 (compatible; jobhunt/1.0)"}


def _strip(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(br|/p|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------- Adzuna
ADZUNA_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in", "it",
    "mx", "nl", "nz", "pl", "sg", "us", "za",
}


def adzuna_available() -> bool:
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def adzuna(query: str, country: str = "gb", location: str = "",
           limit: int = 50, max_days_old: int = 14) -> Iterator[dict]:
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if not (app_id and app_key):
        raise RuntimeError(
            "Adzuna needs ADZUNA_APP_ID and ADZUNA_APP_KEY. Free registration: "
            "https://developer.adzuna.com/signup"
        )
    country = (country or "gb").lower()
    if country not in ADZUNA_COUNTRIES:
        raise ValueError(f"Adzuna does not serve {country!r}. Covered: "
                         f"{', '.join(sorted(ADZUNA_COUNTRIES))}")

    params: dict[str, Any] = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "results_per_page": min(limit, 50),
        "max_days_old": max_days_old,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location

    resp = requests.get(
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
        headers=UA, params=params, timeout=TIMEOUT,
    )
    resp.raise_for_status()

    for j in resp.json().get("results", []):
        loc = (j.get("location") or {}).get("display_name", "")
        yield {
            "source": "adzuna",
            "source_id": str(j.get("id") or ""),
            "title": j.get("title", ""),
            "company": (j.get("company") or {}).get("display_name", ""),
            "location": loc,
            "remote": "remote" if "remote" in loc.lower() else None,
            "description": _strip(j.get("description")),
            "url": j.get("redirect_url"),
            "apply_url": j.get("redirect_url"),
            "salary_min": j.get("salary_min"),
            "salary_max": j.get("salary_max"),
            "salary_raw": (f"{j.get('salary_min')}-{j.get('salary_max')}"
                           if j.get("salary_min") else None),
            "employment_type": j.get("contract_time"),
            "posted_at": j.get("created"),
            "raw": {"category": (j.get("category") or {}).get("label"), "country": country},
        }


# ------------------------------------------------------------------- Arbeitnow
def arbeitnow(query: str = "", limit: int = 100) -> Iterator[dict]:
    """Germany and wider Europe. The feed is unfiltered, so we filter locally."""
    resp = requests.get("https://www.arbeitnow.com/api/job-board-api",
                        headers=UA, timeout=TIMEOUT)
    resp.raise_for_status()
    needle = query.lower().strip()
    n = 0

    for j in resp.json().get("data", []):
        title = j.get("title", "")
        desc = _strip(j.get("description"))
        if needle and needle not in f"{title} {desc}".lower():
            continue
        created = j.get("created_at")
        posted = None
        if created:
            try:
                from datetime import datetime, timezone
                posted = datetime.fromtimestamp(int(created), tz=timezone.utc).isoformat(
                    timespec="seconds")
            except (ValueError, TypeError, OSError):
                posted = None
        yield {
            "source": "arbeitnow",
            "source_id": j.get("slug"),
            "title": title,
            "company": j.get("company_name", ""),
            "location": j.get("location") or "Europe",
            "remote": "remote" if str(j.get("remote")).lower() == "true" else None,
            "description": desc,
            "url": j.get("url"),
            "apply_url": j.get("url"),
            "employment_type": ", ".join(j.get("job_types") or []),
            "posted_at": posted,
            "raw": {"tags": j.get("tags")},
        }
        n += 1
        if n >= limit:
            return


# ---------------------------------------------------------------------- Jobicy
def jobicy(query: str = "", geo: str = "", limit: int = 50) -> Iterator[dict]:
    """Remote roles worldwide. ``geo`` is a region slug such as 'europe'."""
    params: dict[str, Any] = {"count": min(limit, 50)}
    if query:
        params["tag"] = query
    if geo:
        params["geo"] = geo

    resp = requests.get("https://jobicy.com/api/v2/remote-jobs",
                        headers=UA, params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    for j in resp.json().get("jobs", []):
        yield {
            "source": "jobicy",
            "source_id": str(j.get("id") or ""),
            "title": j.get("jobTitle", ""),
            "company": j.get("companyName", ""),
            "location": j.get("jobGeo") or "Remote",
            "remote": "remote",
            "description": _strip(j.get("jobDescription") or j.get("jobExcerpt")),
            "url": j.get("url"),
            "apply_url": j.get("url"),
            "salary_raw": _jobicy_salary(j),
            "employment_type": ", ".join(j.get("jobType") or []) if isinstance(
                j.get("jobType"), list) else j.get("jobType"),
            "seniority": j.get("jobLevel"),
            "posted_at": j.get("pubDate"),
            "raw": {"industry": j.get("jobIndustry")},
        }


def _jobicy_salary(j: dict) -> str | None:
    lo, hi = j.get("annualSalaryMin"), j.get("annualSalaryMax")
    if lo and hi:
        return f"{j.get('salaryCurrency', '')} {lo}-{hi}".strip()
    return None


# -------------------------------------------------------------------- The Muse
def themuse(query: str = "", location: str = "", page: int = 0,
            limit: int = 40, category: str = "") -> Iterator[dict]:
    """Large employers, including their non-US offices.

    A note on what this is good for: The Muse indexes big-brand corporate
    employers, so it is strong for analyst and operations roles and weak for
    research, policy and humanitarian work. It ships disabled in the default
    profile for that reason - turn it on if you retarget toward the private
    sector.

    The API filters by location and category but not free text, so ``query``
    is applied locally against the title.
    """
    params: dict[str, Any] = {"page": page}
    if location:
        params["location"] = location
    if category:
        params["category"] = category

    resp = requests.get("https://www.themuse.com/api/public/jobs",
                        headers=UA, params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    needle = query.lower().strip()
    n = 0
    for j in resp.json().get("results", []):
        title = j.get("name", "")
        if needle and needle not in title.lower():
            continue
        locations = [l.get("name", "") for l in (j.get("locations") or [])]
        levels = [l.get("name", "") for l in (j.get("levels") or [])]
        company = (j.get("company") or {}).get("name", "")
        landing = (j.get("refs") or {}).get("landing_page")
        yield {
            "source": "themuse",
            "source_id": str(j.get("id") or ""),
            "title": title,
            "company": company,
            "location": "; ".join(locations[:3]),
            "remote": "remote" if any("flexible" in l.lower() or "remote" in l.lower()
                                      for l in locations) else None,
            "description": _strip(j.get("contents")),
            "url": landing,
            "apply_url": landing,
            "seniority": "; ".join(levels),
            "posted_at": j.get("publication_date"),
            "raw": {"categories": [c.get("name") for c in (j.get("categories") or [])]},
        }
        n += 1
        if n >= limit:
            return
