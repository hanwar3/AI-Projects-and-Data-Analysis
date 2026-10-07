"""jobhunt - command line interface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import match, packet
from .config import APP_ROOT, load_env, status as env_status
from .db import DB, DEFAULT_DB, STATUSES, utcnow
from .pipeline import FREE_SOURCES, run_search
from . import regions as regions_mod
from .profile import Profile
from .sources import ats, discover

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Find, score, and track jobs. Free to run.")
watch_app = typer.Typer(no_args_is_help=True, help="Company boards to poll (free).")
answers_app = typer.Typer(no_args_is_help=True, help="Reusable screening answers.")
app.add_typer(watch_app, name="watch")
app.add_typer(answers_app, name="answers")

con = Console()
DEFAULT_PROFILE = "example"


def _db() -> DB:
    return DB(DEFAULT_DB)


def _profile(name: str) -> Profile:
    try:
        return Profile.load(name)
    except FileNotFoundError:
        con.print(f"[red]No profile '{name}'.[/] Profiles live in {APP_ROOT / 'profiles'}")
        raise typer.Exit(1)


def _score_style(score: float) -> str:
    if score >= 75:
        return "bold green"
    if score >= 55:
        return "green"
    if score >= 40:
        return "yellow"
    return "dim"


def _flag_marks(row) -> str:
    marks = []
    if row["needs_citizenship"]:
        marks.append("[red]CIT[/]")
    if row["needs_clearance"]:
        marks.append("[red]CLR[/]")
    if row["no_sponsorship"]:
        marks.append("[red]SPON[/]")
    return " ".join(marks)


# ------------------------------------------------------------------------ init
@app.command()
def init(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p")):
    """Set up the database and load the profile's watchlist."""
    db = _db()
    p = _profile(profile)
    added = 0
    for entry in p.watchlist:
        if db.add_watch(entry.get("name", ""), entry.get("ats", ""),
                        entry.get("slug", ""), entry.get("tags", "")):
            added += 1
    con.print(f"[green]Database ready:[/] {DEFAULT_DB}")
    con.print(f"[green]Watchlist:[/] {added} companies added, "
              f"{len(db.watchlist())} active")

    env = env_status()
    con.print("\n[bold]Integrations[/]")
    con.print(f"  Apify (LinkedIn/Indeed) : {'[green]configured[/]' if env['apify'] else '[yellow]not set - free tier at console.apify.com[/]'}")
    con.print(f"  USAJOBS (federal)       : {'[green]configured[/]' if env['usajobs'] else '[yellow]not set - free key at developer.usajobs.gov/apirequest[/]'}")

    resume = p.resume_text()
    con.print(f"\n[bold]Resume[/]: {'[green]' + str(len(resume)) + ' chars parsed[/]' if resume else '[yellow]not found - check resume_path[/]'}")
    con.print("\nNext: [cyan]jobhunt search[/]")
    db.close()


# ---------------------------------------------------------------------- search
@app.command()
def search(
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
    region: str = typer.Option("", "--region", "-r",
                               help="Where to search. `jobhunt regions` lists them."),
    only: str = typer.Option("", "--only",
                             help="Comma-separated source names; overrides the region"),
    free_only: bool = typer.Option(False, "--free-only", help="Skip every paid source"),
):
    """Pull new jobs from all enabled sources, dedupe, and score them."""
    db, p = _db(), _profile(profile)
    region_key = region or p.active_region
    try:
        reg = p.region(region_key)
    except KeyError as exc:
        con.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    sources = [s.strip() for s in only.split(",") if s.strip()] or None
    if free_only:
        sources = [s for s in FREE_SOURCES if reg.enabled(s)]

    con.print(Panel.fit(
        f"[bold]{p.name}[/] · {len(p.all_titles())} title queries\n"
        f"[cyan]{reg.label}[/]",
        title="jobhunt search"))
    if reg.notes:
        con.print(f"[dim]{reg.notes}[/]\n")
    report = run_search(db, p, only=sources, region_key=region_key,
                        log=lambda m: con.print(m))

    table = Table(title="Results", header_style="bold")
    for col in ("source", "found", "new", "est. cost"):
        table.add_column(col, justify="right" if col != "source" else "left")
    for r in report.results:
        if r.found or r.new or r.errors:
            table.add_row(r.source, str(r.found), str(r.new),
                          f"${r.cost:.3f}" if r.cost else "free")
    skipped = [r for r in report.results if r.skipped and not r.found]
    if skipped:
        con.print("[dim]not run: "
                  + ", ".join(f"{r.source} ({r.skipped})" for r in skipped) + "[/]")
    con.print(table)
    con.print(f"\n[bold green]{report.total_new} new[/] of {report.total_found} seen · "
              f"scored {report.scored} · spend this run ${report.total_cost:.3f}")

    if report.errors:
        con.print("\n[yellow]Issues:[/]")
        for e in report.errors[:8]:
            con.print(f"  · {e}")

    con.print("\nNext: [cyan]jobhunt top[/]")
    db.close()


