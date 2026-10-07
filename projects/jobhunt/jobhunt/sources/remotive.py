"""Remotive - a free, keyless feed of remote roles.

Small compared with LinkedIn, but it costs nothing and surfaces remote
research/policy/analyst positions that never reach the big boards.
"""

from __future__ import annotations

import re
from typing import Iterator

import requests

BASE = "https://remotive.com/api/remote-jobs"
TIMEOUT = 30


def search(query: str, limit: int = 50) -> Iterator[dict]:
    resp = requests.get(
        BASE,
        params={"search": query, "limit": limit},
        headers={"User-Agent": "jobhunt/1.0"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    for j in resp.json().get("jobs", []):
        yield {
            "source": "remotive",
            "source_id": str(j.get("id")),
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("candidate_required_location") or "Remote",
            "remote": "remote",
            "description": _strip(j.get("description")),
            "url": j.get("url"),
            "apply_url": j.get("url"),
            "salary_raw": j.get("salary") or None,
            "employment_type": j.get("job_type"),
            "posted_at": j.get("publication_date"),
            "raw": {"category": j.get("category"), "query": query},
        }


def _strip(html_text: str | None) -> str:
    if not html_text:
        return ""
    import html as html_mod

    text = html_mod.unescape(html_text)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(br|/p|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
