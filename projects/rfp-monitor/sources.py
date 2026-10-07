"""
sources.py -- one fetch_* function per source, all registered in ALL_SOURCES.

Each fetcher returns a list of dicts shaped like
    {"source", "type", "title", "detail", "country", "deadline", "ref", "url"}
plus optional "agency" (issuing body) and "posted" (publication date).

Fetchers only fetch and reshape. The keyword filter, the notice_id
no-duplicate rule, date normalisation and scoring all live in monitor.py,
so adding a source never touches them: write a function, decorate it with
@source("Name") and append it to ALL_SOURCES.

Failure contract: a fetcher that fails outright raises. One that loses only
some of its queries raises PartialFetch(rows, message), so the rows it did
get are stored and the gap still lands in run_log.
"""
import html
import json
import re
import time
from datetime import date, timedelta
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from browser_fetch import browser_session
from config import KEYWORDS, MAX_AGE_DAYS_NO_DEADLINE, REQUEST_TIMEOUT
from matching import fold, norm_date

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class PartialFetch(Exception):
    """Some of a source's queries failed; .rows holds what the rest returned."""

    def __init__(self, rows, message):
        super().__init__(message)
        self.rows = rows


def source(label):
    """Attach the display name used in run_log and the dashboard."""
    def wrap(fn):
        fn.label = label
        return fn
    return wrap


# ------------------------------------------------------------------ helpers

