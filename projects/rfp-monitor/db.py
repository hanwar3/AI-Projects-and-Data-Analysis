"""
db.py -- SQLite persistence.

The PRIMARY KEY on notices.notice_id is what makes duplicates impossible:
rows go in with INSERT OR IGNORE, so seeing a notice again is a no-op and
its original date_found is never overwritten.
"""
import sqlite3
from contextlib import closing
from datetime import datetime

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id   TEXT PRIMARY KEY,
    date_found  TEXT NOT NULL,
    source      TEXT,
    type        TEXT,
    title       TEXT,
    detail      TEXT,
    country     TEXT,
    deadline    TEXT,
    ref         TEXT,
    url         TEXT,
    agency      TEXT,
    posted      TEXT,
    matched     TEXT,
    score       INTEGER DEFAULT 0,
    triage      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_log (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time    TEXT NOT NULL,
    source      TEXT,
    new_count   INTEGER,
    status      TEXT,
    error       TEXT,
    trigger     TEXT,
    fetched     INTEGER,
    matched     INTEGER,
    seconds     REAL
);
CREATE INDEX IF NOT EXISTS idx_notices_found ON notices(date_found);
CREATE INDEX IF NOT EXISTS idx_run_log_time ON run_log(run_time);
"""

# Columns beyond the original spec schema, added to databases that predate them.
_ADDED_COLUMNS = {
    "notices": {"agency": "TEXT", "posted": "TEXT", "matched": "TEXT",
                "score": "INTEGER DEFAULT 0", "triage": "TEXT DEFAULT ''"},
    "run_log": {"trigger": "TEXT", "fetched": "INTEGER", "matched": "INTEGER", "seconds": "REAL"},
}

_INSERT_COLS = ("notice_id", "date_found", "source", "type", "title", "detail", "country",
                "deadline", "ref", "url", "agency", "posted", "matched", "score")

TRIAGE_OPTIONS = ["", "shortlist", "applied", "not relevant"]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # dashboard reads while a check writes
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db() -> None:
    with closing(connect()) as con, con:
        con.executescript(SCHEMA)
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def insert_notices(rows: list[dict]) -> list[dict]:
    """INSERT OR IGNORE every row; return only the rows that were actually new."""
    found = now_str()
    sql = (f"INSERT OR IGNORE INTO notices ({', '.join(_INSERT_COLS)}) "
           f"VALUES ({', '.join('?' * len(_INSERT_COLS))})")
    new = []
    with closing(connect()) as con, con:
        for r in rows:
            cur = con.execute(sql, (r["notice_id"], found, *(r.get(c) for c in _INSERT_COLS[2:])))
            if cur.rowcount == 1:
                new.append(r)
    return new


def log_run(run_time, source, new_count, status, error=None, trigger=None,
            fetched=None, matched=None, seconds=None) -> None:
    with closing(connect()) as con, con:
        con.execute(
            "INSERT INTO run_log (run_time, source, new_count, status, error, trigger, fetched, matched, seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_time, source, new_count, status, error, trigger, fetched, matched, seconds))


def all_notices() -> list[dict]:
    with closing(connect()) as con:
        return [dict(r) for r in con.execute("SELECT * FROM notices ORDER BY date_found DESC, score DESC")]


def recent_runs(limit: int = 20) -> list[dict]:
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(
            "SELECT run_time, source, trigger, fetched, matched, new_count, status, error, seconds "
            "FROM run_log ORDER BY run_id DESC LIMIT ?", (limit,))]


def last_run_time() -> str | None:
    with closing(connect()) as con:
        return con.execute("SELECT MAX(run_time) FROM run_log").fetchone()[0]


def last_run_id() -> int:
    """Grows with every run_log row; lets the dashboard notice finished checks."""
    with closing(connect()) as con:
        return con.execute("SELECT COALESCE(MAX(run_id), 0) FROM run_log").fetchone()[0]


def set_triage(notice_id: str, value: str) -> None:
    with closing(connect()) as con, con:
        con.execute("UPDATE notices SET triage = ? WHERE notice_id = ?", (value or "", notice_id))
