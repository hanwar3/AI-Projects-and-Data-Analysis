"""Strict Pydantic v2 schemas for GFDRR M&E documents, sections, chunks, and benchmark queries."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .taxonomy import (
    CROSSCUTTING_AREAS,
    PRIORITY_AREAS,
    THEMATIC_AREAS,
    THEMATIC_TO_PRIORITY,
    WB_CLASSIFICATIONS,
    WB_REGIONS,
)

ClassificationTier = Literal[
    "Public",
    "Official Use Only",
    "Confidential",
    "Strictly Confidential",
]

CrosscuttingTag = Literal["FCV", "Gender/Inclusion"]


class SectionRecord(BaseModel):
    """A section-numbered structural unit within a GFDRR grant or M&E document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: str = Field(..., pattern=r"^S\d+\.\d+$", description="Section identifier e.g. S1.0")
    section_title: str = Field(..., min_length=4)
    page_ref: str = Field(..., pattern=r"^p\.\d+(-\d+)?$")
    text: str = Field(..., min_length=60)
    contains_synthetic_pii: bool = False
    pii_types_present: list[str] = Field(default_factory=list)


class DocumentMetadata(BaseModel):
    """Document-level metadata aligned to GFDRR's 2026-2030 Results Framework."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(..., pattern=r"^DOC-GFDRR-\d{3}$")
    grant_id: str = Field(..., pattern=r"^GFDRR-[A-Z]{2,4}-202\d-\d{3}$")
    title: str = Field(..., min_length=10)
    doc_type: Literal[
        "Grant M&E Review",
        "Results Framework Evaluation",
        "Post-Disaster Rapid Assessment (GRADE)",
        "Disaster Risk Finance Note",
        "Nature-based Solutions Brief",
        "Hydromet & EWS Evaluation",
        "Urban Heat & Resilient Housing Evaluation",
        "FCV Field Monitoring Note",
    ]
    country: str = Field(..., min_length=2)
    region: str
    priority_area: str
    thematic_area: str
    crosscutting_tags: list[CrosscuttingTag] = Field(default_factory=list)
    wb_classification: ClassificationTier
    fiscal_year: int = Field(..., ge=2024, le=2030)
    grant_amount_usd_m: float = Field(..., ge=0.1, le=50.0)
    wb_financing_informed_usd_m: float = Field(..., ge=0.0, le=5000.0)
    resilience_beneficiaries_targeted: int = Field(..., ge=0)

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        if v not in WB_REGIONS:
            raise ValueError(f"Unknown WB region: {v}")
        return v

    @field_validator("thematic_area")
    @classmethod
    def validate_thematic(cls, v: str) -> str:
        if v not in THEMATIC_AREAS:
            raise ValueError(f"Unknown GFDRR thematic area: {v}")
        return v

    @field_validator("priority_area")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in PRIORITY_AREAS:
            raise ValueError(f"Unknown GFDRR priority area: {v}")
        return v

    @field_validator("wb_classification")
    @classmethod
    def validate_classification(cls, v: str) -> str:
        if v not in WB_CLASSIFICATIONS:
            raise ValueError(f"Unknown WB classification: {v}")
        return v

    @field_validator("crosscutting_tags")
    @classmethod
    def validate_crosscutting(cls, v: list[str]) -> list[str]:
        for tag in v:
            if tag not in CROSSCUTTING_AREAS:
                raise ValueError(f"Invalid crosscutting tag: {tag}")
        return v

    @model_validator(mode="after")
    def verify_priority_alignment(self) -> "DocumentMetadata":
        expected = THEMATIC_TO_PRIORITY[self.thematic_area]
        if self.priority_area != expected:
            raise ValueError(
                f"Thematic area '{self.thematic_area}' belongs to '{expected}', got '{self.priority_area}'"
            )
        return self


class CorpusDocument(BaseModel):
    """Complete structured document with executive summary and numbered sections."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: DocumentMetadata
    executive_summary: str = Field(..., min_length=40)
    sections: list[SectionRecord] = Field(..., min_length=3)


class HazardExposureRecord(BaseModel):
    """Country-level geospatial multi-hazard exposure & resilience beneficiary tracking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    country: str
    iso3: str = Field(..., min_length=3, max_length=3)
    region: str
    admin1_hotspot: str
    riverine_coastal_flood_pop_m: float = Field(..., ge=0.0)
    seismic_mmi6_share_pct: float = Field(..., ge=0.0, le=100.0)
    annual_extreme_heat_days_gt35c: int = Field(..., ge=0, le=365)
    fcv_status: bool
    active_gfdrr_grants: int = Field(..., ge=1)
    wb_financing_informed_usd_m: float = Field(..., ge=0.0)
    projected_beneficiaries_m: float = Field(..., ge=0.0)
    dominant_hazards: str


class BenchmarkQuery(BaseModel):
    """Benchmark M&E evaluation query with graded ground-truth chunk relevance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    query: str
    clearance_role: str
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    relevant_chunks: dict[str, int] = Field(
        default_factory=dict,
        description="Mapping of chunk_id -> graded relevance (2=highly relevant, 1=partially relevant)",
    )
    expected_abstain: bool = False
    expected_blocked_classification: bool = False
    expects_pii_redaction: bool = False
    required_facts: list[str] = Field(default_factory=list)
    query_category: Literal[
        "lexical_exact",
        "semantic_paraphrase",
        "multi_grant_synthesis",
        "pii_trap",
        "classification_restricted",
        "unanswerable_abstain",
    ]
