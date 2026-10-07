"""ReliefWeb - UN OCHA's humanitarian job board. Worldwide, free.

For disaster risk reduction, emergency management, recovery and resilience
work outside the US, this is the single highest-signal source there is. UN
agencies, INGOs, donors and national societies all post here, and almost none
of them reach LinkedIn in any useful way.

Setup (free, one form):
    https://apidoc.reliefweb.int/parameters#appname
ReliefWeb asks you to register an appname so they can contact you if a query
misbehaves. Once approved, put it in .env:

    RELIEFWEB_APPNAME=your-registered-appname

Without it the API returns 403, and this source is skipped with a note.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterator

import requests

BASE = "https://api.reliefweb.int/v2/jobs"
TIMEOUT = 30


def appname() -> str:
    return os.getenv("RELIEFWEB_APPNAME", "").strip()


def available() -> bool:
    return bool(appname())


def search(query: str, country: str = "", limit: int = 50,
           experience: str | None = None) -> Iterator[dict]:
    """Search ReliefWeb jobs.

    ``query`` is matched against title and body; ``country`` is the ReliefWeb
    country facet name (e.g. "Pakistan"), not an ISO code.
    """
    name = appname()
    if not name:
        raise RuntimeError(
            "RELIEFWEB_APPNAME is not set. Register a free appname at "
            "https://apidoc.reliefweb.int/parameters#appname and add it to .env"
        )

    payload: dict[str, Any] = {
        "limit": min(limit, 200),
        "profile": "full",
        "sort": ["date.created:desc"],
        "query": {"value": query, "operator": "AND"},
    }
    filters = []
    if country:
        filters.append({"field": "country.name", "value": country})
    if experience:
        filters.append({"field": "experience.name", "value": experience})
    if filters:
        payload["filter"] = {"operator": "AND", "conditions": filters}

    resp = requests.post(
        BASE,
        params={"appname": name},
        json=payload,
        headers={"User-Agent": "jobhunt/1.0", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    for item in resp.json().get("data", []):
        yield _normalise(item)


def _names(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, dict):
        values = [values]
    return [v.get("name", "") for v in values if isinstance(v, dict) and v.get("name")]


def _normalise(item: dict) -> dict:
    f = item.get("fields", {}) or {}
    orgs = _names(f.get("source"))
    countries = _names(f.get("country"))
    city = f.get("city")
    location = ", ".join(x for x in [city if isinstance(city, str) else None,
                                     "; ".join(countries[:2])] if x)

    body = f.get("body") or f.get("body-html") or ""
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"[ \t]+", " ", body)

    career = _names(f.get("career_categories"))
    theme = _names(f.get("theme"))
    extra = " ".join(career + theme)

    date = f.get("date") or {}
    return {
        "source": "reliefweb",
        "source_id": str(item.get("id") or f.get("id") or ""),
        "title": f.get("title", ""),
        "company": orgs[0] if orgs else "Humanitarian organisation",
        "location": location or "; ".join(countries[:2]) or "Multiple locations",
        "remote": "remote" if "remote" in (location + " " + f.get("title", "")).lower() else None,
        "description": (body + ("\n\n" + extra if extra else "")).strip(),
        "url": f.get("url"),
        "apply_url": f.get("url"),
        "employment_type": "; ".join(_names(f.get("type"))),
        "seniority": "; ".join(_names(f.get("experience"))),
        "posted_at": date.get("created"),
        "raw": {
            "closing": date.get("closing"),
            "countries": countries,
            "career_categories": career,
            "themes": theme,
        },
    }
