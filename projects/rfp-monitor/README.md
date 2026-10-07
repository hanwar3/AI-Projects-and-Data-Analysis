# RFP Monitor

Watches 17 public sources for open RFPs, EOIs and calls for proposals in
disaster risk reduction, resilience, climate adaptation and urban planning.
Everything lands in a local SQLite database with duplicates blocked at the
database level, and every check is written to a run log.

**Checks only run when you ask for one.** Press **Refresh now** in the
dashboard, or run `python monitor.py`. Nothing happens on a timer.

## Start it

Double-click **`Start RFP Monitor.bat`**. The dashboard opens at
<http://localhost:8501> showing everything found so far. Keep the black
window open (minimised is fine); close it to stop.

A full check takes a few minutes — UNGM alone runs ~60 keyword searches.

## Sources

| Source | What it covers | How |
|---|---|---|
| World Bank projects | Project procurement notices worldwide | Open API, newest pages |
| World Bank Group corporate | The Bank's own consultancies: GFDRR, CIF, IFC | Public JSON feed |
| UN Global Marketplace | ~40 UN bodies: UNDP, UNICEF, WFP, FAO, UNOPS, IOM, UNHCR, UN-Habitat, UNDRR, UNEP, WHO | Public search + anti-forgery token |
| UNDP procurement notices | UNDP's own notice board | HTML |
| Asian Development Bank | Procurement, consultant recruitment, advance notices, IFB/IFP | RSS |
| African Development Bank | Project and corporate notices, consultancy EOIs | Browser (Cloudflare) |
| EBRD | Project procurement notices | Public listing service |
| Islamic Development Bank | Project tenders | HTML |
| Caribbean Development Bank | Procurement notices | HTML |
| Green Climate Fund | Corporate tenders | Browser (Oracle page) |
| EU Funding & Tenders | Horizon Europe (incl. disaster-resilient society), ECHO, INTPA | Portal search API |
| Grants.gov | US federal funding: FEMA, NSF, NOAA, HUD, DHS | Open API |
| IUCN | Open tenders | Browser (procurement portal grid) |
| IFRC | Current tenders | Browser (403 to scripts) |
| ADPC | Procurement notices | HTML |
| CDEMA | External opportunities | HTML |
| WWF-Pakistan | Procurement of goods and works | HTML |
| ReliefWeb *(off)* | Humanitarian consultancies in your themes | Needs a free appname — see below |

No logins and no personal API keys. The EU portal's `apiKey=SEDIA` is the
portal's own public key, embedded in its web page.

### Turning ReliefWeb on

ReliefWeb's API needs a free, approved "appname" (a two-minute request at
<https://apidoc.reliefweb.int/parameters#appname>). Paste it into
`RELIEFWEB_APPNAME` in `config.py` and the source switches itself on.

### Sites that need a real browser

AfDB sits behind a Cloudflare challenge, IUCN added Drupal's antibot, IFRC
answers 403 to scripts, and the Green Climate Fund's list only exists after
JavaScript runs. `browser_fetch.py` drives the Edge or Chrome already
installed on this machine through Playwright for those four — no browser
download, no API key, no AI in the loop.

**Not covered:** IDB (its project-procurement portal returns an SSL error and
its open-data CSV is a year stale), AIIB (JavaScript-only listing), ICIMOD
(no public tender list), TED (phrase queries return nothing, and it is mostly
EU local works).

## Tuning (all in `config.py`)

- `KEYWORDS`: a notice must contain at least one (case- and accent-insensitive).
- `BROAD_KEYWORDS`: generic terms that still match but score lower.
- `SERVICE_HINTS` / `GOODS_HINTS`: push consultancies and grants up, goods and works down.
- `NOTIFY_MIN_SCORE`, `DISABLED_SOURCES` (e.g. `{"fetch_gcf"}`), `RELIEFWEB_APPNAME`.

The **Relevance** column is a transparent points model, not an AI guess. The
formula is at the top of `matching.py`.

## Guarantees

- `notice_id = source::ref-or-url` is the PRIMARY KEY and rows go in with
  `INSERT OR IGNORE`, so re-checking never duplicates anything and never
  overwrites the original `date_found`.
- `run_log` gets one row per source per check: time, trigger, fetched,
  matched, new, status (`ok` / `partial` / `error`) and the error text.
- The Refresh button and `python monitor.py` both call `monitor.run_cycle()`.
- Notices whose deadline has passed are not stored. The **Triage** column
  (shortlist / applied / not relevant) is saved to the database.

## Files

| File | Role |
|---|---|
| `app.py` | Dashboard (Streamlit) |
| `monitor.py` | `run_cycle()`, the single ingestion path |
| `sources.py` | One `fetch_*` per source + `ALL_SOURCES` |
| `browser_fetch.py` | Headless Edge/Chrome for the protected sites |
| `matching.py` | Keyword gate, relevance score, date parsing |
| `db.py`, `exports.py` | SQLite storage; Excel/CSV export |
| `config.py` | Everything you'd want to change |
| `data/` | `rfp_monitor.db`, `monitor.log` |

Tests: `.venv\Scripts\python -m pytest`