# ------------------------------------------------------------------------- top
@app.command()
def top(
    n: int = typer.Option(20, "--n"),
    min_score: float = typer.Option(45.0, "--min"),
    status_filter: str = typer.Option("new", "--status", "-s", help="new|shortlist|applied|active|all"),
    hide_blocked: bool = typer.Option(False, "--hide-blocked", help="Drop citizenship/clearance/sponsorship-blocked roles"),
    search_text: str = typer.Option("", "--find"),
):
    """The highest-scoring jobs you have not dealt with yet."""
    db = _db()
    rows = db.query_jobs(status=status_filter, min_score=min_score, limit=n,
                         search=search_text or None, hide_blocked=hide_blocked)
    if not rows:
        con.print("[yellow]Nothing matches.[/] Try --min 0, --status all, or run a search.")
        db.close()
        return

    table = Table(header_style="bold", show_lines=False, box=None, pad_edge=False)
    table.add_column("id", justify="right", style="dim", no_wrap=True)
    table.add_column("pts", justify="right", no_wrap=True)
    table.add_column("title", max_width=46, no_wrap=True, overflow="ellipsis")
    table.add_column("company", max_width=22, no_wrap=True, overflow="ellipsis")
    table.add_column("location", max_width=18, no_wrap=True, overflow="ellipsis")
    table.add_column("src", style="dim", no_wrap=True)
    table.add_column("!", no_wrap=True)

    for r in rows:
        table.add_row(
            str(r["id"]),
            f"[{_score_style(r['score'])}]{r['score']:.0f}[/]",
            r["title"] or "",
            r["company"] or "",
            r["location"] or "",
            r["source"],
            _flag_marks(r),
        )
    con.print(table)
    con.print("\n[dim]CIT=US citizenship · CLR=clearance · SPON=no sponsorship[/]")
    con.print("[dim]jobhunt show <id> · jobhunt prep <id> · jobhunt status <id> shortlist[/]")
    db.close()


@app.command()
def show(job_id: int, profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
         region: str = typer.Option("", "--region", "-r"),
         full: bool = typer.Option(False, "--full", help="Print the whole description")):
    """Full detail and score breakdown for one job."""
    db, p = _db(), _profile(profile)
    row = db.get_job(job_id)
    if not row:
        con.print(f"[red]No job {job_id}[/]")
        raise typer.Exit(1)

    con.print(Panel(match.explain(row, p, region or None),
                    title=f"job {job_id}", border_style="cyan"))
    con.print(f"[bold]Status:[/] {row['status']}   [bold]Apply:[/] {row['apply_url'] or row['url']}")
    if row["packet_path"]:
        con.print(f"[bold]Packet:[/] {row['packet_path']}")

    events = db.events_for(job_id)
    if events:
        con.print("\n[bold]History[/]")
        for e in events[:10]:
            con.print(f"  {e['ts'][:16]}  {e['kind']:14} {e['old_value'] or ''} -> {e['new_value'] or ''} {e['detail'] or ''}")

    desc = row["description"] or ""
    if full:
        con.print("\n[bold]Description[/]\n" + desc)
    elif desc:
        con.print(f"\n[dim]{desc[:700]}...[/]\n[dim](--full for all {len(desc)} chars)[/]")
    db.close()


