# Design Decisions, Failure Forensics & Limitations (Project 08)

**Author:** Haider Waseem Anwar  
**Context:** Designing an enterprise-governed RAG platform for multilateral Disaster Risk Management (DRM) & Monitoring and Evaluation (M&E) grant portfolios aligned with GFDRR's 2026–2030 Strategy and World Bank AMS 6.21A classification rules.

---

## 1. Domain Framing (Why Standard RAG Breaks on Grant M&E Documents)

Working previously with FEMA Hazard Mitigation Assistance project records (`N = 7,781`), flood inundation footprints along the Indus plain (including nature-based flood retention contexts in Sindh), and multilateral procurement feeds revealed three structural properties of DRM operational documents that break naive chunk-and-embed pipelines:

1. **Intra-Document Sibling Interference:** Every GFDRR grant review mentions the same country, hazard, and headline grant ID across all sections, but **Section `S1.0`** describes historical physical hazard exposure while **Section `S2.0`** records *World Bank Financing Informed* and **Section `S3.0`** records *verified Results Framework beneficiaries*. Embedding flat 500-token sliding windows blurs `S1.0` baseline hazard exposure (e.g., *33.4M people living in Indus floodplains*) with `S3.0` project target outcomes (*6.4M beneficiaries under `GFDRR-PK-2026-001`*). Preserving structural section boundaries (`S1.0`–`S4.0`) and teaching the Stage-4 reranker section-intent cues eliminated baseline-vs-outcome confusion.
2. **Pre-Retrieval vs. Post-Retrieval Access Control:** Filtering restricted documents *after* top-$k$ retrieval creates two vulnerabilities: (a) timing/score side-channels and (b) empty top-$k$ starvation when restricted chunks crowd out valid `Public` evidence. Enforcing World Bank's 4-tier classification gate **strictly pre-retrieval** ensures unauthorized chunks never enter BM25 or dense candidate sets, while still detecting explicit queries targeting restricted grants (`BLOCKED_CLASSIFICATION`) and logging them to an immutable SQLite audit table.
3. **Why Three PII Checkpoints Instead of One:** Redacting only at ingestion permanently destroys auditability for `restricted_audit` teams and misses query-time reconstructions; redacting only at final LLM output allows raw national IDs (`CNIC`, `NID`), phone numbers, and household street addresses into third-party model prompts and UI citation previews. Running typed redaction independently at **Checkpoint A** (retrieved chunk), **Checkpoint B** (rendered citation snippet), and **Checkpoint C** (generated synthesis) guarantees `0%` PII leakage even if an upstream regex is bypassed or a live LLM echoes prompt fragments.

---

## 2. Key Empirical Findings & Failure Forensics

### Finding A: Unweighted Hybrid RRF (`0.824`) Underperformed Dense-Only (`0.852`) Until Paired with a Cross-Feature Reranker (`0.906`)
- **What happened:** Naive unweighted Reciprocal Rank Fusion (`k=60`, no metadata filter, no reranker) scored `nDCG@5 = 0.824`, slightly trailing `Dense-only` (`0.852`).
- **Root cause:** On pure semantic paraphrase queries (e.g., *"living coastal tree belts damping storm waves in Southeast Asian river deltas"* matching Viet Nam's Mekong mangrove grant without sharing exact surface words), BM25 assigned near-zero scores or ranked generic storm-surge documents higher, pulling down RRF ranks. Conversely, `Dense-only` struggled to separate exact alphanumeric grant IDs (`GFDRR-PK-2026-004` vs `001` vs `019`).
- **Resolution:** Adding thematic/country metadata pre-filtering and a lightweight cross-feature reranker (`Config D`) lifted `Recall@5` from `0.781` to **`0.919`** and `nDCG@5` to **`0.906`** (`0.910` after 4-fold HITL tuning) at only `+5.5 ms` P95 latency cost.

### Finding B: Out-of-Portfolio Geography Hallucination Trap (Entity-Calibrated Abstention)
- **What happened during initial testing:** Out-of-corpus queries such as *"sovereign geothermal catastrophe bond coupon spread for Iceland's Reykjanes rift"* initially achieved `0.62` uncalibrated confidence against Belize's `Cat DDO` document because generic DRM terms (`sovereign`, `catastrophe`) matched strongly.
- **Resolution:** Added a deterministic **Entity & Vocabulary Grounding Gate** inside `HybridRetriever` that penalizes queries introducing capitalized geographic/institutional proper nouns with zero corpus document frequency (`Iceland`, `Reykjanes`, `Chile`, `Atacama`, `Saskatchewan`), raising **Abstention F1 from `0.667` to `1.000`** without causing a single false abstention on the 26 answerable queries.

### Finding C: Automated Rubric Judge Optimism Bias ($\kappa_w = 0.46$)
- Comparing automated rubric grading against M&E specialist relevance labels across `208` query-chunk pairs yielded quadratic-weighted Cohen's $\kappa_w = 0.46$. Inspection of the $3 \times 3$ confusion matrix (`[[51, 63, 28], [1, 5, 9], [0, 6, 45]]`) shows the automated judge rarely misses truly relevant `grade=2` chunks (`45/51` recalled) but over-rates `S1.0` contextual sections from the same country. We therefore use automated judges only for regression triage, never as a substitute for human M&E ground truth.

---

## 3. Cryptographic Provenance Trade-Offs

Exported Markdown briefs carry both a visible JSON **HMAC-SHA256 Provenance Receipt** and a steganographic **zero-width Unicode watermark** (`U+200B..U+2060` bounded by `U+FEFF`/`U+2063`) in the document title binding `answer_sha256[:24] || hmac_sha256[:24]`.
- **Strength:** Detects any 1-character post-export numerical edit (e.g., inflating `$410.0M` to `$910.0M`) as well as receipt-stripping attacks via `gfdrr-rag verify-provenance`.
- **Known Limit:** Zero-width characters survive Markdown, HTML, Slack/Teams copy-paste, and UTF-8 editors, but are stripped if a brief is printed to paper and re-OCR'd. Production Word/PDF exports should pair this with WBG PKI detached signatures (CMS/CAdES).

---

## 4. Candid Limitations

1. **Synthetic Domain Corpus:** All 22 GFDRR documents and 6 PII-bearing field notes are synthetic/public-domain evaluations modeled on GFDRR's 2026–2030 Strategy. Real operational PDFs include scanned annexes, multi-column Aide-Mémoire tables, and French/Spanish/Arabic field logs requiring OCR + multilingual NER (e.g., Presidio + XLM-R token classifiers) beyond deterministic regex/role rules.
2. **Sample Size (`N = 32` Queries):** While sufficient to detect the `+0.087` HITL `nDCG@5` gain (`95% CI [+0.039, +0.145]`), production CI gates across all 10 GFDRR thematic areas should maintain `250–500` stratified queries curated with thematic focal points.
