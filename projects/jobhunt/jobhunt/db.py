"""SQLite storage for jobs, applications, and the event timeline.

Design notes
------------
* One file (``data/jobs.db``). No server, no cloud, no cost.
* ``jobs`` holds every posting we have ever seen, deduped by ``fingerprint``.
* ``events`` is an append-only log so status history is never lost.
* Everything is plain SQL - no ORM - so the DB stays readable with any
  SQLite browser and the app stays dependency-light.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = APP_ROOT / "data" / "jobs.db"

# Pipeline states, in order. Progress is measured by index in this list.
STATUSES = [
    "new",          # freshly scraped, not reviewed
    "shortlist",    # you marked it worth applying to
    "prepped",      # application packet generated
    "applied",      # submitted
    "screen",       # recruiter screen / HR contact
    "interview",    # interview stage
    "final",        # final round / reference check
    "offer",
    "rejected",
    "ghosted",      # no response past the staleness threshold
    "withdrawn",
    "skipped",      # you decided not to apply
]

TERMINAL_STATUSES = {"rejected", "ghosted", "withdrawn", "skipped", "offer"}
ACTIVE_STATUSES = {"applied", "screen", "interview", "final"}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint       TEXT UNIQUE NOT NULL,
    source            TEXT NOT NULL,
    source_id         TEXT,
    title             TEXT NOT NULL,
    company           TEXT,
    location          TEXT,
    remote            TEXT,
    description       TEXT,
    url               TEXT,
    apply_url         TEXT,
    salary_min        REAL,
    salary_max        REAL,
    salary_raw        TEXT,
    employment_type   TEXT,
    seniority         TEXT,
    posted_at         TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    raw               TEXT,

    -- scoring (written by match.py)
    score             REAL DEFAULT 0,
    score_breakdown   TEXT,
    matched_terms     TEXT,
    missing_terms     TEXT,

    -- eligibility flags (written by match.py)
    needs_citizenship INTEGER DEFAULT 0,
    needs_clearance   INTEGER DEFAULT 0,
    no_sponsorship    INTEGER DEFAULT 0,
    flags             TEXT,

    -- pipeline
    status            TEXT NOT NULL DEFAULT 'new',
    status_changed_at TEXT,
    applied_at        TEXT,
    notes             TEXT,
    priority          INTEGER DEFAULT 0,
    packet_path       TEXT,
    contact_name      TEXT,
    contact_email     TEXT,
    next_action       TEXT,
    next_action_date  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score    ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company  ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_seen     ON jobs(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs(source);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- status_change | note | email | reminder | packet
    old_value  TEXT,
    new_value  TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts DESC);

-- Every scrape run, so spend and yield stay visible.
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    source        TEXT NOT NULL,
    query         TEXT,
    found         INTEGER DEFAULT 0,
    new_jobs      INTEGER DEFAULT 0,
    est_cost_usd  REAL DEFAULT 0,
    ok            INTEGER DEFAULT 1,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts DESC);

-- Companies whose ATS boards we poll directly (free, unlimited).
CREATE TABLE IF NOT EXISTS watchlist (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    ats       TEXT NOT NULL,          -- greenhouse | lever | ashby | smartrecruiters | workable
    slug      TEXT NOT NULL,
    tags      TEXT,
    active    INTEGER DEFAULT 1,
    last_ok   TEXT,
    last_err  TEXT,
    UNIQUE(ats, slug)
);

-- Reusable answers for repetitive ATS screening questions.
CREATE TABLE IF NOT EXISTS answer_bank (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    key      TEXT UNIQUE NOT NULL,
    question TEXT,
    answer   TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str | None) -> str:
    """Aggressive normalisation used for building dedupe fingerprints."""
    if not text:
        return ""
    text = text.lower()
    # Strip anything that varies between reposts of the same role.
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\b(senior|sr|junior|jr|lead|staff|principal|i{1,3}|iv|v|1|2|3)\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fingerprint(title: str, company: str | None, location: str | None = None) -> str:
    """Stable identity for a posting across sources.

    LinkedIn, Indeed and the company's own Greenhouse board will all carry the
    same role. We key on normalised title + company (+ the city token) so those
    collapse into one row instead of three.
    """
    city = ""
    if location:
        city = _norm(location.split(",")[0])
    basis = f"{_norm(title)}|{_norm(company)}|{city}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


class DB:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ utils
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------- jobs
    def upsert_job(self, job: dict[str, Any]) -> tuple[int, bool]:
        """Insert a job, or refresh ``last_seen`` if we already have it.

        Returns ``(job_id, is_new)``.
        """
        fp = job.get("fingerprint") or fingerprint(
            job.get("title", ""), job.get("company"), job.get("location")
        )
        now = utcnow()
        cur = self.conn.execute("SELECT id FROM jobs WHERE fingerprint = ?", (fp,))
        row = cur.fetchone()

        if row:
            # Known posting. Refresh volatile fields but never clobber the
            # user's pipeline state or notes.
            self.conn.execute(
                """UPDATE jobs SET last_seen = ?,
                       description = COALESCE(NULLIF(?, ''), description),
                       apply_url   = COALESCE(NULLIF(?, ''), apply_url),
                       salary_raw  = COALESCE(NULLIF(?, ''), salary_raw)
                   WHERE id = ?""",
                (now, job.get("description") or "", job.get("apply_url") or "",
                 job.get("salary_raw") or "", row["id"]),
            )
            self.conn.commit()
            return row["id"], False

        cols = {
            "fingerprint": fp,
            "source": job.get("source", "unknown"),
            "source_id": job.get("source_id"),
            "title": job.get("title", "").strip(),
            "company": (job.get("company") or "").strip() or None,
            "location": job.get("location"),
            "remote": job.get("remote"),
            "description": job.get("description"),
            "url": job.get("url"),
            "apply_url": job.get("apply_url") or job.get("url"),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "salary_raw": job.get("salary_raw"),
            "employment_type": job.get("employment_type"),
            "seniority": job.get("seniority"),
            "posted_at": job.get("posted_at"),
            "first_seen": now,
            "last_seen": now,
            "raw": json.dumps(job.get("raw", {}), default=str)[:200000],
            "status": "new",
            "status_changed_at": now,
        }
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, tuple(cols.values()))
        self.conn.commit()
        return int(cur.lastrowid), True

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def update_score(self, job_id: int, score: float, breakdown: dict,
                     matched: list[str], missing: list[str], flags: dict) -> None:
        self.conn.execute(
            """UPDATE jobs SET score=?, score_breakdown=?, matched_terms=?, missing_terms=?,
                   needs_citizenship=?, needs_clearance=?, no_sponsorship=?, flags=?
               WHERE id=?""",
            (
                round(score, 2),
                json.dumps(breakdown),
                json.dumps(matched[:40]),
                json.dumps(missing[:25]),
                int(flags.get("needs_citizenship", False)),
                int(flags.get("needs_clearance", False)),
                int(flags.get("no_sponsorship", False)),
                json.dumps(flags),
                job_id,
            ),
        )

    def set_status(self, job_id: int, status: str, detail: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        row = self.get_job(job_id)
        if row is None:
            raise ValueError(f"no job with id {job_id}")
        old = row["status"]
        now = utcnow()
        applied_at = row["applied_at"]
        if status == "applied" and not applied_at:
            applied_at = now
        self.conn.execute(
            "UPDATE jobs SET status=?, status_changed_at=?, applied_at=? WHERE id=?",
            (status, now, applied_at, job_id),
        )
        self.log_event(job_id, "status_change", old_value=old, new_value=status, detail=detail)
        self.conn.commit()

    def log_event(self, job_id: int, kind: str, old_value: str | None = None,
                  new_value: str | None = None, detail: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (job_id, ts, kind, old_value, new_value, detail) VALUES (?,?,?,?,?,?)",
            (job_id, utcnow(), kind, old_value, new_value, detail),
        )
        self.conn.commit()

    def events_for(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY ts DESC", (job_id,)
        ).fetchall()

    def query_jobs(self, status: str | None = None, min_score: float = 0.0,
                   limit: int = 50, offset: int = 0, search: str | None = None,
                   source: str | None = None, order: str = "score DESC, first_seen DESC",
                   hide_blocked: bool = False) -> list[sqlite3.Row]:
        where, params = ["score >= ?"], [min_score]
        if status and status != "all":
            if status == "active":
                marks = ",".join("?" * len(ACTIVE_STATUSES))
                where.append(f"status IN ({marks})")
                params.extend(sorted(ACTIVE_STATUSES))
            else:
                where.append("status = ?")
                params.append(status)
        if source:
            where.append("source = ?")
            params.append(source)
        if search:
            where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        if hide_blocked:
            where.append("needs_citizenship = 0 AND needs_clearance = 0 AND no_sponsorship = 0")
        sql = (f"SELECT * FROM jobs WHERE {' AND '.join(where)} "
               f"ORDER BY {order} LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def stale_applications(self, days: int = 21) -> list[sqlite3.Row]:
        """Applications with no movement for ``days`` - candidates for follow-up."""
        return self.conn.execute(
            """SELECT * FROM jobs
               WHERE status IN ('applied','screen','interview','final')
                 AND julianday('now') - julianday(status_changed_at) > ?
               ORDER BY status_changed_at ASC""",
            (days,),
        ).fetchall()

    # ------------------------------------------------------------------- runs
    def log_run(self, source: str, query: str, found: int, new_jobs: int,
                est_cost_usd: float = 0.0, ok: bool = True, detail: str = "") -> None:
        self.conn.execute(
            """INSERT INTO runs (ts, source, query, found, new_jobs, est_cost_usd, ok, detail)
               VALUES (?,?,?,?,?,?,?,?)""",
            (utcnow(), source, query, found, new_jobs, est_cost_usd, int(ok), detail),
        )
        self.conn.commit()

    def spend_this_month(self) -> float:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(est_cost_usd), 0) s FROM runs
               WHERE strftime('%Y-%m', ts) = strftime('%Y-%m', 'now')"""
        ).fetchone()
        return float(row["s"])

    # -------------------------------------------------------------- watchlist
    def add_watch(self, name: str, ats: str, slug: str, tags: str = "") -> bool:
        try:
            self.conn.execute(
                "INSERT INTO watchlist (name, ats, slug, tags) VALUES (?,?,?,?)",
                (name, ats, slug, tags),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def watchlist(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM watchlist"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY name"
        return self.conn.execute(sql).fetchall()

    def mark_watch(self, watch_id: int, ok: bool, err: str = "") -> None:
        if ok:
            self.conn.execute("UPDATE watchlist SET last_ok=?, last_err=NULL WHERE id=?",
                              (utcnow(), watch_id))
        else:
            self.conn.execute("UPDATE watchlist SET last_err=? WHERE id=?", (err[:400], watch_id))
        self.conn.commit()

    # ------------------------------------------------------------ answer bank
    def set_answer(self, key: str, question: str, answer: str) -> None:
        self.conn.execute(
            """INSERT INTO answer_bank (key, question, answer) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET question=excluded.question, answer=excluded.answer""",
            (key, question, answer),
        )
        self.conn.commit()

    def answers(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM answer_bank ORDER BY key").fetchall()

    # -------------------------------------------------------------- meta k/v
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()


def bulk_upsert(db: DB, jobs: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Upsert an iterable of jobs. Returns ``(total_seen, newly_added)``."""
    total = new = 0
    for job in jobs:
        if not job.get("title"):
            continue
        total += 1
        _, is_new = db.upsert_job(job)
        if is_new:
            new += 1
    return total, new
