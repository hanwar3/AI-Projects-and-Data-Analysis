# jobhunt

A job search that runs on your machine, costs nothing, and remembers everything.

Built for a specific problem: you are finishing a PhD in hazards and urban
planning, you are in the DC metro, and the roles you want are scattered across
federal postings, consulting firms, think tanks, NGOs and universities. No
single job board covers that, and mass-applying to 500 listings is the wrong
strategy when the roles you want draw twenty applicants, not two thousand.

So this optimises for **precision and preparation**, not volume.

```
  sources  ─────────►  dedupe  ─────►  score  ─────►  SQLite  ─────►  dashboard
  ATS boards  (free)                   0-100          one file        + CLI
  USAJOBS     (free)                   + eligibility
  ReliefWeb   (free)                     flags
  Adzuna      (free)
  Remotive · Jobicy · Arbeitnow (free)      region decides which
  LinkedIn    (~$0.40 / 1000)               sources run at all
  Indeed      (~$5 / 1000)
```

---

## Quick start

**Double-click `Job Search App.bat`.** That is the whole setup. It finds
Python, creates the database on first run, and gives you a menu:

```
     1.  Open dashboard                 (browse, rank, track)
     2.  Search for new jobs            (free sources only)
     3.  Search for new jobs            (everything, may cost credit)
     4.  Search a different country/region
     5.  Show my pipeline               (applied, stale, follow-ups)
     6.  Show top matches in terminal
     7.  Settings check                 (which API keys are set up)
     8.  Edit my profile                (titles, skills, region)
     9.  Add a company to watch         (paste a careers URL)
```

`Open Dashboard.bat` skips the menu and goes straight to the dashboard.
On macOS or Linux, use `./job-search-app.sh`.

Prefer the terminal?

```bash
cd jobhunt
python jobhunt.py init
python jobhunt.py search --free-only
python jobhunt.py serve
```

Everything runs locally. Nothing is uploaded, there is no account, and the
whole state of your search is one SQLite file at `data/jobs.db`.

