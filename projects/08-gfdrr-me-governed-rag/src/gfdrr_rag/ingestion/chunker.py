"""Structure-aware section-preserving chunker with parent-child linking and SHA-256 digests."""

from __future__ import annotations

import hashlib
from pydantic import BaseModel, ConfigDict, Field

from gfdrr_corpus.schemas import ClassificationTier, CorpusDocument, CrosscuttingTag


class ParentDocumentContext(BaseModel):
    """Parent document summary and structural sibling registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    grant_id: str
    title: str
    doc_type: str
    executive_summary: str
    wb_classification: ClassificationTier
    doc_sha256: str
    child_chunk_ids: tuple[str, ...]


class IngestedChunk(BaseModel):
    """Section-preserving child chunk linked to parent grant context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(..., pattern=r"^DOC-GFDRR-\d{3}::S\d+\.\d+$")
    doc_id: str
    grant_id: str
    title: str
    doc_type: str
    country: str
    region: str
    priority_area: str
    thematic_area: str
    crosscutting_tags: tuple[CrosscuttingTag, ...]
    wb_classification: ClassificationTier
    fiscal_year: int
    section_id: str
    section_title: str
    page_ref: str
    raw_text: str
    indexed_text: str
    parent_summary: str
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    doc_sha256: str
    chunk_sha256: str


class StructureAwareChunker:
    """Splits GFDRR documents along numbered section boundaries while linking parent context."""

    def ingest_documents(
        self, documents: list[CorpusDocument]
    ) -> tuple[list[IngestedChunk], dict[str, ParentDocumentContext]]:
        chunks: list[IngestedChunk] = []
        parents: dict[str, ParentDocumentContext] = {}

        for doc in documents:
            canonical_doc_payload = doc.model_dump_json()
            doc_sha256 = hashlib.sha256(canonical_doc_payload.encode("utf-8")).hexdigest()
            child_ids = tuple(f"{doc.metadata.doc_id}::{sec.section_id}" for sec in doc.sections)

            parents[doc.metadata.doc_id] = ParentDocumentContext(
                doc_id=doc.metadata.doc_id,
                grant_id=doc.metadata.grant_id,
                title=doc.metadata.title,
                doc_type=doc.metadata.doc_type,
                executive_summary=doc.executive_summary,
                wb_classification=doc.metadata.wb_classification,
                doc_sha256=doc_sha256,
                child_chunk_ids=child_ids,
            )

            for idx, sec in enumerate(doc.sections):
                chunk_id = child_ids[idx]
                prev_id = child_ids[idx - 1] if idx > 0 else None
                next_id = child_ids[idx + 1] if idx + 1 < len(child_ids) else None
                chunk_sha256 = hashlib.sha256(sec.text.encode("utf-8")).hexdigest()

                # Parent-enriched searchable representation preserves title, thematic tags, and section text
                indexed_text = (
                    f"{doc.metadata.title} | Grant {doc.metadata.grant_id} | "
                    f"{doc.metadata.country} ({doc.metadata.region}) | "
                    f"{doc.metadata.priority_area} — {doc.metadata.thematic_area} | "
                    f"Crosscutting: {', '.join(doc.metadata.crosscutting_tags) or 'General'} | "
                    f"Section {sec.section_id} {sec.section_title}: {sec.text}"
                )

                chunks.append(
                    IngestedChunk(
                        chunk_id=chunk_id,
                        doc_id=doc.metadata.doc_id,
                        grant_id=doc.metadata.grant_id,
                        title=doc.metadata.title,
                        doc_type=doc.metadata.doc_type,
                        country=doc.metadata.country,
                        region=doc.metadata.region,
                        priority_area=doc.metadata.priority_area,
                        thematic_area=doc.metadata.thematic_area,
                        crosscutting_tags=tuple(doc.metadata.crosscutting_tags),
                        wb_classification=doc.metadata.wb_classification,
                        fiscal_year=doc.metadata.fiscal_year,
                        section_id=sec.section_id,
                        section_title=sec.section_title,
                        page_ref=sec.page_ref,
                        raw_text=sec.text,
                        indexed_text=indexed_text,
                        parent_summary=doc.executive_summary,
                        prev_chunk_id=prev_id,
                        next_chunk_id=next_id,
                        doc_sha256=doc_sha256,
                        chunk_sha256=chunk_sha256,
                    )
                )

        return chunks, parents
