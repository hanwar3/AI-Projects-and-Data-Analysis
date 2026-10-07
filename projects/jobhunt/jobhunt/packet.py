"""Application packets - everything you need to apply, assembled per job.

What this does and does not do
------------------------------
It does **not** log into LinkedIn and click Easy Apply for you. That breaks
LinkedIn's terms, gets accounts restricted, and - for a PhD-level research or
federal role where a posting draws twenty applicants rather than two thousand -
a blast of generic submissions actively hurts you.

What it does instead is remove the repetitive part. For each job it writes a
folder containing:

    packet.md        the posting, why it scored, and a checklist
    cover_letter.md  a draft with your real CV bullets already slotted in
    answers.md       your stored answers to the usual screening questions
    autofill.json    structured field values for the browser autofill helper
    claude_prompt.md a ready prompt to have Claude sharpen the letter

The letter draft is deliberately a *draft*. It is assembled from your own CV,
so nothing in it is invented, and the Claude prompt is there for the pass that
actually makes it good.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .profile import Profile, resume_bullets
from .match import score_job

APP_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = APP_ROOT / "output"


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:maxlen] or "job"


def relevant_bullets(job: dict[str, Any], profile: Profile, n: int = 5) -> list[str]:
    """Pick the CV bullets that best answer this posting.

    Scored by how many of the posting's distinctive words each bullet shares,
    weighted so that skill and domain terms count more than filler.
    """
    text = profile.resume_text()
    if not text:
        return []

    bullets = resume_bullets(text)
    if not bullets:
        return []

    jd = f"{job.get('title','')} {job.get('description','')}".lower()
    keywords: dict[str, float] = {}
    for skill in profile.all_skills():
        if skill.lower() in jd:
            keywords[skill.lower()] = profile.skill_weight(skill) * 2.0
    for domain in profile.domains:
        if domain.lower() in jd:
            keywords[domain.lower()] = keywords.get(domain.lower(), 0) + 1.5

    scored: list[tuple[float, str]] = []
    for b in bullets:
        low = b.lower()
        s = sum(w for term, w in keywords.items() if term in low)
        # Quantified achievements read better in a letter.
        if re.search(r"\d[\d,.]*\s*(%|\$|k\b|m\b|million|acres|households|interviews|people)", low):
            s += 1.0
        if s > 0:
            scored.append((s, b))

    scored.sort(key=lambda kv: -kv[0])
    out, seen = [], set()
    for _, b in scored:
        key = b[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= n:
            break
    return out


def build_cover_letter(job: dict[str, Any], profile: Profile,
                       bullets: list[str], matched: list[str]) -> str:
    company = job.get("company") or "the organisation"
    title = job.get("title") or "the role"

    skills = [m.split(":", 1)[1] for m in matched if m.startswith("skill:")][:6]
    domains = [m.split(":", 1)[1] for m in matched if m.startswith("domain:")][:4]

    skill_phrase = ", ".join(skills[:4]) if skills else "mixed-methods research"
    domain_phrase = " and ".join(domains[:2]) if domains else "disaster resilience"

    body = [
        f"{date.today():%B %d, %Y}",
        "",
        f"Re: {title} — {company}",
        "",
        "Dear Hiring Committee,",
        "",
        f"I am writing to apply for the {title} position at {company}. I am completing "
        f"a PhD in Urban and Regional Sciences at Texas A&M University, where my "
        f"research sits directly in {domain_phrase}. The combination of "
        f"{skill_phrase} that this role calls for is the core of what I have been "
        f"doing for the last five years.",
        "",
        "A few things from my record that speak to this posting:",
        "",
    ]

    if bullets:
        body += [f"- {b}" for b in bullets]
    else:
        body.append("- [Add 3 achievements relevant to this posting]")

    body += [
        "",
        f"What draws me to {company} specifically is [ONE specific, researched "
        f"sentence — a named programme, publication, or project. Replace this; "
        f"a generic line here is worse than none].",
        "",
        "I would welcome the chance to discuss how this background fits your team's "
        "current work.",
        "",
        "Sincerely,",
        profile.name,
        profile.email,
        profile.phone,
        profile.linkedin,
    ]
    return "\n".join(body)


def build_claude_prompt(job: dict[str, Any], profile: Profile,
                        bullets: list[str], scored: dict) -> str:
    desc = (job.get("description") or "")[:6000]
    return f"""# Task
Rewrite the attached cover letter draft for this specific posting. Keep it to
one page (roughly 300-350 words).

## Rules
- Use ONLY facts present in the CV bullets below. Invent nothing - no numbers,
  employers, or claims that are not already there.
- Replace the bracketed placeholder about the organisation with something
  specific. If you do not have grounds to write it, say so rather than
  inventing praise.
- Mirror the posting's own vocabulary where it is honest to do so - many of
  these employers screen with keyword filters.
- Plain, direct prose. No "I am thrilled", no "passionate about", no filler.