> **If Windows says Python is not found:** install it from
> [python.org/downloads](https://www.python.org/downloads/) and tick
> *"Add python.exe to PATH"* on the first screen of the installer.

---

## Searching other countries

The whole geography of a search is one setting. `jobhunt regions` lists what
ships with the app:

| key | covers |
|---|---|
| `us_dc` | DC metro — Washington, Virginia, Maryland *(default)* |
| `us` | United States, nationwide |
| `us_texas` | Texas |
| `canada` | Canada |
| `uk` | United Kingdom |
| `europe` | EU, Switzerland, Norway |
| `australia_nz` | Australia and New Zealand |
| `gulf` | UAE, Saudi Arabia, Qatar |
| `south_asia` | Pakistan, India, Bangladesh, Nepal |
| `africa` | Kenya, South Africa, Nigeria, Ethiopia, Senegal |
| `global_remote` | Remote, anywhere |
| `humanitarian_global` | UN / INGO / donor roles worldwide — a field, not a place |

Try one without committing to it:

```bash
python jobhunt.py search --region uk
python jobhunt.py rescore --region uk
```

`rescore` matters: location is 10 points of the score, so re-ranking makes
London score the way Washington does under `us_dc`. To make a region stick,
set `active_region:` in `profiles/example.yaml` and run `jobhunt rescore`.

A region carries more than a place name — it decides the ISO country code sent
to Indeed and Adzuna, the location string LinkedIn matches on, the city words
that count as "right here" when scoring, and **which sources are worth running
at all**. USAJOBS is not called outside the US. Adzuna is skipped in the Gulf,
which it does not serve. Indeed is skipped for `global_remote`, since it needs
a country anchor. Nothing is called just to have its results thrown away.

You can override any region, or add your own, under `regions:` in the profile —
there is a worked `geneva` example commented out in there.

### Which regions are actually well served

Being straight about this, because it affects where you spend your time:

- **United States** is the strongest. USAJOBS alone is comprehensive and free.
- **Anywhere else, ReliefWeb is the source that matters** for disaster,
  recovery and resilience work — UN agencies, INGOs and donors, worldwide.
  It needs a free registered appname. If you search outside the US at all,
  set this one up first.
- **LinkedIn via Apify works in every country** and is the general-purpose
  fallback. Roughly $0.40 per 1,000 results against a $5/month free credit.
- **Adzuna** covers 20 countries well for mainstream roles.
- **Arbeitnow** (Europe) and **Jobicy** (remote) are free and keyless but thin
  for research and policy work — Jobicy in particular skews to remote tech, so
  its queries are deliberately kept narrow in the profile.
- **The Muse** ships **disabled**: it indexes big-brand corporate employers and
  produced mostly sales and finance roles in testing. Turn it on if you
  retarget toward the private sector.

---

## What it actually costs

| Source | Cost | Coverage |
|---|---|---|
| Greenhouse / Lever / Ashby / Workable / Recruitee boards | **$0**, unlimited | Worldwide, straight from the employer's ATS |
| USAJOBS | **$0**, free key | US federal — every opening |
| ReliefWeb | **$0**, free appname | Worldwide humanitarian / DRR / recovery |
| Adzuna | **$0**, free key | 20 countries |
| Remotive · Jobicy · Arbeitnow · The Muse | **$0**, no key at all | Remote, Europe, corporate |
| LinkedIn via Apify | ~$0.40 per 1,000 jobs | Any country. Free plan gives $5/month ≈ 12,000 jobs |
| Indeed via Apify | ~$5 per 1,000 jobs | 60+ countries. Off by default — 10× LinkedIn's price |
| Scoring, tracking, dashboard, packets | **$0** | All local, no LLM API calls |

The profile sets `budget_usd_per_month: 4.0`. A run that would cross that cap
stops before spending, and every run's estimated cost is recorded in the `runs`
table. `jobhunt board` shows the month's spend.

**Realistically you will never pay anything.** The free tier covers far more
searching than a focused hunt needs.

---

## Setup for the free API keys

All optional, all genuinely free, each a couple of minutes. Copy
`.env.example` to `.env` and fill in whichever you want — or use menu option 7
in the launcher, which opens the file for you.

| Key | Get it from | Unlocks |
|---|---|---|
| `USAJOBS_EMAIL` + `USAJOBS_KEY` | [developer.usajobs.gov/apirequest](https://developer.usajobs.gov/apirequest) | Every US federal opening |
| `RELIEFWEB_APPNAME` | [apidoc.reliefweb.int](https://apidoc.reliefweb.int/parameters#appname) | UN / INGO humanitarian roles worldwide |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | [developer.adzuna.com/signup](https://developer.adzuna.com/signup) | 20 countries of general listings |
| `APIFY_TOKEN` | [console.apify.com](https://console.apify.com) → Settings → API | LinkedIn and Indeed, any country |

Then check what took:

```bash
python jobhunt.py doctor
```

**Which to do first depends on where you are looking.** Searching the US:
USAJOBS, without question — from the DC area, FEMA, DHS, NOAA and HUD are
all inside commuting distance. Searching anywhere else, or the humanitarian
sector anywhere: ReliefWeb.

---

## Daily use

```bash
python jobhunt.py search           # pull new postings
python jobhunt.py top              # highest-scoring, unreviewed
python jobhunt.py show 42          # full breakdown of why it scored
python jobhunt.py prep 42          # build the application packet
python jobhunt.py status 42 applied
python jobhunt.py followup 42 14   # remind me in 14 days
python jobhunt.py board            # pipeline, response rate, what has gone quiet
```

Or just run `python jobhunt.py serve` and work from the dashboard, which does
all of the above with clicking.

---

## How scoring works

Not an LLM call. Every job would cost a round-trip, results would drift between
runs, and you could not see why anything ranked where it did. Instead it is a
transparent weighted model, and `jobhunt show <id>` prints the arithmetic.

| Component | Max | What it measures |
|---|---|---|
| title | 30 | Does the title fall in one of your clusters |
| skills | 25 | Weighted share of your skills the posting asks for |
| domain | 20 | Subject-matter overlap — disaster, resilience, floodplain… |
| seniority | 10 | Pitched at your level |
| location | 10 | Matched against the active region's cities and countries |
| bonus | 5 | PhD-shaped signals: publications, IRB, mixed methods |

Expert skills count more than ones you merely list. Skill credit saturates, so
a posting naming eight of your skills scores well but one naming twenty is not
three times better — it is just a long job description. A posting whose title
matches nothing you do gets damped hard, so keyword soup in a long description
cannot fake a match.

### Eligibility flags

Three things get detected and **flagged, never silently subtracted**:

- `CIT` — requires US citizenship
- `CLR` — requires an existing security clearance
- `SPON` — states it will not sponsor a visa

These are not "slightly worse jobs", they are a different question, and it is
yours to answer. Use `--hide-blocked` to drop them from the list once you have
decided.

This matters more than it sounds. In testing, the sponsorship detector caught
GiveDirectly's *"we are unable to sponsor or take over sponsorship of employment
visas"* buried deep in a posting — a job that would otherwise have cost an hour.

---

## Company watchlist

Watching an employer's own ATS beats any aggregator: you see the posting the
hour it goes up, with the real apply link.

**Adding one takes 30 seconds and always works:**

```bash
python jobhunt.py watch add-url "https://boards.greenhouse.io/theirslug"
python jobhunt.py watch list
```

Open the employer's careers page, copy the URL, paste it. The ATS and slug are
parsed out of the URL, and the board's declared company name is checked before
it is saved.

There is also `watch discover "Company Name"`, which guesses. Be aware it is
unreliable, and honestly so: slug guessing produces convincing collisions —
`greenhouse/cc` is Climate Corps, not Climate Central; `greenhouse/sc` is Sands
Capital, not Save the Children. Every hit is verified against the board's own
name before being offered, which is why it rejects far more than it accepts.

**A real limitation, stated plainly:** only Greenhouse, Lever, Ashby, Workable
and Recruitee publish open board APIs. Most large hazards and federal-contractor
employers — ICF, Guidehouse, Booz Allen, Tetra Tech US, AECOM, Dewberry, RAND —
run on Workday, iCIMS or Taleo, which do not. Those employers are covered
through LinkedIn and USAJOBS instead. Nothing is lost; it arrives by a different
route.

---

## Application packets

`python jobhunt.py prep 42` writes a folder into `output/`:

| File | What it is |
|---|---|
| `apply.html` | **Open this first.** Every field these forms ask for, each with a copy button |
| `packet.md` | The posting, the score breakdown, and a checklist |
| `cover_letter.md` | A draft with real bullets from your CV already slotted in |
| `claude_prompt.md` | Hand to Claude to sharpen the letter |
| `answers.md` | Your stored answers to the usual screening questions |
| `autofill.json` | The same field values as raw JSON |

The letter is deliberately a *draft*. It is assembled only from your own CV, so
nothing in it is invented, and it leaves an explicit placeholder where a
specific sentence about the employer belongs — because a generic line there is
worse than none. The Claude prompt is the pass that makes it good, and it runs
on the Claude Code subscription you already have rather than a metered API key.

Stop retyping the same answers:

```bash
python jobhunt.py answers set work_authorization "..." 
python jobhunt.py answers set salary "..."
python jobhunt.py answers list
```

---

## On automatic applying

The app does not log into LinkedIn and click Easy Apply for you. That is a
deliberate decision, not a missing feature:

1. It violates LinkedIn's terms and gets accounts restricted. Losing your
   LinkedIn account mid-search is a serious, self-inflicted setback.
2. ATS forms differ wildly and change without notice. A script that types into
   the wrong field submits a wrong application under your name.
3. For your roles it does not work anyway. Federal applications need
   position-specific narrative responses; academic ones need a research
   statement; consulting ones get screened by a human who notices a generic
   letter.

What is automated is everything that is genuinely repetitive: finding the
postings, filtering the ones you cannot apply to, ranking what is left,
assembling the packet, and remembering the state of every application. The
final click stays yours, which is also where your judgement is worth the most.

---

## Retargeting the app

Everything lives in `profiles/example.yaml`. Edit it and run
`python jobhunt.py rescore` — no code changes.

- `titles:` — clusters of search queries. Add or drop a cluster to change what
  is searched.
- `skills:` — three tiers, `expert` / `proficient` / `familiar`, weighted 1.0 /
  0.7 / 0.4.
- `domains:` — subject-matter words that indicate a posting is in your world.
- `exclude_titles:` — hard-zero anything matching. Remove `intern` from this
  list if you want internships.
- `active_region:` — which country/region to search. `jobhunt regions` lists them.
- `locations:` — ordered; earlier entries score higher. Applies to the active region.
- `min_salary:` — set a number to flag anything advertised below it.

To search for something else entirely, copy the file and use
`--profile <name>`.

---

## Project layout

```
Job_Search_App/
├── Job Search App.bat      double-click launcher (menu)
├── Open Dashboard.bat      double-click launcher (straight to dashboard)
├── job-search-app.sh       same, for macOS/Linux
├── jobhunt.py              run without installing
├── profiles/example.yaml    everything configurable
├── data/jobs.db            all state, one SQLite file
├── output/                 generated application packets
└── jobhunt/
    ├── db.py               schema, dedupe, event log
    ├── profile.py          YAML profile + resume parsing
    ├── match.py            the scoring model
    ├── pipeline.py         runs sources, enforces budget
    ├── packet.py           application packet generation
    ├── cli.py              commands
    ├── regions.py          country/region presets
    ├── sources/
    │   ├── ats.py          Greenhouse/Lever/Ashby/Workable/Recruitee
    │   ├── discover.py     find an employer's board from a URL or name
    │   ├── usajobs.py      US federal
    │   ├── reliefweb.py    UN/INGO humanitarian, worldwide
    │   ├── intl.py         Adzuna, Arbeitnow, Jobicy, The Muse
    │   ├── remotive.py     remote
    │   └── apify.py        LinkedIn + Indeed, with cost estimation
    └── web/                FastAPI + a single-file dashboard
```

Deduplication is by normalised title + company + city, so the same role
arriving from LinkedIn, Indeed and the company's own Greenhouse board collapses
into one row rather than three. Your pipeline state and notes are never
overwritten by a re-scrape.

---

## Command reference

| Command | Does |
|---|---|
| `init` | Create the DB, load the watchlist |
| `regions` | List the regions you can search |
| `doctor` | Check keys, profile, resume parsing, source reachability |
| `search` | Pull from all enabled sources (`--region uk`, `--free-only`, `--only linkedin`) |
| `top` | Best unreviewed matches (`--min`, `--status`, `--hide-blocked`, `--find`) |
| `show <id>` | Full score breakdown and history (`--full`) |
| `open-job <id>` | Open the posting in a browser |
| `prep <id>` | Generate the application packet (`--open`) |
| `status <id> <state>` | Move through the pipeline |
| `note <id> "..."` | Attach a note |
| `followup <id> <days>` | Set a reminder |
| `board` | Pipeline, response rate, stale applications, spend |
| `ghost --days 45` | Mark long-silent applications as ghosted |
| `rescore` | Re-run scoring after editing the profile (`--region uk`) |
| `export` | Write everything to CSV |
| `serve` | Local dashboard |
| `watch add-url <url>` | Add an employer from a careers URL |
| `watch list` / `remove` | Manage the watchlist |
| `answers set/list` | The reusable answer bank |

Pipeline states: `new → shortlist → prepped → applied → screen → interview →
final → offer`, plus `rejected`, `ghosted`, `withdrawn`, `skipped`.

---

## Requirements

Python 3.10+. Everything needed was already present on this machine:
`requests`, `PyYAML`, `typer`, `rich`, `fastapi`, `uvicorn`, `pypdf`.

If you move it to another machine: `pip install -e .`
