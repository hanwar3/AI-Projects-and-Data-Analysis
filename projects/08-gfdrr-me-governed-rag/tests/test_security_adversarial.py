"""Adversarial security tests for Project 08: role spoofing, PII evasion, and provenance tampering."""

from __future__ import annotations

import pytest

from gfdrr_rag.config import RAGConfig
from gfdrr_rag.governance.classification import ClassificationPolicyGate
from gfdrr_rag.governance.pii_guardrail import MultiLayerPIIGuardrail
from gfdrr_rag.governance.provenance import decode_zero_width, encode_zero_width
from gfdrr_rag.pipeline import GovernedRAGPipeline


def test_invalid_clearance_role_rejected() -> None:
    gate = ClassificationPolicyGate()
    with pytest.raises(ValueError, match="Invalid clearance_role"):
        gate.validate_role("super_admin_bypass")


@pytest.mark.parametrize(
    ("role", "expected_max_tier"),
    [
        ("public_external", "Public"),
        ("wbg_analyst", "Official Use Only"),
        ("me_specialist", "Confidential"),
        ("restricted_audit", "Strictly Confidential"),
    ],
)
def test_role_to_wb_classification_mapping(role: str, expected_max_tier: str) -> None:
    gate = ClassificationPolicyGate()
    assert gate.validate_role(role) == expected_max_tier


@pytest.mark.parametrize(
    ("raw_input", "expected_token"),
    [
        ("Reach field lead at k.mensah_2026@worldbank-sim.org immediately.", "[REDACTED_EMAIL]"),
        ("Call emergency satellite +509-3741-8829 before convoy departure.", "[REDACTED_PHONE]"),
        ("Verified claimant CNIC 42101-7834912-3 on site.", "[REDACTED_ID]"),
        ("Camp committee registrant BEN-ID-SO-773104 signed receipt.", "[REDACTED_ID]"),
        ("Staff registry NID-MR-55201944 inspected cold chain.", "[REDACTED_ID]"),
        ("Delivered kit to House 14-B, Street 7, Satellite Town, Dadu, Sindh.", "[REDACTED_ADDRESS]"),
        ("Depot at Parcelle 28, Rue Turgeau 19, Port-au-Prince.", "[REDACTED_ADDRESS]"),
        ("Supervised by Field Survey Lead Farzana Bibi during heatwave.", "[REDACTED_PERSON]"),
        ("Signed off by Dr. Mariem Mint Vall on clinic inverter.", "[REDACTED_PERSON]"),
    ],
)
def test_adversarial_pii_patterns_masked(raw_input: str, expected_token: str) -> None:
    guard = MultiLayerPIIGuardrail()
    out = guard.redact(raw_input, "checkpoint_a_retrieved_chunks")
    assert expected_token in out.redacted_text
    assert out.redaction_count >= 1
    assert guard.contains_unredacted_pii(out.redacted_text) == []


def test_zero_width_watermark_roundtrip() -> None:
    digest = "a1b2c3d4e5f60718293a4b5c6d7e8f901122334455667788"
    wm = encode_zero_width(digest)
    embedded = f"# Heading{wm}\nBody paragraph."
    assert decode_zero_width(embedded) == digest


def test_unanswerable_queries_abstain_cleanly() -> None:
    pipe = GovernedRAGPipeline(RAGConfig())
    resp = pipe.query(
        "What sovereign geothermal catastrophe bond coupon spread was priced for Iceland in FY2028?",
        clearance_role="me_specialist",
    )
    assert resp.abstained is True
    assert "ABSTAINED" in resp.synthesis.answer_text
    assert resp.synthesis.cited_chunk_ids == []
    assert pipe.provenance_signer.verify_manifest(
        resp.provenance, answer_text=resp.synthesis.answer_text
    ).verified
