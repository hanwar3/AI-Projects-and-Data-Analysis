"""End-to-end Governed M&E Grant Portfolio RAG Pipeline."""

from __future__ import annotations

import time
from pydantic import BaseModel, ConfigDict, Field

from gfdrr_corpus.loader import load_corpus_documents
from gfdrr_corpus.schemas import CorpusDocument
from gfdrr_corpus.taxonomy import WB_CLASSIFICATION_ORDER

from .config import RAGConfig, RetrievalWeights
from .generation.synthesizer import GroundedSynthesizer, RedactedEvidenceItem, SynthesisResult
from .governance.classification import ClassificationPolicyGate, ClassificationVerdict
from .governance.pii_guardrail import CheckpointRedactionResult, MultiLayerPIIGuardrail
from .governance.provenance import (
    ChunkProvenanceEntry,
    ProvenanceManifest,
    ProvenanceSigner,
    ProvenanceVerificationResult,
)
from .ingestion.chunker import IngestedChunk, ParentDocumentContext, StructureAwareChunker
from .retrieval.hybrid import HybridRetriever


class ChunkRedactionDiff(BaseModel):
    """Before/after redaction audit record for interactive inspection and verification."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    checkpoint_a_count: int
    checkpoint_b_count: int
    pii_types: list[str]
    redacted_text: str
    redacted_snippet: str
    synthetic_demo_before_text: str | None = None


class RAGResponse(BaseModel):
    """Complete auditable output of a Governed RAG query."""

    model_config = ConfigDict(extra="forbid")

    query: str
    clearance_role: str
    retrieval_mode: str
    classification_verdict: ClassificationVerdict
    abstained: bool
    abstention_reason: str | None = None
    max_confidence: float
    retrieved_evidence: list[RedactedEvidenceItem] = Field(default_factory=list)
    redaction_diffs: list[ChunkRedactionDiff] = Field(default_factory=list)
    checkpoint_redaction_counts: dict[str, int] = Field(default_factory=dict)
    total_pii_redactions: int = 0
    zero_pii_leakage_verified: bool = True
    synthesis: SynthesisResult
    provenance: ProvenanceManifest
    latency_ms: float


class GovernedRAGPipeline:
    """Orchestrates WB Classification Gating, Hybrid Retrieval, 3-Layer PII Redaction, and Provenance."""

    def __init__(
        self,
        config: RAGConfig | None = None,
        documents: list[CorpusDocument] | None = None,
    ) -> None:
        self.config = config or RAGConfig()
        raw_docs = documents if documents is not None else load_corpus_documents()

        self.chunker = StructureAwareChunker()
        self.chunks, self.parents = self.chunker.ingest_documents(raw_docs)
        self._chunk_by_id: dict[str, IngestedChunk] = {c.chunk_id: c for c in self.chunks}

        self.classification_gate = ClassificationPolicyGate(db_path=self.config.audit_db_path)
        self.pii_guardrail = MultiLayerPIIGuardrail()
        self.retriever = HybridRetriever(
            self.chunks,
            provider_mode=self.config.provider_mode,
            openai_api_key=self.config.openai_api_key,
        )
        self.synthesizer = GroundedSynthesizer(
            provider_mode=self.config.provider_mode,
            anthropic_api_key=self.config.anthropic_api_key,
            openai_api_key=self.config.openai_api_key,
        )
        self.provenance_signer = ProvenanceSigner(secret_key=self.config.hmac_secret)

    def query(
        self,
        query: str,
        *,
        clearance_role: str = "wbg_analyst",
        metadata_filter: dict[str, str] | None = None,
        retrieval_mode: str | None = None,
        top_k: int | None = None,
        weights: RetrievalWeights | None = None,
        user_id: str = "gfdrr_staff_session",
        include_synthetic_demo_diff: bool = False,
    ) -> RAGResponse:
        t0 = time.perf_counter()
        mode = retrieval_mode or self.config.retrieval_mode
        k = top_k or self.config.top_k
        w = weights or self.config.weights

        # Stage 1: Hard Pre-Retrieval World Bank Classification Gate
        authorized_chunks, verdict = self.classification_gate.filter_chunks(
            self.chunks,
            query=query,
            clearance_role=clearance_role,
            metadata_filter=metadata_filter,
            user_id=user_id,
        )

        if verdict.decision == "BLOCKED_CLASSIFICATION":
            synth = self.synthesizer.synthesize_abstention(
                reason=verdict.reason,
                blocked_classification=True,
                clearance_role=clearance_role,
                max_tier=verdict.max_allowed_tier,
            )
            ckpt_c: CheckpointRedactionResult = self.pii_guardrail.redact(
                synth.answer_text, "checkpoint_c_generated_output"
            )
            synth.answer_text = ckpt_c.redacted_text
            ckpt_counts = {
                "checkpoint_a_retrieved_chunks": 0,
                "checkpoint_b_citation_snippets": 0,
                "checkpoint_c_generated_output": ckpt_c.redaction_count,
            }
            manifest = self.provenance_signer.sign_manifest(
                query=query,
                answer_text=synth.answer_text,
                model_version=self.config.model_version,
                clearance_role=clearance_role,
                max_classification_used="NONE (BLOCKED)",
                abstained=True,
                total_pii_redactions=ckpt_c.redaction_count,
                redactions_by_checkpoint=ckpt_counts,
                cited_chunks=[],
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return RAGResponse(
                query=query,
                clearance_role=clearance_role,
                retrieval_mode=mode,
                classification_verdict=verdict,
                abstained=True,
                abstention_reason=verdict.reason,
                max_confidence=0.0,
                retrieved_evidence=[],
                redaction_diffs=[],
                checkpoint_redaction_counts=ckpt_counts,
                total_pii_redactions=ckpt_c.redaction_count,
                zero_pii_leakage_verified=not bool(
                    self.pii_guardrail.contains_unredacted_pii(synth.answer_text)
                ),
                synthesis=synth,
                provenance=manifest,
                latency_ms=round(elapsed_ms, 3),
            )

        # Stage 2: Multi-Stage Hybrid Retrieval + Abstention Gate
        bundle = self.retriever.retrieve(
            query=query,
            authorized_chunks=authorized_chunks,
            retrieval_mode=mode,
            metadata_filter=metadata_filter,
            top_k=k,
            abstention_threshold=self.config.abstention_threshold,
            weights=w,
        )

        # Stage 3: Multi-Layered Dynamic PII Redaction — Checkpoints A (chunk) & B (citation snippet)
        redacted_evidence: list[RedactedEvidenceItem] = []
        redaction_diffs: list[ChunkRedactionDiff] = []
        ckpt_a_total = 0
        ckpt_b_total = 0

        for sc in bundle.results:
            c = sc.chunk
            ckpt_a = self.pii_guardrail.redact(c.raw_text, "checkpoint_a_retrieved_chunks")
            raw_snippet = f"{c.title} ({c.grant_id}, {c.page_ref}): {c.raw_text[:220]}..."
            ckpt_b = self.pii_guardrail.redact(raw_snippet, "checkpoint_b_citation_snippets")

            ckpt_a_total += ckpt_a.redaction_count
            ckpt_b_total += ckpt_b.redaction_count

            redacted_evidence.append(
                RedactedEvidenceItem(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    grant_id=c.grant_id,
                    title=c.title,
                    country=c.country,
                    region=c.region,
                    thematic_area=c.thematic_area,
                    wb_classification=c.wb_classification,
                    section_id=c.section_id,
                    section_title=c.section_title,
                    page_ref=c.page_ref,
                    redacted_text=ckpt_a.redacted_text,
                    redacted_snippet=ckpt_b.redacted_text,
                    bm25_score=sc.bm25_score,
                    dense_score=sc.dense_score,
                    rrf_score=sc.rrf_score,
                    rerank_score=sc.rerank_score,
                    doc_sha256=c.doc_sha256,
                    chunk_sha256=c.chunk_sha256,
                )
            )
            if ckpt_a.redaction_count > 0 or ckpt_b.redaction_count > 0:
                redaction_diffs.append(
                    ChunkRedactionDiff(
                        chunk_id=c.chunk_id,
                        checkpoint_a_count=ckpt_a.redaction_count,
                        checkpoint_b_count=ckpt_b.redaction_count,
                        pii_types=sorted(ckpt_a.counts_by_type.keys()),
                        redacted_text=ckpt_a.redacted_text,
                        redacted_snippet=ckpt_b.redacted_text,
                        # Only populated for synthetic corpus educational trace export when explicitly requested
                        synthetic_demo_before_text=c.raw_text if include_synthetic_demo_diff else None,
                    )
                )

        # Stage 4: Synthesis + Checkpoint C (final output PII redaction)
        if bundle.abstained:
            synth = self.synthesizer.synthesize_abstention(
                reason=bundle.abstention_reason or "Confidence below abstention threshold.",
                blocked_classification=False,
                clearance_role=clearance_role,
                max_tier=verdict.max_allowed_tier,
            )
        else:
            synth = self.synthesizer.synthesize(query=query, evidence=redacted_evidence)

        ckpt_c = self.pii_guardrail.redact(synth.answer_text, "checkpoint_c_generated_output")
        if ckpt_c.redaction_count > 0:
            synth = self.synthesizer.verify_citations(ckpt_c.redacted_text, redacted_evidence)
        else:
            synth.answer_text = ckpt_c.redacted_text

        # Verify zero PII leakage across all externally visible strings
        leaked_any = bool(self.pii_guardrail.contains_unredacted_pii(synth.answer_text))
        for ev in redacted_evidence:
            if self.pii_guardrail.contains_unredacted_pii(ev.redacted_text):
                leaked_any = True
            if self.pii_guardrail.contains_unredacted_pii(ev.redacted_snippet):
                leaked_any = True

        # Stage 5: Cryptographic Provenance Manifest
        ev_by_id = {ev.chunk_id: ev for ev in redacted_evidence}
        cited_entries = [
            ChunkProvenanceEntry(
                chunk_id=cid,
                doc_id=ev_by_id[cid].doc_id,
                grant_id=ev_by_id[cid].grant_id,
                section_id=ev_by_id[cid].section_id,
                page_ref=ev_by_id[cid].page_ref,
                wb_classification=ev_by_id[cid].wb_classification,
                doc_sha256=ev_by_id[cid].doc_sha256,
                chunk_sha256=ev_by_id[cid].chunk_sha256,
            )
            for cid in synth.cited_chunk_ids
            if cid in ev_by_id
        ]

        if cited_entries:
            highest_used = max(
                (e.wb_classification for e in cited_entries),
                key=lambda t: WB_CLASSIFICATION_ORDER[t],
            )
        elif bundle.abstained:
            highest_used = "NONE (ABSTAINED)"
        else:
            highest_used = "Public"

        ckpt_counts = {
            "checkpoint_a_retrieved_chunks": ckpt_a_total,
            "checkpoint_b_citation_snippets": ckpt_b_total,
            "checkpoint_c_generated_output": ckpt_c.redaction_count,
        }
        total_redactions = sum(ckpt_counts.values())

        manifest = self.provenance_signer.sign_manifest(
            query=query,
            answer_text=synth.answer_text,
            model_version=self.config.model_version,
            clearance_role=clearance_role,
            max_classification_used=highest_used,
            abstained=bundle.abstained,
            total_pii_redactions=total_redactions,
            redactions_by_checkpoint=ckpt_counts,
            cited_chunks=cited_entries,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return RAGResponse(
            query=query,
            clearance_role=clearance_role,
            retrieval_mode=mode,
            classification_verdict=verdict,
            abstained=bundle.abstained,
            abstention_reason=bundle.abstention_reason,
            max_confidence=bundle.max_confidence,
            retrieved_evidence=redacted_evidence,
            redaction_diffs=redaction_diffs,
            checkpoint_redaction_counts=ckpt_counts,
            total_pii_redactions=total_redactions,
            zero_pii_leakage_verified=not leaked_any,
            synthesis=synth,
            provenance=manifest,
            latency_ms=round(elapsed_ms, 3),
        )

    def export_brief(self, response: RAGResponse, title: str = "GFDRR Governed M&E Brief") -> str:
        return self.provenance_signer.export_watermarked_brief(
            title=title,
            query=response.query,
            answer_text=response.synthesis.answer_text,
            manifest=response.provenance,
        )

    def verify_brief(self, brief_markdown: str) -> ProvenanceVerificationResult:
        return self.provenance_signer.verify_exported_brief(brief_markdown)
