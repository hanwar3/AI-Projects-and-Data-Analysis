"""Arbitration & Critic Reviewer verifying provenance hashes, claim grounding, and rubric completeness."""

from __future__ import annotations

import re
from typing import Any

from ..security.sandbox import SandboxedToolRunner

_CITE_PATTERN = re.compile(r"\[(DOC-GFDRR-\d{3})\s+§(S\d+\.\d+),\s*(p\.\d+(?:-\d+)?)\]")


def verify_claims_tool(
    grant_bullets: list[str],
    evidence_chunks: list[dict[str, Any]],
    geo_records: list[dict[str, Any]],
    crosscutting_audit: dict[str, Any],
) -> dict[str, Any]:
    """Verify every draft claim against authorized retrieved chunk IDs and score 5-part M&E rubric."""
    valid_keys = {
        (c["doc_id"], c["section_id"], c["page_ref"]) for c in evidence_chunks
    }
    verified_bullets: list[str] = []
    rejected_bullets: list[str] = []

    for bullet in grant_bullets:
        matches = list(_CITE_PATTERN.finditer(bullet))
        if not matches or "UNVERIFIED" in bullet:
            rejected_bullets.append(bullet)
            continue
        if all((m.group(1), m.group(2), m.group(3)) in valid_keys for m in matches):
            verified_bullets.append(bullet)
        else:
            rejected_bullets.append(bullet)

    joined_text = " ".join(verified_bullets).lower()
    rubric_dimensions = {
        "risk_baseline_and_context": bool(verified_bullets),
        "wb_financing_leverage_quantified": ("$" in joined_text or "informed" in joined_text),
        "me_2026_2030_indicators_cited": any(
            k in joined_text for k in ("rf-2030", "beneficiar", "target", "%")
        ),
        "geospatial_exposure_integrated": len(geo_records) > 0,
        "crosscutting_fcv_gender_audited": bool(
            crosscutting_audit.get("gender_targeted_actions_present")
            or crosscutting_audit.get("identified_gaps")
        ),
    }
    rubric_score = sum(1 for ok in rubric_dimensions.values() if ok) / len(rubric_dimensions)

    return {
        "verified_bullets": verified_bullets,
        "rejected_unverified_bullets": rejected_bullets,
        "unsupported_claims_caught": len(rejected_bullets),
        "rubric_dimensions": rubric_dimensions,
        "rubric_coverage_pct": round(rubric_score * 100.0, 1),
    }


class CriticArbitratorAgent:
    """Validates cryptographic provenance and strips any unsupported claim before HIC sign-off."""

    role = "critic_arbitrator"

    def run(
        self,
        run_id: str,
        grant_output: dict[str, Any],
        geo_output: dict[str, Any],
        crosscutting_output: dict[str, Any],
        sandbox: SandboxedToolRunner,
    ) -> tuple[dict[str, Any], list[Any]]:
        tool_records: list[Any] = []
        prov_statuses: list[dict[str, Any]] = []

        for manifest in grant_output.get("provenance_manifests", []):
            v_res, rec = sandbox.invoke(
                run_id=run_id,
                agent_role=self.role,
                tool_name="provenance.verify_manifest",
                params={"manifest_dict": manifest},
            )
            tool_records.append(rec)
            prov_statuses.append(v_res)

        claim_check, rec_claims = sandbox.invoke(
            run_id=run_id,
            agent_role=self.role,
            tool_name="rubric.verify_claims",
            params={
                "grant_bullets": grant_output["bullets"],
                "evidence_chunks": grant_output["evidence_chunks"],
                "geo_records": geo_output["records"],
                "crosscutting_audit": crosscutting_output,
            },
        )
        tool_records.append(rec_claims)

        all_prov_valid = all(p.get("verified", False) for p in prov_statuses) if prov_statuses else True
        return (
            {
                "all_provenance_verified": all_prov_valid,
                "provenance_checks": prov_statuses,
                **claim_check,
            },
            tool_records,
        )
