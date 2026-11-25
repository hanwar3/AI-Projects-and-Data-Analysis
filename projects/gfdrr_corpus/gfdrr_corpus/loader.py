"""Loader helpers for reading the GFDRR corpus, hazard exposure table, and benchmark queries."""

from __future__ import annotations

import json
from pathlib import Path

from .build_corpus import BENCHMARKS_DIR, DOCUMENTS_DIR, TABLES_DIR, export_all
from .schemas import BenchmarkQuery, CorpusDocument, HazardExposureRecord


def ensure_built() -> None:
    if not (DOCUMENTS_DIR / "gfdrr_corpus.jsonl").exists():
        export_all()


def load_corpus_documents(corpus_jsonl: Path | None = None) -> list[CorpusDocument]:
    ensure_built()
    path = corpus_jsonl or (DOCUMENTS_DIR / "gfdrr_corpus.jsonl")
    docs: list[CorpusDocument] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                docs.append(CorpusDocument.model_validate_json(line))
    return docs


def load_hazard_table(table_json: Path | None = None) -> list[HazardExposureRecord]:
    ensure_built()
    path = table_json or (TABLES_DIR / "country_hazard_exposure.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [HazardExposureRecord.model_validate(item) for item in raw]


def load_benchmark_queries(queries_json: Path | None = None) -> list[BenchmarkQuery]:
    ensure_built()
    path = queries_json or (BENCHMARKS_DIR / "eval_queries.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [BenchmarkQuery.model_validate(item) for item in raw]
