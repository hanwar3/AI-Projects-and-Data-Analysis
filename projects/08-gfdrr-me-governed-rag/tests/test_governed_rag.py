"""Comprehensive security, governance, retrieval, provenance, HITL, and API tests for Project 08."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from fastapi.testclient import TestClient
import pytest

from gfdrr_corpus.loader import load_benchmark_queries, load_corpus_documents
from gfdrr_rag.api import create_app
from gfdrr_rag.cli import main as cli_main
from gfdrr_rag.config import RAGConfig
from gfdrr_rag.eval.ablation import run_four_way_ablation
from gfdrr_rag.eval.hitl_loop import HITLFeedbackStore, run_hitl_feedback_experiment
from gfdrr_rag.governance.classification import ClassificationPolicyGate
from gfdrr_rag.governance.pii_guardrail import MultiLayerPIIGuardrail
from gfdrr_rag.governance.provenance import ProvenanceSigner, strip_zero_width
from gfdrr_rag.ingestion.chunker import StructureAwareChunker
from gfdrr_rag.pipeline import GovernedRAGPipeline


@pytest.fixture(scope="module")
def pipeline() -> GovernedRAGPipeline:
    return GovernedRAGPipeline(RAGConfig())


def test_structure_aware_chunking_and_parent_links() -> None:
    docs = load_corpus_documents()
    chunker = StructureAwareChunker()
    chunks, parents = chunker.ingest_documents(docs)

    assert len(docs) == 22
    assert len(parents) == 22
    assert len(chunks) == 88

    first_doc_children = [c for c in chunks if c.doc_id == "DOC-GFDRR-001"]
    assert [c.section_id for c in first_doc_children] == ["S1.0", "S2.0", "S3.0", "S4.0"]
    assert first_doc_children[0].prev_chunk_id is None
    assert first_doc_children[0].next_chunk_id == "DOC-GFDRR-001::S2.0"
    assert first_doc_children[3].prev_chunk_id == "DOC-GFDRR-001::S3.0"
    assert first_doc_children[3].next_chunk_id is None
    assert len(first_doc_children[0].doc_sha256) == 64
    assert len(first_doc_children[0].chunk_sha256) == 64


def test_classification_gate_hierarchy_and_immutable_sqlite_audit(pipeline: GovernedRAGPipeline) -> None:
    gate = ClassificationPolicyGate()

    # public_external can ONLY see Public documents (9 docs * 4 sections = 36 chunks)
    pub_chunks, verdict_pub = gate.filter_chunks(
        pipeline.chunks,
        query="Summarize coastal embankment resilience in Bangladesh.",
        clearance_role="public_external",
    )
    assert len(pub_chunks) == 36
    assert all(c.wb_classification == "Public" for c in pub_chunks)
    assert verdict_pub.decision == "ALLOWED"

    # public_external requesting Confidential Belize Cat DDO must be HARD BLOCKED and audited
    blocked_chunks, verdict_blk = gate.filter_chunks(
        pipeline.chunks,
        query="Disclose Belize Cat DDO triggers in GFDRR-BZ-2026-005",
        clearance_role="public_external",
        metadata_filter={"country": "Belize"},
        user_id="ext_visitor_01",
    )
    assert blocked_chunks == []
    assert verdict_blk.decision == "BLOCKED_CLASSIFICATION"
    assert "DOC-GFDRR-005" in verdict_blk.targeted_blocked_doc_ids
    assert verdict_blk.audit_event_id is not None

    # me_specialist can access Confidential (Pakistan 002) but NOT Strictly Confidential (Haiti 007)
    ht_chunks, verdict_ht = gate.filter_chunks(
        pipeline.chunks,
        query="Provide Strictly Confidential Haiti EOC VSAT depot details in GFDRR-HT-2026-007",
        clearance_role="me_specialist",
        metadata_filter={"country": "Haiti"},
    )
    assert ht_chunks == []
    assert verdict_ht.decision == "BLOCKED_CLASSIFICATION"

    # Verify SQLite audit log immutability triggers block UPDATE and DELETE
    events = gate.list_audit_events()
    assert len(events) >= 2
    with pytest.raises(sqlite3.IntegrityError, match="Immutable audit table"):
        gate._conn.execute("UPDATE classification_audit_log SET decision = 'ALLOWED'")
    with pytest.raises(sqlite3.IntegrityError, match="Immutable audit table"):
        gate._conn.execute("DELETE FROM classification_audit_log")


def test_three_checkpoint_pii_redaction_zero_leakage(pipeline: GovernedRAGPipeline) -> None:
    guard = MultiLayerPIIGuardrail()
    adversarial_sample = (
        "Field spot-check conducted by Senior Verification Engineer Tariq Mahmood Baloch "
        "(email t.baloch.field@gfdrr-sim.org, tel +92-300-5584921) for household "
        "CNIC 42101-7834912-3 at House 14-B, Street 7, Satellite Town, Dadu, Sindh "
        "under ticket BEN-ID-PK-2026-44109."
    )
    res = guard.redact(adversarial_sample, "checkpoint_a_retrieved_chunks")
    assert "[REDACTED_PERSON]" in res.redacted_text
    assert "[REDACTED_EMAIL]" in res.redacted_text
    assert "[REDACTED_PHONE]" in res.redacted_text
    assert "[REDACTED_ID]" in res.redacted_text
    assert "[REDACTED_ADDRESS]" in res.redacted_text
    assert "Tariq Mahmood Baloch" not in res.redacted_text
    assert "42101-7834912-3" not in res.redacted_text
    assert "t.baloch.field" not in res.redacted_text
    assert "+92-300-5584921" not in res.redacted_text
    assert guard.contains_unredacted_pii(res.redacted_text) == []

    # End-to-end check across ALL 6 PII trap benchmark queries
    pii_queries = [q for q in load_benchmark_queries() if q.expects_pii_redaction]
    assert len(pii_queries) == 6
    for pq in pii_queries:
        resp = pipeline.query(
            pq.query,
            clearance_role=pq.clearance_role,
            metadata_filter=pq.metadata_filter,
        )
        assert resp.zero_pii_leakage_verified is True
        assert resp.total_pii_redactions >= 2
        assert resp.checkpoint_redaction_counts["checkpoint_a_retrieved_chunks"] >= 1
        assert resp.checkpoint_redaction_counts["checkpoint_b_citation_snippets"] >= 0
        assert guard.contains_unredacted_pii(resp.synthesis.answer_text) == []
        for ev in resp.retrieved_evidence:
            assert guard.contains_unredacted_pii(ev.redacted_text) == []
            assert guard.contains_unredacted_pii(ev.redacted_snippet) == []


def test_checkpoint_c_catches_injected_output_pii() -> None:
    guard = MultiLayerPIIGuardrail()
    simulated_llm_leak = (
        "Summary: Mortar strength passed 4.8 MPa; contact Dr. Salman Raza Khan at "
        "salman.khan@leak-test.org or +1-202-555-0194 regarding SSN 123-45-6789."
    )
    ckpt_c = guard.redact(simulated_llm_leak, "checkpoint_c_generated_output")
    assert ckpt_c.redaction_count == 4
    assert guard.contains_unredacted_pii(ckpt_c.redacted_text) == []


def test_provenance_signing_watermark_and_tamper_detection(
    pipeline: GovernedRAGPipeline, tmp_path: Path
) -> None:
    resp = pipeline.query(
        "What World Bank financing amount and beneficiary target did grant GFDRR-PK-2026-001 inform in Sindh?",
        clearance_role="me_specialist",
        metadata_filter={"country": "Pakistan"},
    )
    assert not resp.abstained
    brief_md = pipeline.export_brief(resp, title="Sindh NbS Verification Brief")
    brief_file = tmp_path / "sindh_brief.md"
    brief_file.write_text(brief_md, encoding="utf-8")

    # Intact CLI verification returns exit code 0
    assert cli_main(["verify-provenance", str(brief_file)]) == 0
    check_ok = pipeline.verify_brief(brief_md)
    assert check_ok.verified is True
    assert check_ok.hmac_valid and check_ok.body_hash_valid and check_ok.watermark_valid

    # Tamper Attack 1: Alter a single digit in the brief body ($410.0M -> $910.0M)
    tampered_body = brief_md.replace("410.0M", "910.0M")
    tampered_file = tmp_path / "tampered_body.md"
    tampered_file.write_text(tampered_body, encoding="utf-8")
    assert cli_main(["verify-provenance", str(tampered_file)]) == 1
    check_bad_body = pipeline.verify_brief(tampered_body)
    assert check_bad_body.verified is False
    assert check_bad_body.body_hash_valid is False
    assert check_bad_body.status == "TAMPER_DETECTED"

    # Tamper Attack 2: Strip invisible zero-width cryptographic watermark from heading
    stripped_wm = strip_zero_width(brief_md)
    check_bad_wm = pipeline.verify_brief(stripped_wm)
    assert check_bad_wm.verified is False
    assert check_bad_wm.watermark_valid is False
    assert check_bad_wm.status == "TAMPER_DETECTED"

    # Tamper Attack 3: Forge HMAC signature with wrong key
    attacker_signer = ProvenanceSigner(secret_key="wrong-attacker-secret-key")
    assert attacker_signer.verify_exported_brief(brief_md).verified is False


def test_four_way_ablation_and_abstention_gate(pipeline: GovernedRAGPipeline) -> None:
    ablation = run_four_way_ablation(pipeline)
    by_mode = {row["retrieval_mode"]: row for row in ablation}

    # Every mode must enforce 100% WB classification compliance and 100% PII redaction recall
    for row in ablation:
        assert row["classification_compliance_rate"] == 1.0
        assert row["pii_redaction_recall"] == 1.0

    # Hybrid RRF + Reranker + Metadata Filter (Config D) must outperform single-modality baselines on nDCG@5
    assert by_mode["hybrid_rrf_rerank"]["ndcg_at_5"] > by_mode["dense_only"]["ndcg_at_5"]
    assert by_mode["hybrid_rrf_rerank"]["ndcg_at_5"] > by_mode["bm25_only"]["ndcg_at_5"]
    assert by_mode["hybrid_rrf_rerank"]["abstention_f1"] >= 0.90
    assert by_mode["hybrid_rrf_rerank"]["citation_precision"] == 1.0


def test_hitl_feedback_loop_and_fastapi_endpoints(pipeline: GovernedRAGPipeline) -> None:
    store = HITLFeedbackStore()
    exp = run_hitl_feedback_experiment(pipeline=pipeline, store=store)

    assert exp["n_chunk_judgments"] > 100
    assert exp["tuned_ndcg_at_5"] > exp["untuned_ndcg_at_5"]
    assert exp["delta_ndcg_at_5"] > 0.0
    assert exp["ndcg_ci95"][0] > 0.0  # Statistically significant positive uplift at 95% CI
    assert 0.35 <= exp["cohen_kappa_quadratic"] <= 0.98

    # FastAPI endpoints verification
    client = TestClient(create_app())
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["corpus_chunks"] == 88

    r_query = client.post(
        "/query",
        json={
            "query": "What spatial deduplication rules prevent double counting toward GFDRR's 100 million resilience target?",
            "clearance_role": "public_external",
        },
    )
    assert r_query.status_code == 200
    payload = r_query.json()
    assert payload["abstained"] is False
    assert payload["zero_pii_leakage_verified"] is True

    r_fb = client.post(
        "/feedback",
        json={
            "reviewer_id": "senior_me_01",
            "query_id": "Q04",
            "chunk_id": "DOC-GFDRR-017::S3.0",
            "human_label": "relevant",
            "auto_judge_grade": 2,
            "notes": "Exact methodology section.",
        },
    )
    assert r_fb.status_code == 200
    assert r_fb.json()["status"] == "recorded"