@app.command()
def open_job(job_id: int):
    """Open the job's apply page in your browser."""
    db = _db()
    row = db.get_job(job_id)
    if not row:
        con.print(f"[red]No job {job_id}[/]")
        raise typer.Exit(1)
    url = row["apply_url"] or row["url"]
    if not url:
        con.print("[red]No URL stored for this job[/]")
        raise typer.Exit(1)
    webbrowser.open(url)
    con.print(f"[green]Opened[/] {url}")
    db.close()


# ----------------------------------------------------------------- pipeline ops
@app.command()
def status(job_id: int, new_status: str, note: str = typer.Option("", "--note")):
    """Move a job along the pipeline: shortlist, applied, interview, rejected..."""
    db = _db()
    if new_status not in STATUSES:
        con.print(f"[red]Unknown status.[/] Valid: {', '.join(STATUSES)}")
        raise typer.Exit(1)
    row = db.get_job(job_id)
    if not row:
        con.print(f"[red]No job {job_id}[/]")
        raise typer.Exit(1)
    db.set_status(job_id, new_status, detail=note or None)
    con.print(f"[green]{row['title']}[/] · {row['status']} -> [bold]{new_status}[/]")
    db.close()


@app.command()
def note(job_id: int, text: str):
    """Attach a note to a job."""
    db = _db()
    db.log_event(job_id, "note", detail=text)
    existing = db.get_job(job_id)["notes"] or ""
    db.conn.execute("UPDATE jobs SET notes=? WHERE id=?",
                    (f"{existing}\n[{utcnow()[:10]}] {text}".strip(), job_id))
    db.conn.commit()
    con.print("[green]Noted.[/]")
    db.close()


@app.command()
def followup(
    job_id: int,
    days: int = typer.Argument(14, help="Days from now"),
    action: str = typer.Option("Follow up", "--action"),
):
    """Schedule a follow-up reminder."""
    db = _db()
    if not db.get_job(job_id):
        con.print(f"[red]No job {job_id}[/]")
        db.close()
        raise typer.Exit(1)
    when = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    db.conn.execute("UPDATE jobs SET next_action=?, next_action_date=? WHERE id=?",
                    (action, when, job_id))
    db.conn.commit()
    db.log_event(job_id, "reminder", new_value=when, detail=action)
    con.print(f"[green]Reminder set[/] for {when}: {action}")
    db.close()


@app.command()
def prep(job_id: int, profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
         open_folder: bool = typer.Option(False, "--open")):
    """Generate the application packet for a job."""
    db, p = _db(), _profile(profile)
    row = db.get_job(job_id)
    if not row:
        con.print(f"[red]No job {job_id}[/]")
        raise typer.Exit(1)

    folder = packet.generate(db, row, p)
    con.print(Panel.fit(
        f"[green]Packet written[/]\n{folder}\n\n"
        "  apply.html        open first - copy button for every form field\n"
        "  packet.md         posting + why it matched + checklist\n"
        "  cover_letter.md   draft built from your real CV bullets\n"
        "  claude_prompt.md  give to Claude to sharpen the letter\n"
        "  answers.md        stored screening answers\n"
        "  autofill.json     raw field values",
        title=row["title"][:60]))

    if open_folder:
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
    db.close()


