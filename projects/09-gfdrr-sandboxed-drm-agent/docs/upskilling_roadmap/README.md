# GFDRR Enterprise AI-Upskilling Playbook & 3-Tier Staff Capability Manual

Practical capability roadmap and hands-on operating manual enabling non-technical, operational, and technical Disaster Risk Management (DRM) & Monitoring and Evaluation (M&E) teams to adopt governed AI safely across **GFDRR's 2026–2030 Strategy** (4 Priority Areas, 10 Thematic Areas, 2 Crosscutting Pillars, and the 100M resilience-beneficiary target).

- **Runnable Companion Lab:** [`hands_on_governed_ai_lab.ipynb`](hands_on_governed_ai_lab.ipynb) — interactive, self-contained exercises for all three tracks (runs 100% offline in `deterministic_local` mode).

---

## Why Governance-First Upskilling Matters in Multilateral DRM

AI tools in multilateral grant operations fail in two predictable ways:
1. **Ungoverned adoption:** Staff paste `Official Use Only` or `Confidential` field verification notes (containing beneficiary IDs, household locations, or deliberative sovereign Cat DDO triggers) into consumer LLMs, or cite unverified numbers across Board-facing briefs.
2. **Opaque technical gating:** Engineering teams build rigid pipelines that M&E and thematic specialists cannot inspect, challenge, or tune when domain terminology (`GRADE`, `SADDD`, `Cat DDO`, `RF-2030-IND-08`) shifts.

This roadmap embeds capability building directly inside daily GFDRR program workflows rather than treating training as slideware.

---

## At-a-Glance 3-Tier Capability Matrix

| Track | Target Audience | Core Operational Competency | Completion Gate (Hands-On Check) |
|---|---|---|---|
| **Track 1 · Foundational** | DRM Task Team Members, Program Assistants, Communications & Donor Reporting Staff | Prompt hygiene, WB AMS 6.21A 4-tier classification hygiene, spotting double-counting/hallucinations, verifying provenance receipts | Run `verify-provenance` on two briefs and correctly identify a post-export tampered number + a `Confidential` block |
| **Track 2 · Applied** | M&E Specialists, Priority/Thematic Leads (NbS, DRF, Hydromet/EWS, Urban Heat, FCV/Gender) | Scoped RAG querying, interpreting retrieval diagnostics, labeling HITL relevance (`0/1/2`), executing HIC `approve`/`revise`/`reject` sign-offs | Label 10 retrieved chunks in SQLite HITL store, inspect `nDCG@5` scorecard & $\kappa$, and sign a documented HIC review |
| **Track 3 · Technical** | Disaster Risk Analytics, Digital Earth & Geospatial Leads, Data/Software Engineers | Extending Pydantic v2 metadata schemas, adding read-only geospatial exposure tools, scoping least-privilege agent manifests, enforcing CI eval gates | Add a scoped tool to `SandboxedToolRunner`, pass all adversarial `pytest` injection/PII/tamper gates with `0%` leakage |

---

## Track 1 — Foundational Manual (DRM & Communications Staff)

### 1.1 World Bank AMS 6.21A Data Classification Rules for AI Prompts
Before submitting any document or question, verify its classification tier:

| WB Classification Tier | Typical GFDRR Examples | AI Tool Rule |
|---|---|---|
| **`Public`** | Published GFDRR Annual Reports, open GeoNode / City Scan outputs, disclosed PADs | Permitted for external briefs; still verify inline `[DOC-ID §Section, p.X]` citations |
| **`Official Use Only`** | Draft grant M&E reviews, internal aide-mémoires, operational e-BPS / Hydromet diagnostics | Internal WBG/GFDRR governed workspace only (`wbg_analyst`+); never copy into external chatbots |
| **`Confidential`** | Deliberative sovereign Cat DDO trigger negotiations, ESF resettlement grievance audits | Restricted to `me_specialist`+; external dissemination blocked by HIC gate |
| **`Strictly Confidential`** | FCV field security/convoy protocols, unredacted camp protection logs, INT-sensitive records | Hard-blocked from standard RAG & specialist agents (`restricted_audit` isolation only) |

