"""Hardened Sandboxed Tool Runner with capability checks, injection blocking, workspace bounds, and SQLite audit."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
from pydantic import BaseModel, ConfigDict

from gfdrr_corpus.taxonomy import CLEARANCE_MAX_TIER, WB_CLASSIFICATION_ORDER
from .capabilities import AGENT_CAPABILITIES, AgentCapabilityManifest


class SecurityViolationError(PermissionError):
    """Raised when an agent attempts a tool call, tier escalation, path escape, or prompt injection."""

    def __init__(self, violation_type: str, message: str, audit_id: int | None = None) -> None:
        super().__init__(message)
        self.violation_type = violation_type
        self.audit_id = audit_id


class ToolExecutionRecord(BaseModel):
    """Audited tool invocation summary."""

    model_config = ConfigDict(extra="forbid")

    audit_id: int
    agent_role: str
    tool_name: str
    status: str
    violation_type: str | None = None
    reason: str
    duration_ms: float


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PROMPT_INJECTION_OVERRIDE",
        re.compile(
            r"(?:ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
            r"override\s+(?:policy|clearance|classification|guardrail)|"
            r"bypass\s+(?:sandbox|classification|redaction)|"
            r"reveal\s+unredacted\s+(?:pii|cnic|ssn|depot)|"
            r"system\s*:\s*grant\s+restricted_audit)",
            re.I,
        ),
    ),
    (
        "SHELL_OR_CODE_EXEC_ATTEMPT",
        re.compile(r"(?:;\s*rm\s+-rf|\$\(|`.*`|subprocess\.Popen|os\.system\(|/bin/sh|/etc/passwd)", re.I),
    ),
)


class SandboxedToolRunner:
    """Executes allowlisted read-only tools inside least-privilege agent capability scopes."""

    def __init__(
        self,
        allowed_read_root: Path,
        db_path: Path | str | None = None,
        timeout_seconds: float = 5.0,
        capabilities: dict[str, AgentCapabilityManifest] | None = None,
    ) -> None:
        self.allowed_read_root = allowed_read_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.capabilities = capabilities or dict(AGENT_CAPABILITIES)
        self.db_path = str(db_path) if db_path is not None else ":memory:"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._tools: dict[str, Callable[..., Any]] = {}
        self._call_counts: dict[str, int] = {}
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS security_audit_log (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                utc_timestamp TEXT NOT NULL,
                run_id TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                violation_type TEXT,
                reason TEXT NOT NULL,
                duration_ms REAL NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS prevent_sec_audit_update
            BEFORE UPDATE ON security_audit_log
            BEGIN
                SELECT RAISE(ABORT, 'Immutable security audit log: UPDATE prohibited');
            END;

            CREATE TRIGGER IF NOT EXISTS prevent_sec_audit_delete
            BEFORE DELETE ON security_audit_log
            BEGIN
                SELECT RAISE(ABORT, 'Immutable security audit log: DELETE prohibited');
            END;
            """
        )
        self._conn.commit()

    def register_tool(self, tool_name: str, fn: Callable[..., Any]) -> None:
        self._tools[tool_name] = fn

    def validate_read_path(self, candidate_path: Path | str) -> Path:
        raw_str = str(candidate_path)
        if ".." in Path(raw_str).parts:
            raise SecurityViolationError(
                "WORKSPACE_BOUNDARY_ESCAPE",
                f"Relative parent traversal ('..') denied for path '{raw_str}'.",
            )
        resolved = Path(candidate_path).resolve()
        if not resolved.is_relative_to(self.allowed_read_root):
            raise SecurityViolationError(
                "WORKSPACE_BOUNDARY_ESCAPE",
                f"Path '{resolved}' is outside read-only sandbox root '{self.allowed_read_root}'.",
            )
        return resolved

    def _scan_params_for_injection(self, params: dict[str, Any]) -> None:
        serialized = repr(params)
        for v_type, pattern in _INJECTION_PATTERNS:
            if pattern.search(serialized):
                raise SecurityViolationError(
                    v_type,
                    f"Blocked adversarial directive matching {v_type} in tool arguments.",
                )

    def invoke(
        self,
        *,
        run_id: str,
        agent_role: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, ToolExecutionRecord]:
        t0 = time.perf_counter()

        try:
            if agent_role not in self.capabilities:
                raise SecurityViolationError(
                    "UNKNOWN_AGENT_ROLE", f"Unregistered agent role '{agent_role}'."
                )
            manifest = self.capabilities[agent_role]

            # 1. Check tool allowlist & explicit deny list
            if not manifest.can_invoke_tool(tool_name):
                raise SecurityViolationError(
                    "CAPABILITY_DENIED",
                    f"Agent '{agent_role}' is denied tool '{tool_name}' "
                    f"(allowed={list(manifest.allowed_tools)}).",
                )
            if tool_name not in self._tools:
                raise SecurityViolationError(
                    "UNREGISTERED_TOOL",
                    f"Tool '{tool_name}' is not registered in sandbox allowlist.",
                )

            # 2. Check per-run call quota
            key = f"{run_id}::{agent_role}"
            used = self._call_counts.get(key, 0)
            if used >= manifest.max_calls_per_run:
                raise SecurityViolationError(
                    "CALL_QUOTA_EXCEEDED",
                    f"Agent '{agent_role}' exceeded max_calls_per_run ({manifest.max_calls_per_run}).",
                )

            # 3. Enforce read-only path boundary if path argument is supplied
            if "table_path" in params and params["table_path"] is not None:
                params = dict(params)
                params["table_path"] = self.validate_read_path(params["table_path"])

            # 4. Scan parameters for prompt injection or shell payloads
            self._scan_params_for_injection(params)

            # 5. Enforce classification tier ceiling on RAG queries
            if tool_name == "rag.query":
                req_role = str(params.get("clearance_role", "public_external"))
                req_tier = str(params.get("requested_tier", CLEARANCE_MAX_TIER.get(req_role, req_role)))
                if req_tier not in WB_CLASSIFICATION_ORDER or not manifest.can_access_tier(req_tier):
                    raise SecurityViolationError(
                        "CLASSIFICATION_ESCALATION_BLOCKED",
                        f"Agent '{agent_role}' (max tier '{manifest.max_wb_classification}') "
                        f"attempted unauthorized escalation to '{req_tier}'.",
                    )

            # 6. Execute inside bounded wall-clock timeout
            fn = self._tools[tool_name]
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(fn, **params)
                try:
                    result = fut.result(timeout=self.timeout_seconds)
                except FuturesTimeoutError as exc:
                    raise SecurityViolationError(
                        "EXECUTION_TIMEOUT",
                        f"Tool '{tool_name}' exceeded {self.timeout_seconds:.1f}s sandbox timeout.",
                    ) from exc

            self._call_counts[key] = used + 1
            elapsed = (time.perf_counter() - t0) * 1000.0
            audit_id = self._record_audit(
                run_id=run_id,
                agent_role=agent_role,
                tool_name=tool_name,
                status="ALLOWED",
                violation_type=None,
                reason=f"Authorized within '{manifest.max_wb_classification}' capability scope.",
                duration_ms=elapsed,
            )
            return result, ToolExecutionRecord(
                audit_id=audit_id,
                agent_role=agent_role,
                tool_name=tool_name,
                status="ALLOWED",
                violation_type=None,
                reason=f"Authorized within '{manifest.max_wb_classification}' capability scope.",
                duration_ms=round(elapsed, 3),
            )

        except SecurityViolationError as sec_exc:
            elapsed = (time.perf_counter() - t0) * 1000.0
            audit_id = self._record_audit(
                run_id=run_id,
                agent_role=agent_role,
                tool_name=tool_name,
                status="BLOCKED",
                violation_type=sec_exc.violation_type,
                reason=str(sec_exc),
                duration_ms=elapsed,
            )
            sec_exc.audit_id = audit_id
            raise

    def _record_audit(
        self,
        *,
        run_id: str,
        agent_role: str,
        tool_name: str,
        status: str,
        violation_type: str | None,
        reason: str,
        duration_ms: float,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO security_audit_log (
                utc_timestamp, run_id, agent_role, tool_name,
                status, violation_type, reason, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                run_id,
                agent_role,
                tool_name,
                status,
                violation_type,
                reason,
                round(duration_ms, 3),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_security_events(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            cur = self._conn.execute(
                "SELECT * FROM security_audit_log WHERE run_id = ? ORDER BY audit_id ASC",
                (run_id,),
            )
        else:
            cur = self._conn.execute("SELECT * FROM security_audit_log ORDER BY audit_id ASC")
        return [dict(r) for r in cur.fetchall()]
