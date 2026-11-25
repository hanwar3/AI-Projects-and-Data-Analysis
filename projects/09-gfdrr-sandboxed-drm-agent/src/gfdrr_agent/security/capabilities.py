"""Least-privilege capability manifests for each specialist and control agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from gfdrr_corpus.taxonomy import CLEARANCE_MAX_TIER, WB_CLASSIFICATION_ORDER


class AgentCapabilityManifest(BaseModel):
    """Scoped least-privilege permission boundary assigned to a single agent role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_role: str
    display_name: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    max_wb_classification: str
    allow_external_export: bool = False
    allow_shell_exec: bool = False
    allow_filesystem_write: bool = False
    max_calls_per_run: int = 6

    def can_invoke_tool(self, tool_name: str) -> bool:
        if tool_name in self.denied_tools:
            return False
        return tool_name in self.allowed_tools

    def can_access_tier(self, tier_or_clearance_role: str) -> bool:
        if tier_or_clearance_role in CLEARANCE_MAX_TIER:
            target_tier = CLEARANCE_MAX_TIER[tier_or_clearance_role]
        else:
            target_tier = tier_or_clearance_role
        if target_tier not in WB_CLASSIFICATION_ORDER:
            return False
        return WB_CLASSIFICATION_ORDER[target_tier] <= WB_CLASSIFICATION_ORDER[self.max_wb_classification]


AGENT_CAPABILITIES: dict[str, AgentCapabilityManifest] = {
    "planner": AgentCapabilityManifest(
        agent_role="planner",
        display_name="GFDRR Portfolio Planner",
        allowed_tools=("taxonomy.map_subtasks",),
        denied_tools=("rag.query", "shell.exec", "export.external", "fs.write", "network.http"),
        max_wb_classification="Public",
        max_calls_per_run=2,
    ),
    "grant_me_analyst": AgentCapabilityManifest(
        agent_role="grant_me_analyst",
        display_name="Grant M&E Analyst",
        allowed_tools=("rag.query",),
        denied_tools=("export.external", "shell.exec", "fs.write", "network.http", "geo.compute_exposure_stats"),
        max_wb_classification="Official Use Only",
        max_calls_per_run=4,
    ),
    "geospatial_hazard_summarizer": AgentCapabilityManifest(
        agent_role="geospatial_hazard_summarizer",
        display_name="Geospatial Hazard Summarizer",
        allowed_tools=("geo.compute_exposure_stats",),
        denied_tools=("rag.query", "export.external", "shell.exec", "fs.write", "network.http"),
        max_wb_classification="Public",
        max_calls_per_run=3,
    ),
    "crosscutting_fcv_gender_reviewer": AgentCapabilityManifest(
        agent_role="crosscutting_fcv_gender_reviewer",
        display_name="Crosscutting Inclusion & FCV Reviewer",
        allowed_tools=("rag.query", "policy.audit_fcv_gender"),
        denied_tools=("export.external", "shell.exec", "fs.write", "network.http"),
        max_wb_classification="Official Use Only",
        max_calls_per_run=4,
    ),
    "critic_arbitrator": AgentCapabilityManifest(
        agent_role="critic_arbitrator",
        display_name="Arbitration & Grounding Critic",
        allowed_tools=("provenance.verify_manifest", "rubric.verify_claims"),
        denied_tools=("export.external", "shell.exec", "fs.write", "network.http"),
        max_wb_classification="Official Use Only",
        max_calls_per_run=4,
    ),
}
