# Project 08 · Governed M&E Grant Portfolio RAG Platform (World Bank / GFDRR)

Auditable, policy-gated Retrieval-Augmented Generation (RAG) platform built for **GFDRR Monitoring & Evaluation (M&E)** and thematic grant review workflows across **GFDRR's 2026–2030 Results Framework** (4 Priority Areas, 10 Thematic Areas, 2 Crosscutting Pillars, and the 100M resilience-beneficiary target).

- **Architecture & Trade-Off Forensics:** [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md)
- **Benchmark Outputs & CSVs:** [`outputs/`](outputs/) (`ablation_metrics.csv`, `error_decomposition.csv`, `hitl_tuning_summary.json`, `rag_interactive_traces.json`)
- **Shared Corpus:** [`../gfdrr_corpus/`](../gfdrr_corpus/) (`22` section-numbered grant & M&E documents, `88` chunks, `32` benchmark queries)

---

## Operational Problem

Evaluating multi-country DRM grant portfolios requires synthesizing technical baselines (`S1.0`), World Bank financing informed (`S2.0`), 2026–2030 corporate outcome indicators (`S3.0`), and field implementation/safeguard audits (`S4.0`). Standard off-the-shelf vector RAG fails multilateral governance requirements in four ways:
1. **Institutional Classification Blindness:** Retrieves `Official Use Only`, `Confidential`, or `Strictly Confidential` deliberative/security records for unauthorized roles.
2. **PII Leakage from Field Verification Notes:** Surfaces beneficiary national IDs (`CNIC`, `NID`, `BEN-ID`), phone numbers, emails, and household addresses embedded in grievance or third-party monitoring annexes.
3. **Unverifiable Citations & Silent Hallucination:** Extrapolates plausible DRM prose on out-of-portfolio countries and provides no cryptographic proof that an exported M&E brief matches the underlying source text.
4. **Static Uncalibrated Ranking:** Deploys a single vector store without measuring lexical-vs-semantic trade-offs or learning from M&E reviewer relevance feedback.

---

## Seven-Layer Governed Architecture

1. **Structure-Aware Ingestion (`ingestion/chunker.py`):** Preserves numbered section boundaries (`S1.0`–`S4.0`), page references, SHA-256 document/chunk digests, and parent-child sibling pointers validated with strict Pydantic v2 schemas.
2. **Pre-Retrieval World Bank AMS 6.21A Classification Gate (`governance/classification.py`):** Maps caller roles (`public_external`, `wbg_analyst`, `me_specialist`, `restricted_audit`) onto WB's 4 classification tiers (`Public`, `Official Use Only`, `Confidential`, `Strictly Confidential`). Unauthorized chunks are filtered **pre-retrieval**; targeted restricted requests are hard-blocked and logged to an append-only SQLite audit table enforced with `BEFORE UPDATE` / `BEFORE DELETE` `RAISE(ABORT)` triggers.
3. **Hybrid Multi-Stage Retrieval (`retrieval/`):** Combines Okapi BM25 (`k1=1.5, b=0.75` preserving grant IDs and `RF-2030-IND-*` codes), dual-mode Dense Semantic Embeddings (`deterministic_local` SVD concept-bridged vectors or `live` OpenAI embeddings), weighted **Reciprocal Rank Fusion (RRF)**, a **Cross-Feature Reranker**, and an entity-calibrated **Abstention Gate**.
4. **3-Checkpoint Dynamic PII Redaction Guardrail (`governance/pii_guardrail.py`):** Scans and masks `PERSON`, `EMAIL`, `PHONE`, `ID`, and `ADDRESS` spans with typed tokens (`[REDACTED_ID]`, `[REDACTED_ADDRESS]`, etc.) across **(A)** retrieved chunk text, **(B)** citation snippets, and **(C)** final generated output.
5. **Citation Precision Verifier (`generation/synthesizer.py`):** Enforces inline `[DOC-GFDRR-XXX §S2.0, p.4-6]` citations and verifies every cited tuple against authorized retrieved chunks.
6. **Cryptographic Provenance & Watermarking (`governance/provenance.py`):** Attaches an **HMAC-SHA256 signed Provenance Manifest** and embeds an invisible **zero-width Unicode steganographic watermark** into exported M&E briefs, verifiable via `python3 -m gfdrr_rag.cli verify-provenance <brief.md>`.
7. **Continuous HITL Feedback Loop (`eval/hitl_loop.py`):** Persists M&E specialist relevance labels (`relevant=2`, `partially_relevant=1`, `irrelevant=0`) in SQLite, audits automated-judge agreement via Cohen's $\kappa$, and tunes RRF/reranker weights out-of-fold.

---

## Verified Benchmark Results (`N = 32` Queries)

| Configuration | Recall@5 | MRR | nDCG@5 | Citation Prec. | Abstention F1 | PII Redaction | WB Class. Compliance | P95 Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `(A)` Dense-only (SVD Semantic) | 0.826 | 0.952 | 0.852 | 1.000 | 1.000 | 100.0% | 100.0% | 7.3 ms |
| `(B)` BM25-only (Lexical) | 0.783 | 0.955 | 0.832 | 1.000 | 1.000 | 100.0% | 100.0% | 7.5 ms |
| `(C)` Hybrid RRF (Unweighted) | 0.781 | 0.942 | 0.824 | 1.000 | 1.000 | 100.0% | 100.0% | 7.6 ms |
| `(D)` Hybrid RRF + Reranker + Filter | 0.919 | 0.962 | 0.906 | 1.000 | 1.000 | 100.0% | 100.0% | 13.1 ms |
| `(E)` Hybrid RRF + Reranker (HITL-Tuned) | **0.926** | **0.962** | **0.910** | **1.000** | **1.000** | **100.0%** | **100.0%** | **12.4 ms** |

- **Continuous HITL Uplift (4-Fold Out-of-Fold, 208 Judgments):** `nDCG@5` improves from `0.824` (unweighted RRF) to `0.911` (**`+0.087`**, 95% paired bootstrap CI **`[+0.039, +0.145]`**, $p = 0.001$).
- **Retrieval vs. Synthesis Error Decomposition (`N = 26` answerable queries):** Complete fact-grounded synthesis rises from `15/26` (`Dense-only`) and `17/26` (`BM25-only`) to `22/26` (`Hybrid RRF + Reranker + Filter`), with `0` off-target hallucinations.
- **Automated Judge Calibration:** Quadratic-weighted Cohen's $\kappa_w = 0.46$ ($\kappa = 0.27$) — automated rubric scoring catches `45/51` (`88%`) of primary `grade=2` sections but over-credits `S1.0` background sections, demonstrating empirically why human M&E calibration remains indispensable.

---

## Reproduce Offline (`deterministic_local` mode)

```bash
# Run all 23 functional, redaction, classification, tamper, HITL, and API tests
python3 -m pytest projects/08-gfdrr-me-governed-rag/tests -v

# Recompute all CSV tables, high-DPI Matplotlib charts, and interactive JSON traces
python3 projects/08-gfdrr-me-governed-rag/scripts/run_benchmarks_and_export.py

# Launch FastAPI service or Streamlit M&E Workbench
uvicorn gfdrr_rag.api:app --reload --port 8080
streamlit run projects/08-gfdrr-me-governed-rag/app_streamlit.py
```
