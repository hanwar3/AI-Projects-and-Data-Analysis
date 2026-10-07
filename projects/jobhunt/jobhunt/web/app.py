"""Local dashboard - FastAPI + one self-contained HTML page.

Binds to 127.0.0.1 only. Nothing leaves the machine, nothing is hosted, and
there is no account to create. Run it with ``jobhunt serve``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import match, packet
from ..db import DB, DEFAULT_DB, STATUSES
from ..profile import Profile

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


class StatusUpdate(BaseModel):
    status: str
    note: str | None = None


class NoteAdd(BaseModel):
    text: str


def _row_to_dict(r) -> dict[str, Any]:
    d = dict(r)
    for key in ("score_breakdown", "matched_terms", "missing_terms", "flags"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = None
    d.pop("raw", None)
    if d.get("description"):
        d["description"] = d["description"][:24000]
    return d


def create_app(profile_name: str = "example") -> FastAPI:
    api = FastAPI(title="jobhunt", docs_url=None, redoc_url=None)

    def db() -> DB:
        return DB(DEFAULT_DB)

    def prof() -> Profile:
        return Profile.load(profile_name)

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @api.get("/api/jobs")
    def jobs(status: str = "new", min_score: float = 0, limit: int = 200,
             q: str = "", hide_blocked: bool = False, order: str = "score"):
        d = db()
        order_sql = {
            "score": "score DESC, first_seen DESC",
            "newest": "first_seen DESC",
            "posted": "posted_at DESC",
            "company": "company ASC, score DESC",
        }.get(order, "score DESC, first_seen DESC")
        rows = d.query_jobs(status=status, min_score=min_score, limit=limit,
                            search=q or None, hide_blocked=hide_blocked, order=order_sql)
        out = [_row_to_dict(r) for r in rows]
        d.close()
        return out

    @api.get("/api/job/{job_id}")
    def job(job_id: int):
        d = db()
        row = d.get_job(job_id)
        if not row:
            d.close()
            raise HTTPException(404, "no such job")
        payload = _row_to_dict(row)
        payload["events"] = [dict(e) for e in d.events_for(job_id)]
        try:
            payload["explain"] = match.explain(row, prof())
        except Exception as exc:
            payload["explain"] = f"(could not explain: {exc})"
        d.close()
        return payload

    @api.post("/api/job/{job_id}/status")
    def set_status(job_id: int, body: StatusUpdate):
        if body.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        d = db()
        if not d.get_job(job_id):
            d.close()
            raise HTTPException(404, "no such job")
        d.set_status(job_id, body.status, detail=body.note)
        d.close()
        return {"ok": True, "status": body.status}

    @api.post("/api/job/{job_id}/note")
    def add_note(job_id: int, body: NoteAdd):
        d = db()
        row = d.get_job(job_id)
        if not row:
            d.close()
            raise HTTPException(404, "no such job")
        d.log_event(job_id, "note", detail=body.text)
        existing = row["notes"] or ""
        d.conn.execute("UPDATE jobs SET notes=? WHERE id=?",
                       (f"{existing}\n{body.text}".strip(), job_id))
        d.conn.commit()
        d.close()
        return {"ok": True}

    @api.post("/api/job/{job_id}/prep")
    def prep(job_id: int):
        d = db()
        row = d.get_job(job_id)
        if not row:
            d.close()
            raise HTTPException(404, "no such job")
        try:
            folder = packet.generate(d, row, prof())
        except Exception as exc:
            d.close()
            raise HTTPException(500, f"packet generation failed: {exc}")
        d.close()
        return {"ok": True, "folder": str(folder)}

    @api.get("/api/stats")
    def stats():
        d = db()
        counts = d.counts_by_status()
        applied = sum(counts.get(s, 0) for s in
                      ("applied", "screen", "interview", "final", "offer", "rejected", "ghosted"))
        advanced = sum(counts.get(s, 0) for s in ("screen", "interview", "final", "offer"))
        stale = [_row_to_dict(r) for r in d.stale_applications(21)]
        due = [_row_to_dict(r) for r in d.conn.execute(
            "SELECT * FROM jobs WHERE next_action_date IS NOT NULL "
            "AND next_action_date <= date('now') "
            "AND status NOT IN ('rejected','withdrawn','skipped','ghosted') "
            "ORDER BY next_action_date").fetchall()]
        runs = [dict(r) for r in d.conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC LIMIT 12").fetchall()]
        spend = d.spend_this_month()
        by_source = {r["source"]: r["n"] for r in d.conn.execute(
            "SELECT source, COUNT(*) n FROM jobs GROUP BY source ORDER BY n DESC").fetchall()}
        d.close()
        return {
            "counts": counts, "total": sum(counts.values()),
            "applied": applied, "advanced": advanced,
            "response_rate": round(advanced / applied * 100, 1) if applied else 0.0,
            "stale": stale, "due": due, "runs": runs,
            "spend_month": round(spend, 3), "by_source": by_source,
            "statuses": STATUSES,
        }

    @api.exception_handler(Exception)
    async def on_error(request, exc):  # pragma: no cover
        return JSONResponse({"error": str(exc)}, status_code=500)

    return api