def _session(**headers):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8"})
    s.headers.update(headers)
    retry = Retry(total=2, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _get(s, url, **kw):
    r = s.get(url, timeout=REQUEST_TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _post(s, url, **kw):
    r = s.post(url, timeout=REQUEST_TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _soup(markup):
    return BeautifulSoup(markup, "lxml")


def _text(node):
    """Whitespace-collapsed text of a BeautifulSoup node or a plain string."""
    s = node.get_text(" ") if hasattr(node, "get_text") else html.unescape(str(node or ""))
    return re.sub(r"\s+", " ", s).strip()


def _html_to_text(markup):
    return _text(_soup(markup)) if markup and "<" in markup else _text(markup)


def _each(queries, fetch_one, pause=0.3):
    """fetch_one(q) for every query, tolerating individual failures."""
    rows, errors = [], []
    for q in queries:
        try:
            rows.extend(fetch_one(q))
        except Exception as e:  # one bad query must not sink the whole source
            errors.append(f"{q!r}: {type(e).__name__}: {e}")
        time.sleep(pause)
    if errors and len(errors) == len(queries):
        raise RuntimeError(f"all {len(queries)} queries failed; first: {errors[0]}")
    if errors:
        raise PartialFetch(rows, f"{len(errors)}/{len(queries)} queries failed; first: {errors[0]}")
    return rows


def _minimal_terms(terms):
    """For substring searches, drop terms that contain another term
    ("flood" already returns everything "flash flood" would)."""
    folded = {t: fold(t) for t in terms}
    return [t for t in terms if not any(o != folded[t] and o in folded[t] for o in folded.values())]


def _rss_items(s, url):
    root = ET.fromstring(_get(s, url).content)
    for item in root.iter("item"):
        yield {
            "title": _text(item.findtext("title")),
            "link": (item.findtext("link") or "").strip(),
            "guid": (item.findtext("guid") or "").strip(),
            "description": _html_to_text(item.findtext("description") or ""),
            "categories": [_text(c.text) for c in item.findall("category") if c.text],
            "pubDate": (item.findtext("pubDate") or "").strip(),
        }


def _col_index(header_cells, word):
    return next((i for i, h in enumerate(header_cells) if word in h), None)


# --------------------------------------------------------------- World Bank

WB_API = "https://search.worldbank.org/api/v2/procnotices"
# Open opportunities only -- "Contract Award" is deliberately excluded.
WB_OPEN_TYPES = ["General Procurement Notice", "Specific Procurement Notice",
                 "Request for Expression of Interest", "Invitation for Bids",
                 "Invitation for Prequalification"]
WB_GROUPS = {"CS": "Consulting services", "NC": "Non-consulting services", "GO": "Goods", "CW": "Works"}
WB_FIELDS = ("id,notice_type,noticedate,submission_deadline_date,project_ctry_name,project_name,"
             "bid_description,procurement_group,procurement_method_name,contact_organization")


WB_PAGE_ROWS, WB_MAX_PAGES = 1000, 6


@source("World Bank projects")
def fetch_world_bank():
    """World Bank project procurement notices API (open, no key).

    Pages through the newest open notices (open types filtered server-side,
    newest first) until they are older than MAX_AGE_DAYS_NO_DEADLINE, and
    leaves keyword matching to the ingestion layer like every other source:
    ~4 requests per check instead of one per keyword, because the API
    answers bursts with HTTP 500s."""
    s = _session()
    cutoff = (date.today() - timedelta(days=MAX_AGE_DAYS_NO_DEADLINE)).isoformat()
    rows = []
    for page in range(WB_MAX_PAGES):
        try:
            notices = _wb_page(s, page * WB_PAGE_ROWS)
        except Exception as e:
            if not rows:
                raise
            raise PartialFetch(rows, f"stopped at page {page + 1}: {type(e).__name__}: {e}") from e
        for n in notices:
            if not isinstance(n, dict) or n.get("notice_type") not in WB_OPEN_TYPES:
                continue
            posted = norm_date(n.get("noticedate"))
            if not n.get("submission_deadline_date") and posted and posted < cutoff:
                continue  # old General Procurement Notice
            group = WB_GROUPS.get(n.get("procurement_group"), n.get("procurement_group") or "")
            rows.append({
                "source": "World Bank",
                "type": " · ".join(filter(None, [n.get("notice_type"), group])),
                "title": n.get("bid_description") or n.get("project_name") or "",
                "detail": " · ".join(filter(None, [n.get("project_name"), n.get("procurement_method_name")])),
                "country": n.get("project_ctry_name") or "",
                "deadline": n.get("submission_deadline_date") or "",
                "posted": posted,
                "agency": n.get("contact_organization") or "",
                "ref": n.get("id") or "",
                "url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{n.get('id')}",
            })
        dates = [d for d in (norm_date(n.get("noticedate")) for n in notices if isinstance(n, dict)) if d]
        if len(notices) < WB_PAGE_ROWS or (dates and min(dates) < cutoff):
            break
        time.sleep(1)
    return rows


def _wb_page(s, offset):
    """One page of open notices, retrying the non-JSON replies the API sometimes sends."""
    params = {"format": "json", "rows": WB_PAGE_ROWS, "os": offset, "fl": WB_FIELDS,
              "notice_type_exact": "^".join(WB_OPEN_TYPES)}
    for attempt in range(3):
        r = _get(s, WB_API, params=params)
        try:
            notices = r.json().get("procnotices") or []
            break
        except ValueError:
            if attempt == 2:
                raise RuntimeError(f"non-JSON reply (HTTP {r.status_code}): {r.text[:80]!r}")
            time.sleep(5 * (attempt + 1))
    return list(notices.values()) if isinstance(notices, dict) else notices  # dict shape seen before


# ----------------------------------------------------------------------- ADB

ADB_FEEDS = {  # "Contracts Awarded" skipped: that ship has sailed
    "Procurement Notices": "https://feeds.feedburner.com/procurement-notices",
    "Consulting Services Recruitment": "https://feeds.feedburner.com/adb-csrn",
    "Advance Notices": "https://feeds.feedburner.com/adb-advanced-notices",
    "Invitation for Bids": "https://feeds.feedburner.com/adb-invitation-for-bids",
    "Invitation for Prequalification": "https://feeds.feedburner.com/adb-invitation-for-prequalification",
}


@source("Asian Development Bank")
def fetch_adb():
    """ADB RSS tender feeds (open). They carry no dates, so nothing expires here."""
    s = _session()

    def one(feed_name):
        out = []
        for it in _rss_items(s, ADB_FEEDS[feed_name]):
            m = re.search(r"\b\d{3,5}-([A-Z]{3})\b", it["title"])  # "Loan 3265-IND: ..." -> IND
            out.append({"source": "ADB", "type": feed_name, "title": it["title"],
                        "detail": " · ".join(it["categories"] + [it["description"]]).strip(" ·"),
                        "country": m.group(1) if m else "", "deadline": "", "agency": "ADB",
                        "ref": it["guid"] or it["link"], "url": it["link"]})
        return out

    return _each(list(ADB_FEEDS), one, pause=0)


# ---------------------------------------------------------------------- UNGM

UNGM_PAGE = "https://www.ungm.org/Public/Notice"
UNGM_SEARCH = "https://www.ungm.org/Public/Notice/Search"
UNGM_PAGE_SIZE = 15  # the site ignores larger values


def _ungm_body(**extra):
    """UNGM's search now rejects empty dates: it wants notices published up to
    today with a deadline from today on, sorted by deadline."""
    today = date.today().strftime("%d-%b-%Y")
    return {"PageIndex": 0, "PageSize": UNGM_PAGE_SIZE, "Title": "", "Description": "", "Reference": "",
            "PublishedFrom": "", "PublishedTo": today, "DeadlineFrom": today, "DeadlineTo": "",
            "Countries": [], "Agencies": [], "UNSPSCs": [], "NoticeTypes": [], "SortField": "Deadline",
            "SortAscending": True, "isPicker": False, "IsSustainable": False, "IsActive": True,
            "NoticeDisplayType": None, "NoticeSearchTotalLabelId": "noticeSearchTotal",
            "TypeOfCompetitions": [], **extra}


@source("UN Global Marketplace")
def fetch_ungm():
    """Every UN agency that publishes on UNGM (UNDP, UNICEF, WFP, FAO, UNOPS,
    IOM, UNHCR, UN-Habitat, UNDRR, UNEP, WHO ...). Public search, no login.

    The search POST is rejected (HTTP 400) unless it carries the anti-forgery
    token and session cookies from the notices page, so one GET sets those up
    first. Its title search matches substrings, so the minimal keyword set
    covers the longer phrases too."""
    s = _session(**{"X-Requested-With": "XMLHttpRequest", "Referer": UNGM_PAGE,
                    "Origin": "https://www.ungm.org"})
    page = _get(s, UNGM_PAGE)
    token = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page.text)
    if not token:
        raise RuntimeError("no __RequestVerificationToken on the notices page -- layout changed")
    s.headers["RequestVerificationToken"] = token.group(1)

    def one(kw):
        out = []
        for page_index in range(20):
            r = _post(s, UNGM_SEARCH, json=_ungm_body(PageIndex=page_index, Title=kw))
            rows = _soup(r.text).select("div.tableRow.dataRow")
            out += [n for n in map(_ungm_row, rows) if n]
            if len(rows) < UNGM_PAGE_SIZE:
                break
            time.sleep(0.3)
        return out

    return _each(_minimal_terms(KEYWORDS), one)


def _ungm_row(row):
    nid = row.get("data-noticeid")
    cells = [_text(c) for c in row.select("div.tableCell")[1:]]  # first cell holds buttons
    if not nid or len(cells) < 7:
        return None
    title, deadline, published, agency, kind, ref, country = cells[:7]
    return {"source": "UNGM", "type": kind, "title": re.sub(r"\s*Open in a new window\s*$", "", title),
            "detail": f"{agency} · {ref}", "country": country,
            "deadline": deadline.split(" ")[0],  # "21-Sep-2026 14:00 (GMT 5.00) ..."
            "posted": published, "agency": agency, "ref": nid,
            "url": f"https://www.ungm.org/Public/Notice/{nid}"}


# ---------------------------------------------------------------------- UNDP

@source("UNDP procurement notices")
def fetch_undp():
    """UNDP's own notice board (~500 live notices on one static page)."""
    base = "https://procurement-notices.undp.org/"
    soup = _soup(_get(_session(), base).text)
    out = []
    for a in soup.select("a.vacanciesTableLink"):
        f = {}
        for cell in a.select("div.vacanciesTable__cell"):
            label = cell.select_one(".vacanciesTable__cell__label")
            if label:
                key = _text(label).lower()
                label.extract()
                f[key] = _text(cell)
        office = f.get("undp office/country", "")
        url = urljoin(base, a.get("href", ""))
        out.append({"source": "UNDP", "type": f.get("process", ""), "title": f.get("title", ""),
                    "detail": office, "country": office.split("/", 1)[-1].strip().title(),
                    "deadline": f.get("deadline", ""), "posted": f.get("posted", ""), "agency": "UNDP",
                    "ref": f.get("ref no") or url, "url": url})
    if not out:
        raise RuntimeError("no notices parsed -- the page layout may have changed")
    return out


# ------------------------------------------------------- World Bank corporate

WBG_RFX = "https://wbgeprocure-rfxnow.worldbank.org/rfxnow"


@source("World Bank Group corporate")
def fetch_wbg_corporate():
    """The WBG's own consultancy procurement (GFDRR, CIF, IFC ...), separate
    from project notices: the public feed behind the RFxNow ads page."""
    ads = _get(_session(), f"{WBG_RFX}/json/advertisement/activeAdvertisements.json").json()
    return [{"source": "WBG corporate", "type": "Corporate procurement · EOI",
             "title": a.get("procurementTitle") or "",
             "detail": _html_to_text(a.get("text") or "")[:1500], "country": "",
             "deadline": a.get("extendedDeadlineProposal") or a.get("eoiDeadline") or "",
             "posted": a.get("publicationDate") or "", "agency": "World Bank Group",
             "ref": str(a.get("procurementNumber") or a.get("id")),
             "url": f"{WBG_RFX}/public/advertisement/{a.get('id')}/view.html"}
            for a in ads.get("advertisementList", [])]


# ---------------------------------------------------------------------- AfDB

AFDB_HUB = "https://www.afdb.org/en/projects-and-operations/procurement"
# Notices are titled "EOI - Somalia - ...", "AMI - Sénégal - ...", "PPM - RDC - ..."
AFDB_NOTICE = re.compile(r"^(EOI|REOI|AMI|AAO|API|SPN|GPN|PPM|RFP|RFQ|IFB|ITB)\b[\s-]", re.I)


@source("African Development Bank")
def fetch_afdb():
    """AfDB's RSS feeds now sit behind a Cloudflare challenge (403 to any
    script, even from inside a browser page), but the procurement page itself
    renders, so the notices are read from there in a real browser."""
    with browser_session() as br:
        links = br.rows(AFDB_HUB, """() => [...document.querySelectorAll("a[href*='/documents/']")]
            .map(a => [a.textContent.trim(), a.getAttribute('href')])""", wait_ms=5000)
    out, seen = [], set()
    for title, href in links or []:
        title = _text(title)
        if not href or href in seen or len(title) < 30 or not AFDB_NOTICE.search(title):
            continue
        seen.add(href)
        parts = [p.strip() for p in title.split(" - ")]
        out.append({"source": "AfDB", "type": parts[0][:30], "title": title, "detail": "",
                    "country": parts[1] if len(parts) >= 3 else "", "deadline": "", "agency": "AfDB",
                    "ref": href, "url": urljoin("https://www.afdb.org", href)})
    return out


# ---------------------------------------------------------------------- EBRD

EBRD = "https://www.ebrd.com"
EBRD_FORM = {"parentPath": "/content/ebrd_dxp/uk/en/home/work-with-us/project-procurement/procurement-notices",
             "cardType": "procurement-notices", "eventSort": "", "sortBy": "", "filters": "",
             "countryFilters": "", "sectorFilters": "", "topicFilters": "", "statusFilters": "",
             "noticeTypeFilters": "", "pageTypeFilters": "", "startDate": "", "endDate": "",
             "IsLoggedIn": "false", "isAlumni": "false", "isBeeps": "false"}


@source("EBRD")
def fetch_ebrd():
    """EBRD project procurement notices through the same public listing
    service the notices page calls (10 results per page)."""
    s = _session(**{"X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{EBRD}/home/work-with-us/project-procurement/procurement-notices.html"})
    # AEM expects its public CSRF token on POSTs; any real failure shows up in the POSTs below.
    token = _get(s, f"{EBRD}/libs/granite/csrf/token.json").json().get("token")
    if token:
        s.headers["CSRF-Token"] = token

    def one(kw):
        out = []
        for page in range(1, 4):
            res = _post(s, f"{EBRD}/bin/ebrd_dxp/filterlistservlet",
                        data=dict(EBRD_FORM, searchKey=kw, currentPage=page)).json().get("searchResult") or []
            for n in res:
                out.append({"source": "EBRD", "type": n.get("projectNoticeType") or n.get("projectContractType") or "",
                            "title": n.get("title") or "",
                            "detail": " · ".join(filter(None, [n.get("projectSector"), n.get("projectContractType")])),
                            "country": n.get("projectCountry") or "", "deadline": n.get("projectCloseDate") or "",
                            "posted": n.get("projectIssueDate") or "", "agency": "EBRD",
                            "ref": n.get("pagePath") or n.get("projectUrl") or "",
                            "url": n.get("projectUrl") or urljoin(EBRD, n.get("pagePath") or "")})
            if len(res) < 10:
                break
        return out

    return _each(_minimal_terms(KEYWORDS), one)


# ---------------------------------------------------------------------- IsDB

@source("Islamic Development Bank")
def fetch_isdb():
    base = "https://www.isdb.org"
    s, out, seen = _session(), [], set()
    for page in range(10):
        articles = _soup(_get(s, f"{base}/project-procurement/tenders", params={"page": page}).text) \
            .select("article.display-teaser")
        fresh = [a for a in articles if a.get("about") not in seen]
        if not fresh:
            break
        for a in fresh:
            seen.add(a.get("about"))
            if _text(a.select_one(".field--name-field-tender-status") or "").lower() != "active":
                continue
            link = a.select_one(".field-title a[href]")
            if not link:
                continue
            out.append({"source": "IsDB", "type": _text(a.select_one(".field--name-field-tender-type") or ""),
                        "title": _text(link), "detail": "",
                        "country": _text(a.select_one(".field--name-field-world-country") or ""),
                        "deadline": _text(a.select_one(".field--name-field-close-date") or ""),
                        "agency": "IsDB", "ref": a.get("about") or link["href"], "url": urljoin(base, link["href"])})
        time.sleep(0.3)
    return out


# ----------------------------------------------------------------------- CDB

@source("Caribbean Development Bank")
def fetch_cdb():
    base = "https://www.caribank.org"
    soup = _soup(_get(_session(), f"{base}/work-with-us/procurement/procurement-notices").text)
    out = []
    for tr in soup.select("table tr"):
        link = tr.select_one("td.views-field-field-cdb-role-service a[href]")
        if not link:
            continue

        def cell(name):
            return _text(tr.select_one(f"td.views-field-{name}") or "")

        when = tr.select_one("td.views-field-field-date-of-approval time")
        out.append({"source": "CDB", "type": cell("field-cdb-contract-awards-type"), "title": _text(link),
                    "detail": cell("field-sector-tag"), "country": cell("field-cdb-country-tag"),
                    "deadline": (when.get("datetime") if when else "") or cell("field-date-of-approval"),
                    "agency": "Caribbean Development Bank", "ref": link["href"], "url": urljoin(base, link["href"])})
    return out


# ------------------------------------------------------ EU Funding & Tenders

EU_SEARCH = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EU_QUERY = {"bool": {"must": [{"terms": {"type": ["0", "1", "2", "8"]}},
                              {"terms": {"status": ["31094501", "31094502"]}}]}}  # forthcoming, open
EU_TYPES = {"0": "EU tender", "1": "EU grant topic", "2": "EU grant call", "8": "EU cascade funding"}
EU_STATUS = {"31094501": "forthcoming", "31094502": "open"}


@source("EU Funding & Tenders")
def fetch_eu_funding_tenders():
    """Horizon Europe (incl. Cluster 3 disaster-resilient society), ECHO,
    INTPA and other EU grants and tenders. 'SEDIA' is the portal's own public
    search key, embedded in its web page -- not a personal credential."""
    s = _session()
    form = {k: ("blob", json.dumps(v), "application/json")
            for k, v in {"query": EU_QUERY, "languages": ["en"],
                         "sort": {"field": "sortStatus", "order": "ASC"}}.items()}

    def one(kw):
        res = _post(s, EU_SEARCH, files=form, params={"apiKey": "SEDIA", "pageSize": 100, "pageNumber": 1,
                                                        "text": f'"{kw}"' if " " in kw else kw}).json()
        out = []
        for x in res.get("results", []):
            md = x.get("metadata") or {}

            def first(key):
                return str((md.get(key) or [""])[0])

            out.append({"source": "EU Funding & Tenders",
                        "type": " · ".join(filter(None, [EU_TYPES.get(first("type"), "EU"),
                                                          EU_STATUS.get(first("status"), "")])),
                        "title": first("title") or _text(x.get("summary")),
                        "detail": " · ".join(filter(None, [first("callTitle"), first("typesOfAction")])),
                        "country": "EU / international", "deadline": first("deadlineDate"),
                        "posted": first("startDate"), "agency": "European Commission",
                        "ref": first("identifier") or x.get("reference") or "", "url": x.get("url") or ""})
        return out

    return _each(KEYWORDS, one, pause=0.2)


# ---------------------------------------------------------------- Grants.gov

@source("Grants.gov (US federal)")
def fetch_grants_gov():
    """US federal funding (FEMA, NSF, NOAA, HUD, DHS ...): open search2 API, no key."""
    s = _session()

    def one(kw):
        d = _post(s, "https://api.grants.gov/v1/api/search2",
                  json={"keyword": kw, "oppStatuses": "forecasted|posted", "rows": 100}).json()
        if d.get("errorcode"):
            raise RuntimeError(d.get("msg") or f"errorcode {d.get('errorcode')}")
        return [{"source": "Grants.gov", "type": f"US federal grant · {h.get('oppStatus', '')}",
                 "title": h.get("title") or "",
                 "detail": " · ".join(filter(None, [h.get("agency"), h.get("number")])),
                 "country": "United States", "deadline": h.get("closeDate") or "",
                 "posted": h.get("openDate") or "", "agency": h.get("agency") or "",
                 "ref": str(h.get("id") or h.get("number") or ""),
                 "url": f"https://www.grants.gov/search-results-detail/{h.get('id')}"}
                for h in (d.get("data") or {}).get("oppHits", [])]

    return _each(KEYWORDS, one, pause=0.2)


# ---------------------------------------------------------------------- IUCN

IUCN_PORTAL = "https://procurement.iucn.org/"


@source("IUCN")
def fetch_iucn():
    """iucn.org now answers 403 to scripts (Drupal antibot), so the tenders
    come from IUCN's separate procurement portal, which renders its list
    with JavaScript."""
    with browser_session() as br:
        rows = br.rows(IUCN_PORTAL, """() => [...document.querySelectorAll('[role=row]')].map(r => {
            const cells = [...r.querySelectorAll('[role=gridcell], .ag-cell')]
                .map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim());
            const a = r.querySelector('a[href]');
            return [cells, a ? a.href : ''];
        }).filter(x => x[0].length > 2)""", wait_ms=9000)
    # Grid columns: deadline | RfP title | IUCN office | country | duration | category
    out = []
    for cells, link in rows or []:
        deadline, title = cells[0], re.sub(r"\s*(OUTLINE|APPLY)\s*", " ", cells[1]).strip()
        if len(title) < 10:
            continue
        out.append({"source": "IUCN", "type": cells[5] if len(cells) > 5 else "Tender / RfP",
                    "title": title, "detail": " · ".join(c for c in cells[2:5] if c),
                    "country": cells[3] if len(cells) > 3 else "", "deadline": deadline,
                    "agency": f"IUCN {cells[2]}".strip() if len(cells) > 2 else "IUCN",
                    "ref": fold(title)[:200], "url": link or IUCN_PORTAL})
    return out


# ---------------------------------------------------------------------- ADPC

@source("Asian Disaster Preparedness Center")
def fetch_adpc():
    page = "https://www.adpc.net/ver25/procurement-notice.asp"
    soup = _soup(_get(_session(), page).text)
    out = []
    for tr in soup.find_all("tr"):
        tds = [_text(td) for td in tr.find_all("td")]
        # live notices have 5 cells starting with a ref like "RFQ-25-184"; awards have 8
        if len(tds) != 5 or not re.match(r"^[A-Z]{2,5}-\d{2}-\d+", tds[0]):
            continue
        a = tr.find("a", href=True)
        href = a["href"].lstrip("#") if a else ""
        out.append({"source": "ADPC", "type": tds[3], "title": tds[1], "detail": tds[2], "country": "",
                    "deadline": tds[4], "agency": "ADPC", "ref": tds[0],
                    "url": urljoin(page, href) if href else page})
    return out


# --------------------------------------------------------------------- CDEMA

@source("CDEMA")
def fetch_cdema():
    base = "https://www.cdema.org"
    soup = _soup(_get(_session(), f"{base}/index.php/opportunities").text)
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/opportunities/\d+-external/")):
        url = urljoin(base, a["href"])
        if url not in seen and _text(a):
            seen.add(url)
            out.append({"source": "CDEMA", "type": "Opportunity", "title": _text(a), "detail": "",
                        "country": "Caribbean", "deadline": "", "agency": "CDEMA", "ref": url, "url": url})
    return out


# -------------------------------------------------------------- WWF-Pakistan

@source("WWF-Pakistan")
def fetch_wwf_pakistan():
    page = "https://www.wwfpak.org/jobs_/procurement_of_goods_and_works/"
    soup = _soup(_get(_session(), page).text)
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/downloads/", re.I)):
        href = a["href"]
        url = "https:" + href if href.startswith("//") else urljoin(page, href)
        title = re.sub(r"\s+(pdf|docx?|xlsx?)\s+[\d.,]+\s*[KM]B\s*$", "", _text(a), flags=re.I)
        if url in seen or len(title) < 8:
            continue
        seen.add(url)
        out.append({"source": "WWF-Pakistan", "type": "Tender / RFP", "title": title, "detail": "",
                    "country": "Pakistan", "deadline": "", "agency": "WWF-Pakistan", "ref": url, "url": url})
    return out


# --------------------------------------------------------- Green Climate Fund

GCF_URL = ("https://iaayou.fa.ocs.oraclecloud.com/fscmUI/faces/NegotiationAbstracts"
           "?prcBuId=300000003621906")


@source("Green Climate Fund")
def fetch_gcf():
    """GCF's tender list is an Oracle page that only exists after JavaScript
    runs, so it is read through the shared browser layer."""
    with browser_session() as br:
        rows = br.rows(GCF_URL, "() => [...document.querySelectorAll('tr')]"
                                ".map(tr => [...tr.cells].map(c => c.innerText.trim()))",
                       wait_ms=3000, wait_until="networkidle")
    out, seen = [], set()
    for cells in rows:
        if len(cells) < 7 or not re.match(r"^RFx\d+", cells[0]):
            continue
        number, title, kind, status, posted, _opened, closes = cells[:7]
        base_no = number.split(",")[0]  # "RFx202600037,2" is amendment 2 of RFx202600037
        if status.lower() != "active" or base_no in seen:
            continue
        seen.add(base_no)
        out.append({"source": "GCF", "type": f"Corporate {kind}", "title": title, "detail": "", "country": "",
                    "deadline": closes, "posted": posted, "agency": "Green Climate Fund",
                    "ref": base_no, "url": GCF_URL})
    if not any(len(c) >= 7 and str(c[0]).startswith("RFx") for c in rows):
        raise RuntimeError("tender table did not render")
    return out


# ----------------------------------------------------------------------- IFRC

IFRC_PAGE = "https://www.ifrc.org/our-promise/global-humanitarian-services/business-opportunities"
_DATE_IN_TEXT = r"(\d{1,2}\s+\w{3,9}\s+20\d\d|20\d\d-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d\d)"


@source("IFRC")
def fetch_ifrc():
    """IFRC answers 403 to scripts, but its business-opportunities page
    renders in a browser: each tender is a heading followed by paragraphs
    carrying the reference number and the closing date."""
    with browser_session() as br:
        items = br.rows(IFRC_PAGE, """() => [...document.querySelectorAll('h2, h3, h4')].map(h => {
            let body = '', n = h.nextElementSibling, i = 0;
            while (n && !/^H[1-4]$/.test(n.tagName) && i < 8) { body += ' ' + (n.innerText || ''); n = n.nextElementSibling; i++; }
            const a = h.parentElement && h.parentElement.querySelector('a[href$=".pdf"], a[href*="tender"], a[href*="rfq"]');
            return [h.textContent.trim(), body.replace(/\\s+/g, ' ').trim().slice(0, 700), a ? a.href : ''];
        }).filter(x => x[0].length > 20 && /tender notice|request for|rfq|rfp|quotation|invitation to bid/i.test(x[0]))""", wait_ms=6000)
    out = []
    for title, body, link in items or []:
        title, body = _text(title), _text(body)
        ref = re.search(r"\b(RF[QP]-[A-Z0-9/-]+|ITB-[A-Z0-9/-]+|Sealed Bid [A-Z0-9/-]+)", body, re.I)
        closing = re.search(r"(?:deadline|closing|submission)[^.]{0,60}?" + _DATE_IN_TEXT, body, re.I)
        out.append({"source": "IFRC", "type": "Tender", "title": title, "detail": body,
                    "country": "", "deadline": closing.group(1) if closing else "", "agency": "IFRC",
                    "ref": ref.group(1) if ref else fold(title)[:150], "url": link or IFRC_PAGE})
    return out


# ------------------------------------------------------------------ ReliefWeb

RELIEFWEB_API = "https://api.reliefweb.int/v2/jobs"
RELIEFWEB_THEMES = ["Disaster Risk Reduction", "Climate Change and Environment",
                    "Recovery and Reconstruction"]


@source("ReliefWeb consultancies")
def fetch_reliefweb():
    """Humanitarian consultancies and calls, filtered to your themes.

    ReliefWeb's API needs a free approved appname (a two-minute request at
    apidoc.reliefweb.int/parameters#appname). Set RELIEFWEB_APPNAME in
    config.py to switch this source on."""
    appname = (getattr(config, "RELIEFWEB_APPNAME", "") or "").strip()
    if not appname:
        raise RuntimeError("no RELIEFWEB_APPNAME in config.py -- source switched off")
    fields = ["title", "url", "date.closing", "source.shortname", "type.name", "country.name",
              "career_categories.name", "theme.name"]
    d = _get(_session(), RELIEFWEB_API, params={
        "appname": appname, "limit": 200, "filter[field]": "theme.name",
        "filter[value][]": RELIEFWEB_THEMES, "fields[include][]": fields,
        "sort[]": "date.created:desc"}).json()

    def first(value, key="name"):
        if isinstance(value, list) and value:
            return (value[0] or {}).get(key, "") if isinstance(value[0], dict) else str(value[0])
        return ""

    out = []
    for item in d.get("data", []):
        f = item.get("fields") or {}
        out.append({"source": "ReliefWeb", "type": first(f.get("type")) or "Job / consultancy",
                    "title": f.get("title") or "",
                    "detail": " · ".join(filter(None, [first(f.get("source"), "shortname"),
                                                       first(f.get("career_categories")),
                                                       first(f.get("theme"))])),
                    "country": first(f.get("country")),
                    "deadline": (f.get("date") or {}).get("closing") or "",
                    "agency": first(f.get("source"), "shortname") or "ReliefWeb",
                    "ref": str(item.get("id") or f.get("url") or ""), "url": f.get("url") or ""})
    return out


# Register every fetcher here -- the ingestion loop just iterates this list.
ALL_SOURCES = [
    fetch_world_bank,
    fetch_adb,
    fetch_ungm,
    fetch_undp,
    fetch_wbg_corporate,
    fetch_afdb,
    fetch_ebrd,
    fetch_isdb,
    fetch_cdb,
    fetch_eu_funding_tenders,
    fetch_grants_gov,
    fetch_iucn,
    fetch_ifrc,
    fetch_adpc,
    fetch_cdema,
    fetch_wwf_pakistan,
    fetch_gcf,
    fetch_reliefweb,
]