### 1.2 Four-Point Pre-Dissemination Verification Checklist
1. **Check Grant Disbursed vs. WB Financing Informed:** Never conflate a `$1.85M` GFDRR technical assistance grant with the `$410.0M` IBRD/IDA project financing it informed.
2. **Check Spatial Deduplication Toward the 100M Target:** Confirm Admin-2 overlapping grants are not summed naively (`DOC-GFDRR-017 §S3.0`).
3. **Check Redaction Tokens:** If an internal note surfaces `[REDACTED_PERSON]`, `[REDACTED_ID]`, or `[REDACTED_ADDRESS]`, **never** manually re-insert names or national IDs from local spreadsheets.
4. **Verify the Cryptographic Receipt:** Before circulating any exported Markdown brief, run:
   ```bash
   python3 -m gfdrr_rag.cli verify-provenance path/to/exported_brief.md
   ```
   Confirm `"status": "VERIFIED"` (`hmac_valid=true`, `body_hash_valid=true`, `watermark_valid=true`).

---

## Track 2 — Applied Manual (M&E & Thematic Specialists)

### 2.1 Reading the Governed RAG Scorecard
- **`Recall@5`:** Did the top 5 retrieved sections capture all relevant grant evidence? Low `Recall@5` means relevant grants are being missed upstream.
- **`nDCG@5` & `MRR`:** Are the *most* relevant `S2.0`/`S3.0` Results Framework sections ranked at position #1/#2 rather than buried beneath `S1.0` background sections?
- **`Abstention F1`:** Does the system cleanly refuse out-of-portfolio geographies (e.g., Iceland, Chile) and unauthorized tiers rather than fabricating plausible DRM prose?
- **Judge Calibration ($\kappa_w$):** Quadratic-weighted Cohen's $\kappa$ measures agreement between automated rubric grading and human M&E specialists. Treat automated eval scores as directional until $\kappa_w \ge 0.65$ on a freshly sampled thematic batch.

### 2.2 Continuous HITL Relevance Labeling Protocol
When reviewing candidate chunks in the Streamlit workbench or API (`/feedback`):
- Label **`relevant` (`2`)** when the section directly answers the indicator, financing leverage, or safeguard question with auditable numbers.
- Label **`partially_relevant` (`1`)** when the section provides valid contextual hazard baseline (`S1.0`) for the right grant but lacks the specific M&E outcome metric.
- Label **`irrelevant` (`0`)** when the chunk comes from a different country/thematic intervention or shares only generic DRM vocabulary.

### 2.3 Executing Human-in-Command (HIC) Portfolio Sign-Off
Every multi-agent resilience brief deterministically halts at `PAUSED_AWAITING_HIC`. Before recording `approve`:
1. Inspect `unsupported_claims_caught` in the Critic report to see what draft extrapolations were stripped.
2. Review `Actionable M&E Safeguard Gaps Flagged` (missing SADDD disaggregation, disability access, or FCV spatial coarsening).
3. Record a substantive rationale (`>= 8` characters; cite verified grant IDs) via `submit_hic_decision(...)`.

---

## Track 3 — Technical Manual (Analytics, Digital Earth & Geospatial Leads)

### 3.1 Extending Read-Only Geospatial Tools Safely
All tabular/raster analytics tools exposed to `geospatial_hazard_summarizer` must:
1. Accept validated country/ISO3 parameters and resolve paths strictly through `SandboxedToolRunner.validate_read_path(...)`.
2. Remain pure read-only functions (`allow_filesystem_write=False`, `allow_shell_exec=False`).
3. Return deterministic dictionary schemas so `CriticArbitratorAgent` can cross-check numerical exposure claims.

### 3.2 Least-Privilege Capability Scoping (`security/capabilities.py`)
Never grant a specialist agent wildcard tool access. Follow strict separation of duties:
- `grant_me_analyst`: `allowed_tools=("rag.query",)`, `max_wb_classification="Official Use Only"`.
- `geospatial_hazard_summarizer`: `allowed_tools=("geo.compute_exposure_stats",)`, `max_wb_classification="Public"`.
- `critic_arbitrator`: `allowed_tools=("provenance.verify_manifest", "rubric.verify_claims")`.

### 3.3 Mandatory Pre-Merge CI Gate
Run all functional, evaluation, redaction, tamper, and sandbox security tests prior to merging any retrieval weight or prompt change:
```bash
python3 -m pytest projects/08-gfdrr-me-governed-rag/tests projects/09-gfdrr-sandboxed-drm-agent/tests -v
```
Every PR must maintain:
- `Classification Compliance Rate == 100.0%`
- `PII Redaction Recall == 100.0%` (`0` unredacted spans across Checkpoints A, B, and C)
- `Security Violation Leak Rate == 0.0%`
- Positive out-of-fold `nDCG@5` 95% bootstrap confidence interval on the M&E benchmark.
