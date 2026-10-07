"""USAJOBS - every federal opening, free.

You live in Reston, which puts FEMA, DHS, NOAA, HUD and the whole federal
hazards apparatus inside commuting distance, so this source earns its place.

Get a key (takes about two minutes, no cost):
    https://developer.usajobs.gov/apirequest
Then either export it or put it in ``.env``:
    USAJOBS_EMAIL=you@example.com
    USAJOBS_KEY=xxxxxxxx

A caveat worth knowing before you spend time here: most federal postings are
open only to US citizens, and many further restrict to "status candidates"
(existing federal employees). The scorer flags both, so citizenship-restricted
listings are marked rather than silently mixed in with the rest.
"""

from __future__ import annotations

import os
from typing import Iterator

import requests

BASE = "https://data.usajobs.gov/api/search"
TIMEOUT = 30


def _headers() -> dict[str, str]:
    email = os.getenv("USAJOBS_EMAIL", "").strip()
    key = os.getenv("USAJOBS_KEY", "").strip()
    if not email or not key:
        raise RuntimeError(
            "USAJOBS needs USAJOBS_EMAIL and USAJOBS_KEY. "
            "Free key: https://developer.usajobs.gov/apirequest"
        )
    return {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}


def search(keyword: str, location: str | None = None, results_per_page: int = 50,
           pages: int = 1, remote: bool = False) -> Iterator[dict]:
    headers = _headers()
    for page in range(1, pages + 1):
        params: dict[str, str | int] = {
            "Keyword": keyword,
            "ResultsPerPage": min(results_per_page, 500),
            "Page": page,
            "SortField": "opendate",
            "SortDirection": "desc",
        }
        if location:
            params["LocationName"] = location
        if remote:
            params["RemoteIndicator"] = "True"

        resp = requests.get(BASE, headers=headers, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json().get("SearchResult", {})
        items = payload.get("SearchResultItems", [])
        if not items:
            return

        for item in items:
            d = item.get("MatchedObjectDescriptor", {})
            yield _normalise(d)

        if len(items) < params["ResultsPerPage"]:
            return


def _normalise(d: dict) -> dict:
    locations = d.get("PositionLocation") or []
    loc_names = [l.get("LocationName", "") for l in locations if l.get("LocationName")]
    remuneration = (d.get("PositionRemuneration") or [{}])[0]

    user_area = (d.get("UserArea") or {}).get("Details", {}) or {}
    body_parts = [
        d.get("QualificationSummary") or "",
        user_area.get("JobSummary") or "",
        user_area.get("MajorDuties") and " ".join(user_area["MajorDuties"]) or "",
        user_area.get("Requirements") or "",
        # This is the field that tells you whether you can actually apply.
        user_area.get("WhoMayApply", {}).get("Name", "")
        if isinstance(user_area.get("WhoMayApply"), dict) else str(user_area.get("WhoMayApply") or ""),
    ]

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "source": "usajobs",
        "source_id": d.get("PositionID"),
        "title": d.get("PositionTitle", ""),
        "company": (d.get("OrganizationName")
                    or d.get("DepartmentName") or "Federal Government"),
        "location": "; ".join(loc_names[:3]),
        "remote": "remote" if user_area.get("TeleworkEligible") else None,
        "description": "\n\n".join(p for p in body_parts if p),
        "url": d.get("PositionURI"),
        "apply_url": (d.get("ApplyURI") or [d.get("PositionURI")])[0],
        "salary_min": _num(remuneration.get("MinimumRange")),
        "salary_max": _num(remuneration.get("MaximumRange")),
        "salary_raw": f"{remuneration.get('MinimumRange','')}-{remuneration.get('MaximumRange','')} "
                      f"{remuneration.get('RateIntervalCode','')}".strip(" -"),
        "employment_type": "; ".join(
            t.get("Name", "") for t in (d.get("PositionSchedule") or [])
        ),
        "seniority": user_area.get("JobGradeCode") or "",
        "posted_at": d.get("PublicationStartDate"),
        "raw": {
            "close_date": d.get("ApplicationCloseDate"),
            "grade": f"{(d.get('JobGrade') or [{}])[0].get('Code','')}",
            "who_may_apply": user_area.get("WhoMayApply"),
        },
    }


def available() -> bool:
    return bool(os.getenv("USAJOBS_EMAIL") and os.getenv("USAJOBS_KEY"))
