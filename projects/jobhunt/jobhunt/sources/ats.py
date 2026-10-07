"""Company ATS boards - free, unlimited, and the highest-signal source.

Most employers publish their open roles as public JSON on whichever applicant
tracking system they use. No key, no rate limit, no scraping. If an employer
matters to you, watching their board directly beats any aggregator: you see the
posting the hour it goes up, and the apply link is the real one rather than a
LinkedIn repost.

Supported: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Iterator

import requests

TIMEOUT = 25
UA = {"User-Agent": "Mozilla/5.0 (compatible; jobhunt/1.0)"}


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    # Greenhouse and friends often double-encode, so a single unescape leaves
    # literal "&nbsp;" in the text - which then pollutes the scoring corpus.
    # Unescape until it stops changing.
    text = raw
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(br|/p|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)   # no trailing space on a line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get(url: str, **kw) -> Any:
    resp = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------ Greenhouse
def greenhouse(slug: str, company: str) -> Iterator[dict]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        yield {
            "source": "greenhouse",
            "source_id": str(j.get("id")),
            "title": j.get("title", ""),
            "company": company,
            "location": loc,
            "remote": "remote" if "remote" in loc.lower() else None,
            "description": strip_html(j.get("content")),
            "url": j.get("absolute_url"),
            "apply_url": j.get("absolute_url"),
            "posted_at": j.get("updated_at") or j.get("first_published"),
            "raw": j,
        }


# ----------------------------------------------------------------------- Lever
def lever(slug: str, company: str) -> Iterator[dict]:
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        yield {
            "source": "lever",
            "source_id": j.get("id"),
            "title": j.get("text", ""),
            "company": company,
            "location": loc,
            "remote": "remote" if "remote" in (loc + str(cats.get("commitment", ""))).lower() else None,
            "description": strip_html(j.get("descriptionPlain") or j.get("description")) + "\n"
                           + strip_html(j.get("additionalPlain") or j.get("additional")),
            "url": j.get("hostedUrl"),
            "apply_url": j.get("applyUrl") or j.get("hostedUrl"),
            "employment_type": cats.get("commitment"),
            "seniority": cats.get("department"),
            "posted_at": _ms_to_iso(j.get("createdAt")),
            "raw": j,
        }


# ----------------------------------------------------------------------- Ashby
def ashby(slug: str, company: str) -> Iterator[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        yield {
            "source": "ashby",
            "source_id": j.get("id"),
            "title": j.get("title", ""),
            "company": company,
            "location": j.get("location") or "",
            "remote": "remote" if j.get("isRemote") else None,
            "description": strip_html(j.get("descriptionHtml") or j.get("descriptionPlain")),
            "url": j.get("jobUrl"),
            "apply_url": j.get("applyUrl") or j.get("jobUrl"),
            "employment_type": j.get("employmentType"),
            "seniority": j.get("department"),
            "salary_raw": comp.get("compensationTierSummary"),
            "posted_at": j.get("publishedAt"),
            "raw": j,
        }


# -------------------------------------------------------------- SmartRecruiters
def smartrecruiters(slug: str, company: str) -> Iterator[dict]:
    offset, page_size = 0, 100
    while True:
        data = _get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit={page_size}&offset={offset}"
        )
        items = data.get("content", [])
        if not items:
            break
        for j in items:
            loc = j.get("location") or {}
            city = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            yield {
                "source": "smartrecruiters",
                "source_id": j.get("id"),
                "title": j.get("name", ""),
                "company": company,
                "location": city,
                "remote": "remote" if loc.get("remote") else None,
                "description": "",   # detail needs a second call; title+company is enough to match on
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "apply_url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "posted_at": j.get("releasedDate"),
                "raw": j,
            }
        offset += page_size
        if offset >= data.get("totalFound", 0) or offset > 1000:
            break


# -------------------------------------------------------------------- Workable
def workable(slug: str, company: str) -> Iterator[dict]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    for j in data.get("jobs", []):
        yield {
            "source": "workable",
            "source_id": j.get("shortcode"),
            "title": j.get("title", ""),
            "company": company,
            "location": j.get("location", {}).get("location_str") if isinstance(j.get("location"), dict) else j.get("location"),
            "remote": "remote" if j.get("telecommuting") else None,
            "description": strip_html(j.get("description")),
            "url": j.get("url") or j.get("application_url"),
            "apply_url": j.get("application_url") or j.get("url"),
            "employment_type": j.get("employment_type"),
            "posted_at": j.get("published_on"),
            "raw": j,
        }


# -------------------------------------------------------------------- Recruitee
def recruitee(slug: str, company: str) -> Iterator[dict]:
    data = _get(f"https://{slug}.recruitee.com/api/offers/")
    for j in data.get("offers", []):
        yield {
            "source": "recruitee",
            "source_id": str(j.get("id")),
            "title": j.get("title", ""),
            "company": company,
            "location": j.get("location") or j.get("city"),
            "remote": "remote" if j.get("remote") else None,
            "description": strip_html(j.get("description")) + "\n" + strip_html(j.get("requirements")),
            "url": j.get("careers_url") or j.get("careers_apply_url"),
            "apply_url": j.get("careers_apply_url") or j.get("careers_url"),
            "employment_type": j.get("employment_type_code"),
            "posted_at": j.get("published_at"),
            "raw": j,
        }


# Workday boards do not expose a stable public JSON board API the way the
# others do; they need a POST against a tenant-specific endpoint that changes
# shape per employer. Rather than ship something that silently breaks, we skip
# them and let the LinkedIn source pick those employers up.
def _unsupported(slug: str, company: str) -> Iterator[dict]:
    raise NotImplementedError(
        "Workday boards have no stable public API. Track this employer via the "
        "LinkedIn source instead, or add its jobs manually."
    )
    yield  # pragma: no cover


PROVIDERS: dict[str, Callable[[str, str], Iterator[dict]]] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "recruitee": recruitee,
    "workday": _unsupported,
}

# Providers that return a clean 404 for a slug that does not exist. For these,
# a 200 response proves the board is real even when it currently lists nothing.
#
# SmartRecruiters is deliberately absent: its public postings endpoint answers
# 200 with ``totalFound: 0`` for *every* string, real or invented, so an empty
# response there proves nothing. We only trust it when it returns actual jobs.
STRICT_404 = {"greenhouse", "lever", "ashby", "workable", "recruitee"}


def fetch_board(ats: str, slug: str, company: str) -> list[dict]:
    fn = PROVIDERS.get(ats.lower())
    if fn is None:
        raise ValueError(f"unsupported ATS {ats!r}; known: {sorted(PROVIDERS)}")
    return list(fn(slug, company))


def probe(ats: str, slug: str) -> tuple[bool, str, int]:
    """Check whether a board slug is real.

    Returns ``(exists, detail, job_count)``. ``exists`` is only ``True`` when
    the response actually proves the board exists - see ``STRICT_404``.
    """
    ats = ats.lower()
    try:
        jobs = fetch_board(ats, slug, slug)
    except NotImplementedError as exc:
        return False, str(exc).split(".")[0], 0
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        return False, f"HTTP {code}", 0
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", 0

    n = len(jobs)
    if ats in STRICT_404:
        return True, f"{n} jobs", n
    # Unverifiable provider: only a non-empty board is evidence.
    if n > 0:
        return True, f"{n} jobs", n
    return False, "empty (unverifiable on this ATS)", 0


def board_identity(ats: str, slug: str) -> str | None:
    """The company name a board declares for itself.

    Slug guessing produces convincing-looking collisions - ``greenhouse/cc`` is
    Climate Corps, not Climate Central; ``greenhouse/sc`` is Sands Capital, not
    Save the Children. Discovery uses this to reject a board whose own name has
    nothing to do with the employer we were looking for.
    """
    ats = ats.lower()
    try:
        if ats == "greenhouse":
            data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            return data.get("name")

        if ats == "workable":
            data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            return (data.get("name") or "").strip() or None

        if ats == "recruitee":
            data = _get(f"https://{slug}.recruitee.com/api/offers/")
            for offer in data.get("offers", []):
                if offer.get("company_name"):
                    return offer["company_name"]
            return None

        if ats == "smartrecruiters":
            data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
            for item in data.get("content", []):
                name = (item.get("company") or {}).get("name")
                if name:
                    return name
            return None

        # Lever and Ashby expose no org name on their JSON APIs, so read the
        # public board page's <title> instead.
        page_url = {
            "lever": f"https://jobs.lever.co/{slug}",
            "ashby": f"https://jobs.ashbyhq.com/{slug}",
        }.get(ats)
        if page_url:
            resp = requests.get(page_url, headers=UA, timeout=TIMEOUT)
            if resp.status_code != 200:
                return None
            m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.S | re.I)
            if not m:
                return None
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
            # Titles look like "Acme Corp - Jobs" or "Jobs at Acme Corp".
            title = re.sub(r"\s*[|\-–]\s*(jobs|careers|job board).*$", "", title, flags=re.I)
            title = re.sub(r"^(jobs|careers)\s+at\s+", "", title, flags=re.I)
            return title.strip() or None
    except Exception:
        return None
    return None


def _ms_to_iso(ms: Any) -> str | None:
    if not ms:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None
