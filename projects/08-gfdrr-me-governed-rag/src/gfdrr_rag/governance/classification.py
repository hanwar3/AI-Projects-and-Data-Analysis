"""World Bank AMS 6.21A 4-tier Data Classification Policy Gate with immutable SQLite audit log."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from gfdrr_corpus.taxonomy import (
    CLEARANCE_MAX_TIER,
    CLEARANCE_ROLES,
    WB_CLASSIFICATION_ORDER,
)
from ..ingestion.chunker import IngestedChunk

GateDecision = Literal["ALLOWED", "PARTIAL_FILTERED", "BLOCKED_CLASSIFICATION"]


class ClassificationVerdict(BaseModel):
    """Structured result of pre-retrieval classification gating."""

    model_config = ConfigDict(extra="forbid")

    decision: GateDecision
    clearance_role: str
    max_allowed_tier: str
    total_corpus_chunks: int
    authorized_chunk_count: int
    blocked_chunk_count: int
    targeted_blocked_doc_ids: list[str] = Field(default_factory=list)
    targeted_blocked_tiers: list[str] = Field(default_factory=list)
    audit_event_id: int | None = None
    reason: str


class ClassificationPolicyGate:
    """Enforces hard pre-retrieval WB classification filtering and immutable SQLite audit logging."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = str(db_path) if db_path is not None else ":memory:"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS classification_audit_log (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                utc_timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL,
                clearance_role TEXT NOT NULL,
                max_allowed_tier TEXT NOT NULL,
                query_sha256 TEXT NOT NULL,
                query_preview TEXT NOT NULL,
                decision TEXT NOT NULL,
                blocked_doc_ids TEXT NOT NULL,
                blocked_tiers TEXT NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS prevent_audit_update
            BEFORE UPDATE ON classification_audit_log
            BEGIN
                SELECT RAISE(ABORT, 'Immutable audit table: UPDATE is prohibited');
            END;

            CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
            BEFORE DELETE ON classification_audit_log
            BEGIN
                SELECT RAISE(ABORT, 'Immutable audit table: DELETE is prohibited');
            END;
            """
        )
        self._conn.commit()

    @staticmethod
    def validate_role(clearance_role: str) -> str:
        if clearance_role not in CLEARANCE_ROLES:
            raise ValueError(
                f"Invalid clearance_role '{clearance_role}'. Must be one of {CLEARANCE_ROLES}"
            )
        return CLEARANCE_MAX_TIER[clearance_role]

    def filter_chunks(
        self,
        chunks: list[IngestedChunk],
        query: str,
        clearance_role: str,
        metadata_filter: dict[str, str] | None = None,
        user_id: str = "anonymous_session",
    ) -> tuple[list[IngestedChunk], ClassificationVerdict]:
        max_tier = self.validate_role(clearance_role)
        max_rank = WB_CLASSIFICATION_ORDER[max_tier]
        meta_filter = metadata_filter or {}

        authorized: list[IngestedChunk] = []
        blocked_all: list[IngestedChunk] = []

        for chunk in chunks:
            chunk_rank = WB_CLASSIFICATION_ORDER[chunk.wb_classification]
            if chunk_rank <= max_rank:
                authorized.append(chunk)
            else:
                blocked_all.append(chunk)

        # Detect whether the user's query or explicit metadata filter directly targets restricted grants/docs
        q_lower = query.lower()
        explicit_grant_ids = set(re.findall(r"GFDRR-[A-Z]{2,4}-202\d-\d{3}", query, flags=re.IGNORECASE))
        explicit_doc_ids = set(re.findall(r"DOC-GFDRR-\d{3}", query, flags=re.IGNORECASE))

        targeted_blocked: list[IngestedChunk] = []
        targeted_authorized: list[IngestedChunk] = []

        def _matches_target(c: IngestedChunk) -> bool:
            if explicit_grant_ids and c.grant_id.upper() in {g.upper() for g in explicit_grant_ids}:
                return True
            if explicit_doc_ids and c.doc_id.upper() in {d.upper() for d in explicit_doc_ids}:
                return True
            if "country" in meta_filter and c.country.lower() == meta_filter["country"].lower():
                return True
            if c.country.lower() != "global" and c.country.lower() in q_lower:
                return True
            return False

        for c in chunks:
            if _matches_target(c):
                if WB_CLASSIFICATION_ORDER[c.wb_classification] <= max_rank:
                    targeted_authorized.append(c)
                else:
                    targeted_blocked.append(c)

        blocked_doc_ids = sorted({c.doc_id for c in targeted_blocked})
        blocked_tiers = sorted(
            {c.wb_classification for c in targeted_blocked},
            key=lambda t: WB_CLASSIFICATION_ORDER[t],
        )

        # Hard block if the query explicitly cites a restricted grant/doc OR all matching target docs exceed clearance
        explicitly_cited_blocked = any(
            (c.grant_id.upper() in {g.upper() for g in explicit_grant_ids})
            or (c.doc_id.upper() in {d.upper() for d in explicit_doc_ids})
            for c in targeted_blocked
        )

        if explicitly_cited_blocked or (targeted_blocked and not targeted_authorized):
            decision: GateDecision = "BLOCKED_CLASSIFICATION"
            reason = (
                f"Access denied under World Bank AMS 6.21A: role '{clearance_role}' "
                f"(max tier '{max_tier}') cannot retrieve {', '.join(blocked_tiers)} records "
                f"({', '.join(blocked_doc_ids)})."
            )
            audit_id = self._log_event(
                user_id=user_id,
                clearance_role=clearance_role,
                max_allowed_tier=max_tier,
                query=query,
                decision=decision,
                blocked_doc_ids=blocked_doc_ids,
                blocked_tiers=blocked_tiers,
                reason=reason,
            )
            return [], ClassificationVerdict(
                decision=decision,
                clearance_role=clearance_role,
                max_allowed_tier=max_tier,
                total_corpus_chunks=len(chunks),
                authorized_chunk_count=0,
                blocked_chunk_count=len(blocked_all),
                targeted_blocked_doc_ids=blocked_doc_ids,
                targeted_blocked_tiers=blocked_tiers,
                audit_event_id=audit_id,
                reason=reason,
            )

        if targeted_blocked:
            decision = "PARTIAL_FILTERED"
            reason = (
                f"Pre-retrieval gate allowed '{max_tier}' and below; suppressed "
                f"{len(blocked_doc_ids)} higher-tier document(s) ({', '.join(blocked_doc_ids)})."
            )
            audit_id = self._log_event(
                user_id=user_id,
                clearance_role=clearance_role,
                max_allowed_tier=max_tier,
                query=query,
                decision=decision,
                blocked_doc_ids=blocked_doc_ids,
                blocked_tiers=blocked_tiers,
                reason=reason,
            )
        else:
            decision = "ALLOWED"
            reason = (
                f"Role '{clearance_role}' authorized up to '{max_tier}' "
                f"({len(authorized)}/{len(chunks)} corpus chunks eligible)."
            )
            audit_id = None

        return authorized, ClassificationVerdict(
            decision=decision,
            clearance_role=clearance_role,
            max_allowed_tier=max_tier,
            total_corpus_chunks=len(chunks),
            authorized_chunk_count=len(authorized),
            blocked_chunk_count=len(blocked_all),
            targeted_blocked_doc_ids=blocked_doc_ids,
            targeted_blocked_tiers=blocked_tiers,
            audit_event_id=audit_id,
            reason=reason,
        )

    def _log_event(
        self,
        user_id: str,
        clearance_role: str,
        max_allowed_tier: str,
        query: str,
        decision: str,
        blocked_doc_ids: list[str],
        blocked_tiers: list[str],
        reason: str,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        q_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        cur = self._conn.execute(
            """
            INSERT INTO classification_audit_log (
                utc_timestamp, user_id, clearance_role, max_allowed_tier,
                query_sha256, query_preview, decision, blocked_doc_ids, blocked_tiers, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                user_id,
                clearance_role,
                max_allowed_tier,
                q_hash,
                query[:160],
                decision,
                ",".join(blocked_doc_ids),
                ",".join(blocked_tiers),
                reason,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_audit_events(self, limit: int = 50) -> list[dict[str, object]]:
        cur = self._conn.execute(
            "SELECT * FROM classification_audit_log ORDER BY event_id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
