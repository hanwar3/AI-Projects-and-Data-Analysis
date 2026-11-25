"""Stateful Multi-Agent Supervisor with Token/Step Budgets, SQLite Crash Recovery, OTel Traces, and Mandatory HIC Gate."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field

from gfdrr_corpus.build_corpus import TABLES_DIR
from gfdrr_corpus.loader import ensure_built
from gfdrr_rag.config import RAGConfig
from gfdrr_rag.governance.provenance import ProvenanceManifest
from gfdrr_rag.pipeline import GovernedRAGPipeline

from ..security.capabilities import AGENT_CAPABILITIES, AgentCapabilityManifest
from ..security.hic_gate import HICAction, HICDecisionRecord, HumanInCommandGate
from ..security.sandbox import SandboxedToolRunner
from .critic import CriticArbitratorAgent, verify_claims_tool
from .planner import ExecutionPlan, PlannerAgent, map_request_taxonomy
from .specialists import (
    CrosscuttingInclusionFCVReviewerAgent,
    GeospatialHazardSummarizerAgent,
    GrantMEAnalystAgent,
    audit_fcv_gender_policy_tool,
    compute_exposure_stats_tool,
)


def ensure_tables_ready() -> Path:
    from gfdrr_corpus.loader import ensure_built

    ensure_built()
    return TABLES_DIR / "country_hazard_exposure.csv"


class SupervisorConfig(BaseModel):
    """Hard operational budget & governance config for the multi-agent supervisor."""

    model_config = ConfigDict(extra="forbid")

    max_tokens_budget: int = Field(default=6500, ge=500, le=50000)
    max_cost_usd_budget: float = Field(default=0.05, ge=0.001, le=5.0)
    max_steps_budget: int = Field(default=10, ge=2, le=30)
    enable_critic: bool = True
    require_hic_approval: bool = True
    inject_unverified_claim_for_audit: bool = False
    checkpoint_db_path: Path | None = None


class OTelSpan(BaseModel):
    """OpenTelemetry-style execution trace span."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    parent_span_id: str | None
    node_name: str
    agent_role: str
    status: str
    tokens_used: int
    estimated_cost_usd: float
    duration_ms: float
    checkpoint_seq: int
    tool_events: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRunResult(BaseModel):
    """Auditable execution state & output of a multi-agent DRM brief run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    trace_id: str
    request: str
    status: Literal[
        "PAUSED_AWAITING_HIC",
        "APPROVED_FINAL",
        "REVISION_REQUESTED",
        "REJECTED",
        "COMPLETED_NO_HIC",
        "BUDGET_EXCEEDED",
    ]
    completed_nodes: list[str]
    recovered_from_checkpoint: bool = False
    total_tokens_used: int
    total_estimated_cost_usd: float
    total_duration_ms: float
    rubric_coverage_pct: float
    unsupported_claims_caught: int
    crosscutting_gaps_identified: list[str] = Field(default_factory=list)
    all_provenance_verified: bool = True
    draft_brief_markdown: str
    final_brief_markdown: str | None = None
    hic_record: HICDecisionRecord | None = None
    spans: list[OTelSpan] = Field(default_factory=list)
    security_events: list[dict[str, Any]] = Field(default_factory=list)


class BudgetExceededError(RuntimeError):
    """Raised when token, step, or cost budgets are exceeded."""


class SimulatedWorkerCrashError(RuntimeError):
    """Simulated mid-workflow worker crash used to prove deterministic SQLite checkpoint recovery."""


class SupervisorOrchestrator:
    """Coordinates Planner, Scoped Specialists, Critic, SQLite Checkpoints, and Mandatory HIC Gate."""

    NODE_ORDER: tuple[str, ...] = (
        "planner",
        "grant_me_analyst",
        "geospatial_hazard_summarizer",
        "crosscutting_fcv_gender_reviewer",
        "critic_arbitrator",
    )

    def __init__(
        self,
        config: SupervisorConfig | None = None,
        rag_pipeline: GovernedRAGPipeline | None = None,
        capabilities: dict[str, AgentCapabilityManifest] | None = None,
    ) -> None:
        self.config = config or SupervisorConfig()
        self.rag = rag_pipeline or GovernedRAGPipeline(RAGConfig())
        self.hazard_csv_path = ensure_tables_ready()
        self.allowed_root = self.hazard_csv_path.parent.parent

        db_target = str(self.config.checkpoint_db_path) if self.config.checkpoint_db_path else ":memory:"
        self._conn = sqlite3.connect(db_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_checkpoint_schema()

        self.sandbox = SandboxedToolRunner(
            allowed_read_root=self.allowed_root,
            db_path=self.config.checkpoint_db_path,
            capabilities=capabilities or dict(AGENT_CAPABILITIES),
        )
        self.hic_gate = HumanInCommandGate(db_path=self.config.checkpoint_db_path)
        self._register_allowlisted_tools()

        self.planner = PlannerAgent()
        self.me_agent = GrantMEAnalystAgent()
        self.geo_agent = GeospatialHazardSummarizerAgent()
        self.cc_agent = CrosscuttingInclusionFCVReviewerAgent()
        self.critic = CriticArbitratorAgent()

    def _init_checkpoint_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                checkpoint_seq INTEGER NOT NULL,
                node_name TEXT NOT NULL,
                utc_timestamp TEXT NOT NULL,
                state_sha256 TEXT NOT NULL,
                state_json TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _register_allowlisted_tools(self) -> None:
        self.sandbox.register_tool("taxonomy.map_subtasks", map_request_taxonomy)
        self.sandbox.register_tool("geo.compute_exposure_stats", compute_exposure_stats_tool)
        self.sandbox.register_tool("policy.audit_fcv_gender", audit_fcv_gender_policy_tool)
        self.sandbox.register_tool("rubric.verify_claims", verify_claims_tool)

        def _rag_query_tool(
            query: str,
            clearance_role: str = "wbg_analyst",
            metadata_filter: dict[str, str] | None = None,
            requested_tier: str | None = None,
        ) -> dict[str, Any]:
            _ = requested_tier
            resp = self.rag.query(
                query,
                clearance_role=clearance_role,
                metadata_filter=metadata_filter or {},
            )
            return {
                "abstained": resp.abstained,
                "answer_text": resp.synthesis.answer_text,
                "retrieved_chunks": [ev.model_dump() for ev in resp.retrieved_evidence],
                "provenance": resp.provenance.model_dump(),
            }

        def _verify_manifest_tool(manifest_dict: dict[str, Any]) -> dict[str, Any]:
            manifest = ProvenanceManifest.model_validate(manifest_dict)
            res = self.rag.provenance_signer.verify_manifest(manifest)
            return res.model_dump()

        self.sandbox.register_tool("rag.query", _rag_query_tool)
        self.sandbox.register_tool("provenance.verify_manifest", _verify_manifest_tool)

    def _save_checkpoint(self, run_id: str, seq: int, node_name: str, state: dict[str, Any]) -> str:
        payload = json.dumps(state, sort_keys=True)
        sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._conn.execute(
            """
            INSERT INTO workflow_checkpoints (
                run_id, checkpoint_seq, node_name, utc_timestamp, state_sha256, state_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, seq, node_name, datetime.now(timezone.utc).isoformat(), sha, payload),
        )
        self._conn.commit()
        return sha

    def load_latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            """
            SELECT * FROM workflow_checkpoints
            WHERE run_id = ?
            ORDER BY checkpoint_seq DESC LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = json.loads(row["state_json"])
        data["_checkpoint_seq"] = int(row["checkpoint_seq"])
        data["_state_sha256"] = str(row["state_sha256"])
        return data

    def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT checkpoint_seq, node_name, utc_timestamp, state_sha256 FROM workflow_checkpoints WHERE run_id = ? ORDER BY checkpoint_seq ASC",
            (run_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _estimate_tokens_and_cost(text_payload: str) -> tuple[int, float]:
        # Deterministic token accounting (~4 chars/token; blended $3.00 / 1M tokens)
        tokens = max(65, len(text_payload) // 4)
        cost = round(tokens * 0.000003, 6)
        return tokens, cost

    def _check_budgets(self, state: dict[str, Any]) -> None:
        if state["steps_executed"] > self.config.max_steps_budget:
            raise BudgetExceededError(
                f"Step budget exceeded ({state['steps_executed']} > {self.config.max_steps_budget})."
            )
        if state["total_tokens"] > self.config.max_tokens_budget:
            raise BudgetExceededError(
                f"Token budget exceeded ({state['total_tokens']} > {self.config.max_tokens_budget})."
            )
        if state["total_cost_usd"] > self.config.max_cost_usd_budget:
            raise BudgetExceededError(
                f"Cost budget exceeded (${state['total_cost_usd']:.5f} > ${self.config.max_cost_usd_budget:.5f})."
            )

    def execute_brief(
        self,
        request: str,
        *,
        run_id: str | None = None,
        clearance_role: str = "wbg_analyst",
        simulate_crash_after_node: str | None = None,
    ) -> WorkflowRunResult:
        rid = run_id or f"RUN-{uuid.uuid4().hex[:10].upper()}"
        existing = self.load_latest_checkpoint(rid)

        if existing is not None:
            state = existing
            recovered = True
        else:
            recovered = False
            trace_id = f"OTEL-{hashlib.sha256(rid.encode('utf-8')).hexdigest()[:16]}"
            state = {
                "run_id": rid,
                "trace_id": trace_id,
                "root_span_id": f"SPAN-ROOT-{rid[-6:]}",
                "request": request,
                "clearance_role": clearance_role,
                "completed_nodes": [],
                "steps_executed": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "total_duration_ms": 0.0,
                "plan": None,
                "grant_output": None,
                "geo_output": None,
                "crosscutting_output": None,
                "critic_output": None,
                "draft_brief_markdown": "",
                "spans": [],
            }

        active_nodes = [
            n for n in self.NODE_ORDER if (n != "critic_arbitrator" or self.config.enable_critic)
        ]

        try:
            for seq_idx, node in enumerate(active_nodes, start=1):
                if node in state["completed_nodes"]:
                    continue

                t_node = time.perf_counter()
                state["steps_executed"] += 1

                if node == "planner":
                    plan_obj, recs = self.planner.build_plan(rid, state["request"], self.sandbox)
                    state["plan"] = plan_obj.model_dump()
                    role_used = self.planner.role
                    payload_repr = json.dumps(state["plan"])

                elif node == "grant_me_analyst":
                    plan_obj = ExecutionPlan.model_validate(state["plan"])
                    me_out, recs = self.me_agent.run(
                        rid,
                        plan_obj,
                        self.sandbox,
                        clearance_role=state["clearance_role"],
                        include_unverified_extrapolation=self.config.inject_unverified_claim_for_audit,
                    )
                    state["grant_output"] = me_out
                    role_used = self.me_agent.role
                    payload_repr = json.dumps(me_out["bullets"])

                elif node == "geospatial_hazard_summarizer":
                    plan_obj = ExecutionPlan.model_validate(state["plan"])
                    geo_out, recs = self.geo_agent.run(
                        rid, plan_obj, self.sandbox, self.hazard_csv_path
                    )
                    state["geo_output"] = geo_out
                    role_used = self.geo_agent.role
                    payload_repr = json.dumps(geo_out["summary_bullets"])

                elif node == "crosscutting_fcv_gender_reviewer":
                    plan_obj = ExecutionPlan.model_validate(state["plan"])
                    cc_out, recs = self.cc_agent.run(
                        rid,
                        plan_obj,
                        state["grant_output"],
                        state["geo_output"],
                        self.sandbox,
                        clearance_role=state["clearance_role"],
                    )
                    state["crosscutting_output"] = cc_out
                    role_used = self.cc_agent.role
                    payload_repr = json.dumps(cc_out)

                elif node == "critic_arbitrator":
                    crit_out, recs = self.critic.run(
                        rid,
                        state["grant_output"],
                        state["geo_output"],
                        state["crosscutting_output"],
                        self.sandbox,
                    )
                    state["critic_output"] = crit_out
                    role_used = self.critic.role
                    payload_repr = json.dumps(crit_out)
                else:
                    raise ValueError(f"Unknown workflow node: {node}")

                dur_ms = round((time.perf_counter() - t_node) * 1000.0, 3)
                toks, cost = self._estimate_tokens_and_cost(payload_repr + state["request"])
                state["total_tokens"] += toks
                state["total_cost_usd"] = round(state["total_cost_usd"] + cost, 6)
                state["total_duration_ms"] = round(state["total_duration_ms"] + dur_ms, 3)

                self._check_budgets(state)

                state["completed_nodes"].append(node)
                span = OTelSpan(
                    trace_id=state["trace_id"],
                    span_id=f"SPAN-{seq_idx:02d}-{node[:8].upper()}",
                    parent_span_id=state["root_span_id"],
                    node_name=node,
                    agent_role=role_used,
                    status="OK",
                    tokens_used=toks,
                    estimated_cost_usd=cost,
                    duration_ms=dur_ms,
                    checkpoint_seq=seq_idx,
                    tool_events=[r.model_dump() for r in recs],
                )
                state["spans"].append(span.model_dump())

                if node == active_nodes[-1]:
                    state["draft_brief_markdown"] = self._assemble_brief(state)

                self._save_checkpoint(rid, seq_idx, node, state)

                if simulate_crash_after_node == node:
                    raise SimulatedWorkerCrashError(
                        f"Simulated worker process crash immediately after checkpointing node '{node}'."
                    )

        except BudgetExceededError:
            return self._build_result(state, status="BUDGET_EXCEEDED", recovered=recovered)

        if not state["draft_brief_markdown"]:
            state["draft_brief_markdown"] = self._assemble_brief(state)
            self._save_checkpoint(rid, len(active_nodes) + 1, "draft_ready", state)

        if self.config.require_hic_approval:
            return self._build_result(state, status="PAUSED_AWAITING_HIC", recovered=recovered)
        return self._build_result(state, status="COMPLETED_NO_HIC", recovered=recovered)

    def resume_from_checkpoint(self, run_id: str) -> WorkflowRunResult:
        ckpt = self.load_latest_checkpoint(run_id)
        if ckpt is None:
            raise ValueError(f"No checkpoint found for run_id '{run_id}'.")
        return self.execute_brief(ckpt["request"], run_id=run_id, clearance_role=ckpt["clearance_role"])

    def submit_hic_decision(
        self,
        *,
        run_id: str,
        reviewer_id: str,
        reviewer_role: str,
        decision: HICAction,
        rationale: str,
    ) -> WorkflowRunResult:
        state = self.load_latest_checkpoint(run_id)
        if state is None:
            raise ValueError(f"Unknown run_id '{run_id}'.")

        record = self.hic_gate.record_decision(
            run_id=run_id,
            draft_text=state["draft_brief_markdown"],
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decision=decision,
            rationale=rationale,
        )
        status_map: dict[HICAction, Literal["APPROVED_FINAL", "REVISION_REQUESTED", "REJECTED"]] = {
            "approve": "APPROVED_FINAL",
            "revise": "REVISION_REQUESTED",
            "reject": "REJECTED",
        }
        status = status_map[decision]
        res = self._build_result(state, status=status, recovered=True, hic_record=record)
        if decision == "approve":
            res.final_brief_markdown = (
                state["draft_brief_markdown"]
                + f"\n\n---\n**HIC Sign-Off:** `{record.decision.upper()}` by `{record.reviewer_id}` "
                f"(`{record.reviewer_role}`) at `{record.utc_timestamp}` · HMAC `{record.signature_hmac[:20]}…`\n"
                f"**Rationale:** {record.rationale}\n"
            )
        return res

    def _assemble_brief(self, state: dict[str, Any]) -> str:
        plan = state["plan"]
        crit = state.get("critic_output")
        grant_bullets = (
            crit["verified_bullets"]
            if crit is not None
            else state["grant_output"]["bullets"]
        )
        geo_bullets = state["geo_output"]["summary_bullets"]
        cc = state["crosscutting_output"]

        lines = [
            f"# GFDRR Country & Thematic Resilience Brief: {', '.join(plan['countries'])}",
            f"**Priority Area:** {plan['priority_area']} · **Thematic Focus:** {plan['thematic_focus']}",
            "",
            "## 1. Governed Grant M&E Evidence & Financing Leverage",
            *(grant_bullets or ["- No authorized grant chunks matched."]),
            "",
            "## 2. Geospatial Multi-Hazard Exposure & 100M Target Alignment",
            *(geo_bullets or ["- No country exposure rows matched."]),
            "",
            "## 3. Crosscutting Inclusive DRM (SADDD/Gender) & FCV Nexus Review",
            *(cc.get("crosscutting_bullets") or ["- Standard gender/FCV screening required."]),
        ]
        if cc.get("identified_gaps"):
            lines.append("")
            lines.append("### Actionable M&E Safeguard Gaps Flagged")
            for g in cc["identified_gaps"]:
                lines.append(f"- **Gap:** {g}")
        return "\n".join(lines)

    def _build_result(
        self,
        state: dict[str, Any],
        *,
        status: Literal[
            "PAUSED_AWAITING_HIC",
            "APPROVED_FINAL",
            "REVISION_REQUESTED",
            "REJECTED",
            "COMPLETED_NO_HIC",
            "BUDGET_EXCEEDED",
        ],
        recovered: bool,
        hic_record: HICDecisionRecord | None = None,
    ) -> WorkflowRunResult:
        crit = state.get("critic_output")
        if crit is not None:
            rubric_pct = float(crit["rubric_coverage_pct"])
            caught = int(crit["unsupported_claims_caught"])
            prov_ok = bool(crit["all_provenance_verified"])
        else:
            # Without critic: compute post-hoc ground-truth rubric coverage & uncaught unsupported claims
            grant_out = state.get("grant_output") or {"bullets": [], "evidence_chunks": []}
            geo_out = state.get("geo_output") or {"records": []}
            cc_out = state.get("crosscutting_output") or {}
            post_check = verify_claims_tool(
                grant_out["bullets"],
                grant_out["evidence_chunks"],
                geo_out["records"],
                cc_out,
            )
            rubric_pct = float(post_check["rubric_coverage_pct"])
            caught = 0
            prov_ok = True

        cc_gaps = (
            state["crosscutting_output"].get("identified_gaps", [])
            if state.get("crosscutting_output")
            else []
        )

        return WorkflowRunResult(
            run_id=state["run_id"],
            trace_id=state["trace_id"],
            request=state["request"],
            status=status,
            completed_nodes=list(state["completed_nodes"]),
            recovered_from_checkpoint=recovered,
            total_tokens_used=int(state["total_tokens"]),
            total_estimated_cost_usd=float(state["total_cost_usd"]),
            total_duration_ms=float(state["total_duration_ms"]),
            rubric_coverage_pct=rubric_pct,
            unsupported_claims_caught=caught,
            crosscutting_gaps_identified=cc_gaps,
            all_provenance_verified=prov_ok,
            draft_brief_markdown=state.get("draft_brief_markdown", ""),
            final_brief_markdown=None,
            hic_record=hic_record,
            spans=[OTelSpan.model_validate(s) for s in state["spans"]],
            security_events=self.sandbox.list_security_events(state["run_id"]),
        )
