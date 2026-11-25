"""Planner Agent decomposing resilience-brief requests into typed GFDRR sub-tasks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gfdrr_corpus.taxonomy import PRIORITY_AREAS, THEMATIC_AREAS

_KNOWN_COUNTRIES = (
    "Pakistan",
    "Bangladesh",
    "Philippines",
    "Türkiye",
    "Viet Nam",
    "Mozambique",
    "Colombia",
    "Somalia",
    "Kenya",
    "Mali",
    "Yemen",
    "Haiti",
    "Nepal",
    "Belize",
    "Mauritania",
    "Tajikistan",
    "Bhutan",
)


class SubTaskSpec(BaseModel):
    """Typed sub-task emitted by the Planner Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    assigned_agent: str
    objective: str
    countries: tuple[str, ...]
    priority_area: str
    thematic_focus: str
    crosscutting_focus: tuple[str, ...]


class ExecutionPlan(BaseModel):
    """Structured multi-agent execution plan aligned to GFDRR 2026-2030 taxonomy."""

    model_config = ConfigDict(extra="forbid")

    request: str
    countries: list[str]
    thematic_focus: str
    priority_area: str
    subtasks: list[SubTaskSpec] = Field(default_factory=list)


def map_request_taxonomy(request: str) -> dict[str, object]:
    """Allowlisted taxonomy mapping tool invoked by Planner Agent."""
    req_low = request.lower()
    matched_countries = [c for c in _KNOWN_COUNTRIES if c.lower() in req_low]
    if not matched_countries:
        matched_countries = ["Pakistan", "Viet Nam"]

    thematic = THEMATIC_AREAS[4]  # Nature-based Solutions default
    priority = PRIORITY_AREAS[1]
    keyword_map = [
        (("mangrove", "wetland", "nature-based", "nbs", "floodplain", "green drainage"), THEMATIC_AREAS[4], PRIORITY_AREAS[1]),
        (("heat", "cool roof", "cooling"), THEMATIC_AREAS[3], PRIORITY_AREAS[1]),
        (("housing", "plinth", "mason", "e-bps"), THEMATIC_AREAS[2], PRIORITY_AREAS[1]),
        (("school", "clinic", "health", "maternal"), THEMATIC_AREAS[6], PRIORITY_AREAS[1]),
        (("cat ddo", "parametric", "risk finance", "drf", "liquidity"), THEMATIC_AREAS[7], PRIORITY_AREAS[2]),
        (("hydromet", "early warning", "ews", "lead-time", "radio"), THEMATIC_AREAS[9], PRIORITY_AREAS[3]),
        (("eoc", "cprt", "preparedness", "evacuation"), THEMATIC_AREAS[8], PRIORITY_AREAS[3]),
        (("grade", "seismic", "analytics", "results framework"), THEMATIC_AREAS[1], PRIORITY_AREAS[0]),
        (("digital earth", "sar", "lidar", "insar", "impact360"), THEMATIC_AREAS[0], PRIORITY_AREAS[0]),
        (("polder", "bridge", "embankment", "infrastructure"), THEMATIC_AREAS[5], PRIORITY_AREAS[1]),
    ]
    for cues, thm, pri in keyword_map:
        if any(cue in req_low for cue in cues):
            thematic = thm
            priority = pri
            break

    return {
        "countries": matched_countries,
        "thematic_focus": thematic,
        "priority_area": priority,
    }


class PlannerAgent:
    """Decomposes user M&E / resilience-brief prompts into scoped specialist sub-tasks."""

    role = "planner"

    def build_plan(self, run_id: str, request: str, sandbox: object) -> tuple[ExecutionPlan, list[object]]:
        from ..security.sandbox import SandboxedToolRunner

        assert isinstance(sandbox, SandboxedToolRunner)
        mapped, rec = sandbox.invoke(
            run_id=run_id,
            agent_role=self.role,
            tool_name="taxonomy.map_subtasks",
            params={"request": request},
        )
        countries = tuple(mapped["countries"])
        thematic = str(mapped["thematic_focus"])
        priority = str(mapped["priority_area"])

        subtasks = [
            SubTaskSpec(
                task_id="T1_GRANT_ME",
                assigned_agent="grant_me_analyst",
                objective=f"Retrieve governed M&E grant evidence and financing informed for {', '.join(countries)} ({thematic}).",
                countries=countries,
                priority_area=priority,
                thematic_focus=thematic,
                crosscutting_focus=("FCV", "Gender/Inclusion"),
            ),
            SubTaskSpec(
                task_id="T2_GEOSPATIAL",
                assigned_agent="geospatial_hazard_summarizer",
                objective=f"Quantify multi-hazard population exposure and 100M resilience target contribution for {', '.join(countries)}.",
                countries=countries,
                priority_area=priority,
                thematic_focus=thematic,
                crosscutting_focus=(),
            ),
            SubTaskSpec(
                task_id="T3_CROSSCUTTING",
                assigned_agent="crosscutting_fcv_gender_reviewer",
                objective=f"Audit SADDD gender inclusion, disability access, and FCV conflict-sensitivity safeguards across {', '.join(countries)}.",
                countries=countries,
                priority_area=priority,
                thematic_focus=thematic,
                crosscutting_focus=("FCV", "Gender/Inclusion"),
            ),
        ]
        plan = ExecutionPlan(
            request=request,
            countries=list(countries),
            thematic_focus=thematic,
            priority_area=priority,
            subtasks=subtasks,
        )
        return plan, [rec]