@app.command()
def board(days_stale: int = typer.Option(21, "--stale")):
    """Your pipeline at a glance, plus anything that has gone quiet."""
    db = _db()
    counts = db.counts_by_status()

    table = Table(title="Pipeline", header_style="bold")
    table.add_column("status")
    table.add_column("n", justify="right")
    for s in STATUSES:
        if counts.get(s):
            table.add_row(s, str(counts[s]))
    con.print(table)

    total = sum(counts.values())
    applied = sum(counts.get(s, 0) for s in ("applied", "screen", "interview", "final", "offer", "rejected"))
    responded = sum(counts.get(s, 0) for s in ("screen", "interview", "final", "offer"))
    con.print(f"\n{total} tracked · {applied} applied · {responded} advanced past submission")
    if applied:
        con.print(f"Response rate: [bold]{responded / applied * 100:.0f}%[/]")

    stale = db.stale_applications(days_stale)
    if stale:
        con.print(f"\n[yellow]Quiet for {days_stale}+ days ({len(stale)}):[/]")
        for r in stale[:12]:
            con.print(f"  [dim]{r['id']:>4}[/] {r['status']:<10} {(r['title'] or '')[:44]:<44} "
                      f"{(r['company'] or '')[:24]} [dim]since {r['status_changed_at'][:10]}[/]")

    due = db.conn.execute(
        "SELECT * FROM jobs WHERE next_action_date IS NOT NULL AND next_action_date <= date('now') "
        "AND status NOT IN ('rejected','withdrawn','skipped','ghosted') ORDER BY next_action_date"
    ).fetchall()
    if due:
        con.print(f"\n[cyan]Follow-ups due ({len(due)}):[/]")
        for r in due[:12]:
            con.print(f"  [dim]{r['id']:>4}[/] {r['next_action_date']}  {r['next_action']}  "
                      f"{(r['title'] or '')[:40]} @ {(r['company'] or '')[:22]}")

    spent = db.spend_this_month()
    con.print(f"\n[dim]Apify spend this month: ${spent:.2f}[/]")
    db.close()


@app.command()
def rescore(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
            region: str = typer.Option("", "--region", "-r",
                                       help="Rescore for a different region")):
    """Rescore every stored job. Run after editing the profile or switching region."""
    db, p = _db(), _profile(profile)
    region_key = region or p.active_region
    try:
        reg = p.region(region_key)
    except KeyError as exc:
        con.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    n = match.rescore_all(db, p, only_new=False, region_key=region_key)
    con.print(f"[green]Rescored {n} jobs[/] for [cyan]{reg.label}[/]")
    con.print("[dim]Location scores now reflect this region. Run `jobhunt top` to see.[/]")
    db.close()


@app.command()
def ghost(days: int = typer.Option(45, "--days")):
    """Mark long-silent applications as ghosted, so the board stays honest."""
    db = _db()
    stale = db.stale_applications(days)
    only_applied = [r for r in stale if r["status"] == "applied"]
    for r in only_applied:
        db.set_status(r["id"], "ghosted", detail=f"no response in {days} days")
    con.print(f"[green]Marked {len(only_applied)} as ghosted[/] (silent {days}+ days)")
    db.close()


@app.command()
def export(path: str = typer.Option("", "--out"), status_filter: str = typer.Option("all", "--status")):
    """Export tracked jobs to CSV."""
    import csv

    db = _db()
    rows = db.query_jobs(status=status_filter, min_score=0, limit=100000)
    out = Path(path) if path else APP_ROOT / f"export-{datetime.now():%Y%m%d}.csv"
    fields = ["id", "score", "status", "title", "company", "location", "source",
              "salary_raw", "posted_at", "applied_at", "url", "apply_url",
              "needs_citizenship", "needs_clearance", "no_sponsorship", "notes"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})
    con.print(f"[green]Wrote {len(rows)} rows[/] -> {out}")
    db.close()


@app.command()
def serve(port: int = typer.Option(8765, "--port"), no_browser: bool = typer.Option(False, "--no-browser")):
    """Launch the local dashboard."""
    import uvicorn

    from .web.app import create_app

    url = f"http://127.0.0.1:{port}"
    con.print(f"[green]Dashboard:[/] {url}   [dim](ctrl-c to stop)[/]")
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


# -------------------------------------------------------------------- watchlist
@watch_app.command("list")
def watch_list():
    """Company boards being polled."""
    db = _db()
    rows = db.watchlist(active_only=False)
    if not rows:
        con.print("[yellow]Watchlist empty.[/] Add one: jobhunt watch add-url <careers-url>")
        db.close()
        return
    table = Table(header_style="bold", box=None, pad_edge=False)
    for c, width in (("id", 4), ("company", 26), ("ats", 11), ("slug", 18),
                     ("on", 2), ("last ok", 10), ("last error", 22)):
        table.add_column(c, max_width=width, no_wrap=True, overflow="ellipsis")
    for r in rows:
        table.add_row(str(r["id"]), r["name"], r["ats"], r["slug"],
                      "y" if r["active"] else "n",
                      (r["last_ok"] or "")[:10], r["last_err"] or "")
    con.print(table)
    db.close()


