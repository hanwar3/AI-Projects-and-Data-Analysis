"""Configuration and dual-mode execution settings for the Governed M&E RAG platform."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RetrievalWeights(BaseModel):
    """Tunable parameters for Hybrid RRF + Cross-Feature Reranker."""

    model_config = ConfigDict(extra="forbid")

    w_bm25: float = Field(default=1.0, ge=0.0, le=5.0)
    w_dense: float = Field(default=1.0, ge=0.0, le=5.0)
    rrf_k: int = Field(default=60, ge=1, le=200)
    rerank_rrf_w: float = Field(default=0.45, ge=0.0, le=2.0)
    rerank_lexical_w: float = Field(default=0.20, ge=0.0, le=2.0)
    rerank_semantic_w: float = Field(default=0.20, ge=0.0, le=2.0)
    rerank_section_w: float = Field(default=0.08, ge=0.0, le=2.0)
    rerank_metadata_w: float = Field(default=0.07, ge=0.0, le=2.0)


class RAGConfig(BaseModel):
    """Runtime configuration supporting both deterministic_local and live API modes."""

    model_config = ConfigDict(extra="forbid")

    provider_mode: Literal["deterministic_local", "live"] = Field(
        default_factory=lambda: (
            "live"
            if os.getenv("GFDRR_PROVIDER_MODE", "deterministic_local") == "live"
            else "deterministic_local"
        )
    )
    retrieval_mode: Literal[
        "dense_only",
        "bm25_only",
        "hybrid_rrf",
        "hybrid_rrf_rerank",
    ] = "hybrid_rrf_rerank"
    top_k: int = Field(default=5, ge=1, le=20)
    abstention_threshold: float = Field(default=0.21, ge=0.0, le=1.0)
    weights: RetrievalWeights = Field(default_factory=RetrievalWeights)
    hmac_secret: str = Field(
        default_factory=lambda: os.getenv(
            "GFDRR_PROVENANCE_HMAC_KEY",
            "wbg-gfdrr-me-audit-dev-hmac-key-2026-v1",
        )
    )
    model_version: str = "gfdrr-governed-rag-v1.2-local"
    audit_db_path: Path | None = None
    openai_api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    anthropic_api_key: str | None = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
