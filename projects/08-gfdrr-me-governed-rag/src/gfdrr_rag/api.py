"""FastAPI service exposing Governed M&E RAG, HITL feedback recording, provenance verification, and audit logs."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import RAGConfig
from .eval.hitl_loop import HITLFeedbackStore
from .pipeline import GovernedRAGPipeline, RAGResponse


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=4)
    clearance_role: str = "wbg_analyst"
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    retrieval_mode: str = "hybrid_rrf_rerank"
    top_k: int = Field(default=5, ge=1, le=10)


class FeedbackRequest(BaseModel):
    reviewer_id: str = Field(..., min_length=2)
    query_id: str = Field(..., min_length=2)
    chunk_id: str = Field(..., min_length=4)
    human_label: str
    auto_judge_grade: int = Field(default=1, ge=0, le=2)
    notes: str = ""


class VerifyBriefRequest(BaseModel):
    brief_markdown: str = Field(..., min_length=20)


def create_app(config: RAGConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="GFDRR Governed M&E Grant Portfolio RAG API",
        version="1.0.0",
        description="World Bank AMS 6.21A classification-gated RAG with 3-checkpoint PII redaction and HMAC provenance.",
    )
    pipeline = GovernedRAGPipeline(config or RAGConfig())
    feedback_store = HITLFeedbackStore()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "corpus_chunks": len(pipeline.chunks),
            "provider_mode": pipeline.config.provider_mode,
        }

    @app.post("/query", response_model=RAGResponse)
    def run_query(req: QueryRequest) -> RAGResponse:
        try:
            return pipeline.query(
                req.query,
                clearance_role=req.clearance_role,
                metadata_filter=req.metadata_filter,
                retrieval_mode=req.retrieval_mode,
                top_k=req.top_k,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/feedback")
    def submit_feedback(req: FeedbackRequest) -> dict[str, object]:
        try:
            fid = feedback_store.record_feedback(
                reviewer_id=req.reviewer_id,
                query_id=req.query_id,
                chunk_id=req.chunk_id,
                human_label=req.human_label,
                auto_judge_grade=req.auto_judge_grade,
                notes=req.notes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "recorded", "feedback_id": fid}

    @app.post("/verify-provenance")
    def verify_provenance(req: VerifyBriefRequest) -> dict[str, object]:
        res = pipeline.verify_brief(req.brief_markdown)
        return res.model_dump()

    @app.get("/audit-log")
    def list_audit_log() -> dict[str, object]:
        return {"events": pipeline.classification_gate.list_audit_events()}

    return app


app = create_app()
