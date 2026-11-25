"""Scoped Specialist Agents: Grant M&E Analyst, Geospatial Hazard Summarizer, Crosscutting FCV/Gender Reviewer."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ..security.sandbox import SandboxedToolRunner
from .planner import ExecutionPlan


def compute_exposure_stats_tool(countries: list[str], table_path: Path) -> dict[str, Any]:
    """Read-only sandboxed geospatial analytics tool over country_hazard_exposure.csv."""
    rows: list[dict[str, Any]] = []
    target_lower = {c.lower() for c in countries}
    with Path(table_path).open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            if raw["country"].lower() in target_lower:
                rows.append(
                    {
                        "country": raw["country"],
                        "iso3": raw["iso3"],
                        "region": raw["region"],
                        "admin1_hotspot": raw["admin1_hotspot"],
                        "riverine_coastal_flood_pop_m": float(raw["riverine_coastal_flood_pop_m"]),
                        "seismic_mmi6_share_pct": float(raw["seismic_mmi6_share_pct"]),
                        "annual_extreme_heat_days_gt35c": int(raw["annual_extreme_heat_days_gt35c"]),
                        "fcv_status": raw["fcv_status"].lower() == "true",
                        "wb_financing_informed_usd_m": float(raw["wb_financing_informed_usd_m"]),
                        "projected_beneficiaries_m": float(raw["projected_beneficiaries_m"]),
                        "share_of_100m_target_pct": round(float(raw["projected_beneficiaries_m"]), 2),
                        "dominant_hazards": raw["dominant_hazards"],
                    }
                )

    total_flood_m = round(sum(r["riverine_coastal_flood_pop_m"] for r in rows), 2)
    total_ben_m = round(sum(r["projected_beneficiaries_m"] for r in rows), 2)
    total_fin_m = round(sum(r["wb_financing_informed_usd_m"] for r in rows), 1)
    return {
        "matched_countries": [r["country"] for r in rows],
        "records": rows,
        "aggregate_flood_exposed_pop_m": total_flood_m,
        "aggregate_wb_financing_informed_usd_m": total_fin_m,
        "aggregate_projected_beneficiaries_m": total_ben_m,
        "share_of_gfdrr_100m_target_pct": total_ben_m,
    }


def audit_fcv_gender_policy_tool(
    grant_bullets: list[str],
    geo_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic policy auditor checking RF-2030-IND-08 SADDD Gender & FCV Nexus criteria."""
    joined = " ".join(grant_bullets).lower()
    has_saddd = any(k in joined for k in ("saddd", "sex, age", "disaggregated"))
    has_womens_action = any(
        k in joined
        for k in ("women", "female", "maternal", "girls", "lactation", "joint spousal")
    )
    has_disability = any(k in joined for k in ("disability", "wheelchair", "assistive"))
    fcv_countries = [r["country"] for r in geo_records if r.get("fcv_status")]
    has_fcv_safeguard = any(
        k in joined
        for k in ("fcv", "conflict", "idp", "refugee", "coarsened", "escort", "do-no-harm", "relay")
    )

    gaps: list[str] = []
    if not has_saddd:
        gaps.append(
            "Explicit SADDD (sex, age, and disability-disaggregated data) indicator citation not yet surfaced in primary summary; require RF-2030-IND-08 disaggregation check."
        )
    if not has_disability:
        gaps.append(
            "Universal disability access / assistive evacuation provision not documented across all cited sub-grants; verify 1:12 ramp & power-backup compliance."
        )
    if fcv_countries and not has_fcv_safeguard:
        gaps.append(
            f"FCV-classified exposure present in {', '.join(fcv_countries)} without explicit conflict-sensitivity / spatial coarsening safeguard note in synthesis."
        )

    return {
        "saddd_present": has_saddd,
        "gender_targeted_actions_present": has_womens_action,
        "disability_inclusion_present": has_disability,
        "fcv_countries_flagged": fcv_countries,
        "fcv_safeguard_verified": has_fcv_safeguard or not fcv_countries,
        "identified_gaps": gaps,
    }