## The posting
Title: {job.get('title')}
Organisation: {job.get('company')}
Location: {job.get('location')}
Match score: {scored.get('score')}/100
Terms that matched: {', '.join(scored.get('matched', [])[:20])}

Description:
{desc}

## My relevant CV bullets (verbatim - the only facts you may use)
{chr(10).join('- ' + b for b in bullets) if bullets else '- [none auto-selected; read the CV]'}

## My details
{profile.name} | {profile.email} | {profile.phone} | {profile.linkedin}
{profile.summary}

## Output
The finished letter, then a short list of anything you could not substantiate
and want me to fill in.
"""


DEFAULT_ANSWERS = {
    "work_authorization": (
        "Are you legally authorized to work in the United States?",
        "[SET THIS: jobhunt answers set work_authorization \"...\"]",
    ),
    "sponsorship": (
        "Will you now or in the future require visa sponsorship?",
        "[SET THIS: jobhunt answers set sponsorship \"...\"]",
    ),
    "relocation": (
        "Are you willing to relocate?",
        "[SET THIS]",
    ),
    "salary": (
        "What are your salary expectations?",
        "[SET THIS - a range, and note it is negotiable based on total package]",
    ),
    "start_date": ("When could you start?", "[SET THIS]"),
    "why_us": (
        "Why do you want to work here?",
        "[Write per application - a stored answer here reads as boilerplate]",
    ),
}


def build_answers(db, job: dict[str, Any]) -> str:
    stored = {row["key"]: row for row in db.answers()}
    lines = ["# Screening answers", "",
             "Stored answers for the questions these forms ask every time.",
             "Update with: `jobhunt answers set <key> \"<answer>\"`", ""]
    keys = sorted(set(stored) | set(DEFAULT_ANSWERS))
    for key in keys:
        if key in stored:
            q = stored[key]["question"] or DEFAULT_ANSWERS.get(key, ("", ""))[0]
            a = stored[key]["answer"]
        else:
            q, a = DEFAULT_ANSWERS[key]
        lines += [f"### {key}", f"**Q:** {q}", "", a, ""]
    return "\n".join(lines)


def build_autofill(job: dict[str, Any], profile: Profile, db) -> dict:
    stored = {row["key"]: row["answer"] for row in db.answers()}
    first, _, last = profile.name.partition(" ")
    return {
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("apply_url") or job.get("url"),
        },
        "fields": {
            "first_name": first,
            "last_name": last.strip(),
            "full_name": profile.name,
            "email": profile.email,
            "phone": profile.phone,
            "linkedin": profile.linkedin,
            "location": profile.location,
            "resume_path": profile.resume_path,
            **{k: v for k, v in stored.items()},
        },
    }


APPLY_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Apply — {title} @ {company}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
        max-width: 760px; margin: 32px auto; padding: 0 18px; }}
 h1 {{ font-size: 19px; margin: 0 0 2px; }}
 .sub {{ color: #777; margin-bottom: 18px; font-size: 13px; }}
 .row {{ display: flex; gap: 10px; align-items: flex-start; padding: 9px 0;
         border-bottom: 1px solid #8883; }}
 .k {{ width: 150px; color: #777; font-size: 13px; flex-shrink: 0; padding-top: 5px; }}
 .v {{ flex: 1; word-break: break-word; white-space: pre-wrap; }}
 button {{ font: inherit; font-size: 12px; padding: 4px 11px; border-radius: 6px;
           border: 1px solid #8886; background: transparent; color: inherit; cursor: pointer; }}
 button:hover {{ border-color: #4a9eff; color: #4a9eff; }}
 button.done {{ border-color: #35c48c; color: #35c48c; }}
 .go {{ display: inline-block; margin: 16px 0; padding: 9px 18px; background: #1f6feb;
        color: #fff; border-radius: 7px; text-decoration: none; font-weight: 600; }}
 .warn {{ border: 1px solid #e2705f; background: #e2705f18; padding: 10px 14px;
          border-radius: 8px; margin: 14px 0; }}
 footer {{ margin-top: 26px; color: #777; font-size: 13px; }}
 code {{ background: #8882; padding: 1px 5px; border-radius: 4px; }}
</style>
<h1>{title}</h1>
<div class="sub">{company} · {location} · match {score}/100</div>
{warning}
<a class="go" href="{apply_url}" target="_blank" rel="noopener">Open the application form</a>
<div id="rows"></div>
<footer>
  Click any value to copy it. Then, once submitted:<br>
  <code>python jobhunt.py status {job_id} applied</code> &nbsp;·&nbsp;
  <code>python jobhunt.py followup {job_id} 14</code>
</footer>
<script>
const FIELDS = {fields_json};
const box = document.getElementById('rows');
for (const [k, v] of Object.entries(FIELDS)) {{
  if (v === null || v === undefined || v === '') continue;
  const row = document.createElement('div');
  row.className = 'row';
  const key = document.createElement('div');
  key.className = 'k'; key.textContent = k.replace(/_/g, ' ');
  const val = document.createElement('div');
  val.className = 'v'; val.textContent = v;
  const btn = document.createElement('button');
  btn.textContent = 'copy';
  btn.onclick = async () => {{
    await navigator.clipboard.writeText(String(v));
    btn.textContent = 'copied'; btn.className = 'done';
    setTimeout(() => {{ btn.textContent = 'copy'; btn.className = ''; }}, 1400);
  }};
  row.append(key, val, btn);
  box.append(row);
}}
</script>
"""


