"""Explicit Human-in-Command (HIC) & HITL checkpoint gate with HMAC-signed audit records."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import sqlite3
from typing import Literal
from pydantic import BaseModel, ConfigDict

HICAction = Literal["approve", "revise", "reject"]


class HICDecisionRecord(BaseModel):
    """Signed Human-in-Command review and release record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hic_id: int
    run_id: str
    utc_timestamp: str
    reviewer_id: str
    reviewer_role: str
    decision: HICAction
    rationale: str
    draft_sha256: str
    signature_hmac: str


class HumanInCommandGate:
    """Enforces mandatory human sign-off before releasing portfolio ratings or external briefs."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        secret_key: str = "wbg-gfdrr-hic-command-signing-key-2026",
    ) -> None:
        self.db_path = str(db_path) if db_path is not None else ":memory:"
        self._secret = secret_key.encode("utf-8")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hic_approval_records (
                hic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                utc_timestamp TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                reviewer_role TEXT NOT NULL,
                decision TEXT NOT NULL,
                rationale TEXT NOT NULL,
                draft_sha256 TEXT NOT NULL,
                signature_hmac TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def record_decision(
        self,
        *,
        run_id: str,
        draft_text: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: HICAction,
        rationale: str,
    ) -> HICDecisionRecord:
        if len(rationale.strip()) < 8:
            raise ValueError("HIC decision requires a substantive rationale (>= 8 chars).")
        if decision not in ("approve", "revise", "reject"):
            raise ValueError(f"Invalid HIC decision: {decision}")

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        draft_sha = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()
        signing_msg = f"{run_id}|{ts}|{reviewer_id}|{reviewer_role}|{decision}|{draft_sha}|{rationale}"
        sig = hmac.new(self._secret, signing_msg.encode("utf-8"), hashlib.sha256).hexdigest()

        cur = self._conn.execute(
            """
            INSERT INTO hic_approval_records (
                run_id, utc_timestamp, reviewer_id, reviewer_role,
                decision, rationale, draft_sha256, signature_hmac
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, ts, reviewer_id, reviewer_role, decision, rationale, draft_sha, sig),
        )
        self._conn.commit()
        return HICDecisionRecord(
            hic_id=int(cur.lastrowid),
            run_id=run_id,
            utc_timestamp=ts,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            rationale=rationale,
            draft_sha256=draft_sha,
            signature_hmac=sig,
        )

    def get_latest_decision(self, run_id: str) -> HICDecisionRecord | None:
        cur = self._conn.execute(
            "SELECT * FROM hic_approval_records WHERE run_id = ? ORDER BY hic_id DESC LIMIT 1",
            (run_id,),
        )
        row = cur.fetchone()
        return HICDecisionRecord(**dict(row)) if row else None