@watch_app.command("add-url")
def watch_add_url(url: str, name: str = typer.Option("", "--name")):
    """Add a company from its careers URL. The reliable way to do this."""
    db = _db()
    parsed = discover.from_url(url)
    if not parsed:
        con.print("[red]Could not recognise that URL.[/]\nSupported: greenhouse, lever, "
                  "ashby, workable, smartrecruiters, recruitee.")
        raise typer.Exit(1)
    ats_name, slug = parsed
    if ats_name == "workday":
        con.print("[yellow]That is a Workday board.[/] Workday exposes no stable public "
                  "API, so it cannot be polled directly - LinkedIn will cover this "
                  "employer instead.")
        raise typer.Exit(1)

    declared = ats.board_identity(ats_name, slug)
    company = name or declared or slug
    ok, detail, n = ats.probe(ats_name, slug)
    if not ok:
        con.print(f"[red]Board did not respond:[/] {detail}")
        raise typer.Exit(1)

    if db.add_watch(company, ats_name, slug, ""):
        con.print(f"[green]Added[/] {company} ({ats_name}/{slug}) — {detail}")
    else:
        con.print(f"[yellow]Already watching[/] {ats_name}/{slug}")
    db.close()


@watch_app.command("add")
def watch_add(name: str, ats_name: str, slug: str, tags: str = typer.Option("", "--tags")):
    """Add a company by explicit ATS and slug."""
    db = _db()
    ok, detail, _ = ats.probe(ats_name, slug)
    if not ok:
        con.print(f"[yellow]Warning: board did not verify ({detail}). Adding anyway.[/]")
    if db.add_watch(name, ats_name, slug, tags):
        con.print(f"[green]Added[/] {name} ({ats_name}/{slug}) — {detail}")
    else:
        con.print(f"[yellow]Already watching[/] {ats_name}/{slug}")
    db.close()


@watch_app.command("discover")
def watch_discover(
    names: list[str] = typer.Argument(None, help="Company names; omit to try the built-in list"),
    add: bool = typer.Option(False, "--add", help="Add every verified hit to the watchlist"),
):
    """Best-effort: guess a company's ATS and slug from its name.

    Guessing is genuinely unreliable — most large employers use Workday or
    iCIMS, which have no public API, and short slugs collide with unrelated
    boards. Every hit here is identity-checked against the board's own declared
    name, but `watch add-url` remains the dependable route.
    """
    db = _db()
    targets = list(names) if names else discover.SUGGESTED_EMPLOYERS
    con.print(f"[dim]Probing {len(targets)} employers...[/]")

    hits = 0
    for company in targets:
        found = discover.discover(company)
        if not found:
            con.print(f"  [dim]{company}: no public board found[/]")
            continue
        ats_name, slug, detail = found
        hits += 1
        con.print(f"  [green]{company}[/]: {ats_name}/{slug} — {detail}")
        if add and db.add_watch(company, ats_name, slug, ""):
            con.print(f"    [dim]added[/]")

    con.print(f"\n[bold]{hits}/{len(targets)}[/] have a pollable public board.")
    if not add and hits:
        con.print("[dim]Re-run with --add to save them.[/]")
    db.close()


@watch_app.command("remove")
def watch_remove(watch_id: int):
    """Stop polling a company."""
    db = _db()
    db.conn.execute("DELETE FROM watchlist WHERE id=?", (watch_id,))
    db.conn.commit()
    con.print("[green]Removed.[/]")
    db.close()


# ----------------------------------------------------------------- answer bank
@answers_app.command("list")
def answers_list():
    """Stored screening answers."""
    db = _db()
    rows = db.answers()
    if not rows:
        con.print("[yellow]No answers stored.[/] jobhunt answers set work_authorization \"...\"")
        db.close()
        return
    for r in rows:
        con.print(f"[bold cyan]{r['key']}[/]")
        if r["question"]:
            con.print(f"  [dim]{r['question']}[/]")
        con.print(f"  {r['answer']}\n")
    db.close()


