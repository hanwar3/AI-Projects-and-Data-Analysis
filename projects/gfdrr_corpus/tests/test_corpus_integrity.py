"""Integrity and taxonomy coverage tests for the shared GFDRR 2026-2030 domain corpus."""

from __future__ import annotations

from gfdrr_corpus.loader import (
    load_benchmark_queries,
    load_corpus_documents,
    load_hazard_table,
)
from gfdrr_corpus.taxonomy import (
    CROSSCUTTING_AREAS,
    PRIORITY_AREAS,
    THEMATIC_AREAS,
    WB_CLASSIFICATIONS,
    WB_REGIONS,
)


def test_corpus_taxonomy_and_classification_completeness() -> None:
    docs = load_corpus_documents()
    assert 18 <= len(docs) <= 24

    priorities = {d.metadata.priority_area for d in docs}
    thematics = {d.metadata.thematic_area for d in docs}
    tiers = {d.metadata.wb_classification for d in docs}
    regions = {d.metadata.region for d in docs}
    crosscutting = {tag for d in docs for tag in d.metadata.crosscutting_tags}

    assert priorities == set(PRIORITY_AREAS)
    assert thematics == set(THEMATIC_AREAS)
    assert tiers == set(WB_CLASSIFICATIONS)
    assert regions == set(WB_REGIONS)
    assert crosscutting == set(CROSSCUTTING_AREAS)


def test_hazard_table_and_benchmark_queries() -> None:
    hazards = load_hazard_table()
    assert len(hazards) == 14
    assert sum(h.projected_beneficiaries_m for h in hazards) > 60.0

    queries = load_benchmark_queries()
    assert len(queries) == 32
    categories = {q.query_category for q in queries}
    assert categories == {
        "lexical_exact",
        "semantic_paraphrase",
        "multi_grant_synthesis",
        "pii_trap",
        "classification_restricted",
        "unanswerable_abstain",
    }