class GrantMEAnalystAgent:
    """Queries Project 08's Governed RAG under least-privilege Official Use Only scope."""

    role = "grant_me_analyst"

    def run(
        self,
        run_id: str,
        plan: ExecutionPlan,
        sandbox: SandboxedToolRunner,
        clearance_role: str = "wbg_analyst",
        include_unverified_extrapolation: bool = False,
    ) -> tuple[dict[str, Any], list[Any]]:
        tool_records: list[Any] = []
        evidence_chunks: list[dict[str, Any]] = []
        manifests: list[dict[str, Any]] = []
        synthesis_lines: list[str] = []

        for country in plan.countries:
            q_str = (
                f"Summarize grant intervention, World Bank financing informed, and M&E 2026-2030 "
                f"beneficiary outcomes in {country} related to {plan.thematic_focus}."
            )
            rag_resp, rec = sandbox.invoke(
                run_id=run_id,
                agent_role=self.role,
                tool_name="rag.query",
                params={
                    "query": q_str,
                    "clearance_role": clearance_role,
                    "metadata_filter": {"country": country},
                },
            )
            tool_records.append(rec)
            if not rag_resp["abstained"]:
                for line in rag_resp["answer_text"].splitlines():
                    if line.startswith("- "):
                        synthesis_lines.append(line)
                evidence_chunks.extend(rag_resp["retrieved_chunks"])
                manifests.append(rag_resp["provenance"])

        # Fallback thematic query if country-filtered Official Use Only returned empty
        if not synthesis_lines:
            rag_resp, rec = sandbox.invoke(
                run_id=run_id,
                agent_role=self.role,
                tool_name="rag.query",
                params={
                    "query": plan.request,
                    "clearance_role": clearance_role,
                    "metadata_filter": {},
                },
            )
            tool_records.append(rec)
            for line in rag_resp["answer_text"].splitlines():
                if line.startswith("- "):
                    synthesis_lines.append(line)
            evidence_chunks.extend(rag_resp["retrieved_chunks"])
            manifests.append(rag_resp["provenance"])

        if include_unverified_extrapolation:
            # Simulates typical unarbitrated multi-agent draft extrapolation caught by Critic
            synthesis_lines.append(
                "- **Extrapolated Projection (Unverified):** Projected sovereign insurance premium "
                "savings will reduce national debt-service ratios by 14.5% by FY2029 [UNVERIFIED_SYNTHESIS]."
            )

        return (
            {
                "bullets": synthesis_lines,
                "evidence_chunks": evidence_chunks,
                "provenance_manifests": manifests,
            },
            tool_records,
        )


class GeospatialHazardSummarizerAgent:
    """Computes read-only country multi-hazard exposure metrics from structured tables."""

    role = "geospatial_hazard_summarizer"

    def run(
        self,
        run_id: str,
        plan: ExecutionPlan,
        sandbox: SandboxedToolRunner,
        hazard_csv_path: Path,
    ) -> tuple[dict[str, Any], list[Any]]:
        geo_out, rec = sandbox.invoke(
            run_id=run_id,
            agent_role=self.role,
            tool_name="geo.compute_exposure_stats",
            params={"countries": plan.countries, "table_path": hazard_csv_path},
        )
        summary_lines = [
            f"- **{r['country']} ({r['admin1_hotspot']}):** {r['riverine_coastal_flood_pop_m']:.1f}M people "
            f"in riverine/coastal flood zones, {r['seismic_mmi6_share_pct']:.1f}% area exposed to MMI VI+ shaking, "
            f"{r['annual_extreme_heat_days_gt35c']} extreme heat days (>35°C/yr); informs ${r['wb_financing_informed_usd_m']:.0f}M "
            f"WB financing and {r['projected_beneficiaries_m']:.2f}M beneficiaries ({r['share_of_100m_target_pct']:.2f}% of 100M 2030 goal)."
            for r in geo_out["records"]
        ]
        geo_out["summary_bullets"] = summary_lines
        return geo_out, [rec]


class CrosscuttingInclusionFCVReviewerAgent:
    """Audits draft against Inclusive DRM & Gender Equality (SADDD) and Disaster-FCV Nexus criteria."""

    role = "crosscutting_fcv_gender_reviewer"

    def run(
        self,
        run_id: str,
        plan: ExecutionPlan,
        grant_output: dict[str, Any],
        geo_output: dict[str, Any],
        sandbox: SandboxedToolRunner,
        clearance_role: str = "wbg_analyst",
    ) -> tuple[dict[str, Any], list[Any]]:
        tool_records: list[Any] = []
        extra_bullets = list(grant_output["bullets"])

        for country in plan.countries[:2]:
            rag_resp, rec = sandbox.invoke(
                run_id=run_id,
                agent_role=self.role,
                tool_name="rag.query",
                params={
                    "query": f"What gender SADDD inclusion, women's leadership, disability, and FCV safeguards apply in {country}?",
                    "clearance_role": clearance_role,
                    "metadata_filter": {"country": country},
                },
            )
            tool_records.append(rec)
            for line in rag_resp["answer_text"].splitlines():
                if line.startswith("- ") and line not in extra_bullets:
                    extra_bullets.append(line)

        audit_res, rec_pol = sandbox.invoke(
            run_id=run_id,
            agent_role=self.role,
            tool_name="policy.audit_fcv_gender",
            params={
                "grant_bullets": extra_bullets,
                "geo_records": geo_output["records"],
            },
        )
        tool_records.append(rec_pol)
        audit_res["crosscutting_bullets"] = [
            b for b in extra_bullets if "S4.0" in b or "women" in b.lower() or "saddd" in b.lower() or "disability" in b.lower() or "coarsened" in b.lower()
        ]
        return audit_res, tool_records