def build_apply_page(job: dict[str, Any], profile: Profile, db, scored: dict) -> str:
    """A local page with one-click copy for every field these forms ask for.

    Deliberately not a form-filling bot. ATS forms differ wildly, they change
    without notice, and a script that types into the wrong field submits a
    wrong application. Copy buttons are unglamorous and they always work.
    """
    autofill = build_autofill(job, profile, db)
    flags = scored["flags"]
    active = [k.replace("needs_", "").replace("_", " ") for k in
              ("needs_citizenship", "needs_clearance", "no_sponsorship", "status_only")
              if flags.get(k)]
    warning = ""
    if active:
        items = "".join(f"<div>{_esc(e)}</div>" for e in flags.get("evidence", []))
        warning = (f'<div class="warn"><b>Check before applying: {", ".join(active)}</b>'
                   f'{items}</div>')

    return APPLY_PAGE.format(
        title=_esc(job.get("title") or ""),
        company=_esc(job.get("company") or ""),
        location=_esc(job.get("location") or "location not stated"),
        score=scored["score"],
        apply_url=_esc(job.get("apply_url") or job.get("url") or "#"),
        job_id=job["id"],
        warning=warning,
        fields_json=json.dumps(autofill["fields"]),
    )


def _esc(text: Any) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def generate(db, job_row, profile: Profile, outdir: Path | None = None) -> Path:
    """Write a full application packet. Returns the folder path."""
    job = dict(job_row)
    scored = score_job(job, profile)
    bullets = relevant_bullets(job, profile)

    outdir = outdir or OUTPUT_DIR
    folder = outdir / f"{job['id']:04d}-{_slug(job.get('company'))}-{_slug(job.get('title'))}"
    folder.mkdir(parents=True, exist_ok=True)

    flags = scored["flags"]
    warnings = [k for k in ("needs_citizenship", "needs_clearance", "no_sponsorship",
                            "status_only", "below_salary_floor") if flags.get(k)]

    packet = [
        f"# {job.get('title')}",
        f"**{job.get('company')}** · {job.get('location') or 'location not stated'} · "
        f"score **{scored['score']}/100**",
        "",
        f"- Source: `{job.get('source')}`",
        f"- Posted: {job.get('posted_at') or 'unknown'}",
        f"- Apply: {job.get('apply_url') or job.get('url')}",
        f"- Salary: {job.get('salary_raw') or 'not stated'}",
        "",
    ]

    if warnings:
        packet += ["> [!WARNING]", f"> **Eligibility flags: {', '.join(warnings)}**", ">"]
        for ev in flags.get("evidence", []):
            packet.append(f"> - {ev}")
        packet.append("")

    packet += [
        "## Why it matched",
        "",
        "| component | points |",
        "|---|---|",
    ]
    for k in ("title", "skills", "domain", "seniority", "location", "bonus"):
        packet.append(f"| {k} | {scored['breakdown'][k]} |")
    packet += [
        "",
        "**Matched terms:** " + ", ".join(scored["matched"][:25]),
        "",
        "## Checklist",
        "",
        "- [ ] Read the full posting (below) - do not apply off the summary",
        "- [ ] Tailor `cover_letter.md` (run `claude_prompt.md` through Claude)",
        "- [ ] Check the CV surfaces this posting's own vocabulary",
        "- [ ] Fill screening questions from `answers.md`",
        "- [ ] Submit, then: `jobhunt status <id> applied`",
        "- [ ] Set a follow-up: `jobhunt followup <id> 14`",
        "",
        "## Full posting",
        "",
        "```",
        (job.get("description") or "(no description captured)")[:20000],
        "```",
    ]

    (folder / "packet.md").write_text("\n".join(packet), encoding="utf-8")
    (folder / "cover_letter.md").write_text(
        build_cover_letter(job, profile, bullets, scored["matched"]), encoding="utf-8")
    (folder / "claude_prompt.md").write_text(
        build_claude_prompt(job, profile, bullets, scored), encoding="utf-8")
    (folder / "answers.md").write_text(build_answers(db, job), encoding="utf-8")
    (folder / "autofill.json").write_text(
        json.dumps(build_autofill(job, profile, db), indent=2), encoding="utf-8")
    (folder / "apply.html").write_text(
        build_apply_page(job, profile, db, scored), encoding="utf-8")

    db.conn.execute("UPDATE jobs SET packet_path=? WHERE id=?", (str(folder), job["id"]))
    db.log_event(job["id"], "packet", detail=str(folder))
    if job.get("status") == "new":
        db.set_status(job["id"], "prepped", detail="packet generated")
    else:
        db.conn.commit()
    return folder