@answers_app.command("set")
def answers_set(key: str, answer: str, question: str = typer.Option("", "--question", "-q")):
    """Store an answer you would otherwise retype on every form."""
    db = _db()
    default_q = packet.DEFAULT_ANSWERS.get(key, ("", ""))[0]
    db.set_answer(key, question or default_q, answer)
    con.print(f"[green]Saved[/] {key}")
    db.close()


@app.command()
def regions(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p")):
    """List the regions you can search, and which sources each one uses."""
    p = _profile(profile)
    table = Table(header_style="bold", box=None, pad_edge=False)
    table.add_column("", no_wrap=True, width=1)
    table.add_column("key", no_wrap=True)
    table.add_column("region", max_width=42, no_wrap=True, overflow="ellipsis")
    table.add_column("cc", no_wrap=True, justify="center")
    table.add_column("srcs", no_wrap=True, justify="right")

    for reg in regions_mod.available(p.region_overrides):
        active = reg.key == p.active_region
        table.add_row(
            "[bold green]*[/]" if active else "",
            f"[bold green]{reg.key}[/]" if active else reg.key,
            reg.label,
            (reg.country or "-").upper(),
            str(len(reg.sources)),
        )
    con.print(table)
    con.print(f"\n[dim]* current, from `active_region` in {p.path.name}[/]")  # type: ignore[attr-defined]
    con.print("[dim]Try one:  jobhunt search --region uk[/]")
    con.print("[dim]Make it stick: set `active_region:` in the profile, then `jobhunt rescore`[/]")


@app.command("_active-region", hidden=True)
def _active_region(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p")):
    """Print the active region's label. Used by the launcher menu."""
    try:
        p = Profile.load(profile)
        print(p.region().label)
    except Exception:
        raise typer.Exit(1)


@app.command()
def doctor():
    """Check configuration and connectivity."""
    load_env()
    con.print(Panel.fit("jobhunt doctor", border_style="cyan"))

    con.print(f"[bold]Database[/]  {DEFAULT_DB} {'[green]exists[/]' if DEFAULT_DB.exists() else '[yellow]not created yet[/]'}")

    env = env_status()
    con.print(f"[bold]APIFY_TOKEN[/]   {'[green]set[/]' if env['apify'] else '[yellow]missing (LinkedIn/Indeed disabled)[/]'}")
    con.print(f"[bold]USAJOBS_KEY[/]   {'[green]set[/]' if env['usajobs'] else '[yellow]missing (federal jobs disabled)[/]'}")

    for name in sorted(Path(APP_ROOT / "profiles").glob("*.yaml")):
        try:
            p = Profile.load(name)
            resume = p.resume_text()
            con.print(f"[bold]Profile[/] {name.stem}: {len(p.all_titles())} queries, "
                      f"{len(p.all_skills())} skills, resume "
                      f"{'[green]' + str(len(resume)) + ' chars[/]' if resume else '[yellow]NOT FOUND[/]'}")
        except Exception as exc:
            con.print(f"[red]Profile {name.stem} failed to load: {exc}[/]")

    con.print("\n[bold]Free source reachability[/]")
    for label, fn in (("greenhouse", lambda: ats.probe("greenhouse", "gitlab")),
                      ("lever", lambda: ats.probe("lever", "palantir")),
                      ("ashby", lambda: ats.probe("ashby", "ramp"))):
        try:
            ok, detail, _ = fn()
            con.print(f"  {label:14} {'[green]ok[/]' if ok else '[red]' + detail + '[/]'}  [dim]{detail}[/]")
        except Exception as exc:
            con.print(f"  {label:14} [red]{exc}[/]")

    db = _db()
    con.print(f"\n[bold]Watchlist[/] {len(db.watchlist())} active")
    con.print(f"[bold]Jobs[/] {sum(db.counts_by_status().values())} stored")
    db.close()


def main() -> None:
    # Windows consoles still default to cp1252, which mangles the box-drawing
    # and bullet characters rich emits.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    load_env()
    app()


if __name__ == "__main__":
    main()
