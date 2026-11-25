# Design Decisions, Security Architecture & Limitations (Project 09)

**Author:** Haider Waseem Anwar  
**Context:** Justifying when—and under what operational guardrails—a multi-agent architecture outperforms single-pass RAG for GFDRR Disaster Risk Management (DRM) & M&E resilience briefs, and pairing technical controls with a practical 3-Tier Staff AI-Upskilling Playbook.

---

## 1. When Is Multi-Agent Orchestration Actually Justified?

Multi-agent systems are frequently over-engineered for tasks a single structured RAG prompt handles faster and cheaper. In our benchmark (`N = 8` multi-country resilience briefs), **Single-Pass Governed RAG** uses `5.4x` fewer tokens (`414` vs `2,242`) and suffices whenever a user asks a single-thematic grant lookup question (covered by Project 08).

Multi-agent decomposition becomes empirically justified **only** when a workflow requires heterogeneous tools and separation of privilege:
1. **Joining Text Evidence with Structured Geospatial Hazard Analytics:** `GeospatialHazardSummarizerAgent` executes deterministic tabular analytics over Admin-1 multi-hazard exposure (`riverine_coastal_flood_pop_m`, `seismic_mmi6_share_pct`, `annual_extreme_heat_days_gt35c`, and `% contribution toward GFDRR's 100M target`)—bridging grant records with the physical hazard footprints mapped in Projects `02`–`05`. Single-pass text RAG scored **`60.0%`** rubric coverage because it lacked structured hazard-table execution and systematic safeguard auditing; adding scoped specialists raised rubric coverage to **`97.5%`**.
2. **Why Multi-Agent Without a Critic Is Dangerous (`0%` vs `100%` Catch Rate):** Merging outputs from multiple specialist agents introduces synthesis extrapolations (e.g., projecting unverified sovereign debt-service savings across countries). Without `CriticArbitratorAgent`, `0%` of injected ungrounded synthesis claims were caught; adding deterministic citation-tuple and provenance-manifest arbitration caught **`100.0%`** before human review at an incremental cost of `$0.23` per 100 briefs (`+767` tokens/brief).

---

## 2. Defense-in-Depth Security Choices

1. **Least-Privilege Capability Manifests Over Prompt Polite Requests:** Telling an LLM in a system prompt *"please do not query Strictly Confidential documents or run shell commands"* is not a security boundary. `SandboxedToolRunner` enforces capability manifests in Python *outside* the model: `GeospatialHazardSummarizerAgent` cannot invoke `rag.query` at all, and `GrantMEAnalystAgent` is capped at `Official Use Only`, deterministically blocking escalation attempts (`CLASSIFICATION_ESCALATION_BLOCKED`), path escapes (`WORKSPACE_BOUNDARY_ESCAPE`), and prompt-injection overrides (`PROMPT_INJECTION_OVERRIDE`).
2. **Node-Granular SQLite Crash Recovery:** Long-running portfolio reviews across live LLM APIs can fail mid-workflow on rate limits or worker restarts. Persisting SHA-256-hashed state checkpoints after every node allows `resume_from_checkpoint(run_id)` to resume at Node `#4` (`crosscutting_fcv_gender_reviewer`) without re-billing or re-running Nodes `#1–#3`.
3. **Human-in-Command (HIC) vs. Passive HITL:** While Project 08 uses **Human-in-the-Loop (HITL)** chunk ratings asynchronously to improve retrieval weights, external dissemination briefs and portfolio ratings require **Human-in-Command (HIC)**: deterministic execution suspension (`PAUSED_AWAITING_HIC`) that cannot transition to `APPROVED_FINAL` without an attributed, HMAC-signed human rationale.

---

## 3. Integrating Technical Engineering with Staff Upskilling

At multilateral institutions, governance guardrails fail if program officers view abstentions or classification blocks as opaque errors and route around them using personal tools. Designing the **3-Tier Staff AI-Upskilling Playbook** (`docs/upskilling_roadmap/`) alongside the codebase ensured every guardrail maps directly to a staff competency:
- *Foundational staff* learn how to read classification verdicts and run `verify-provenance`.
- *M&E and thematic specialists* own the HITL relevance store and HIC release gate.
- *Analytics and geospatial leads* extend read-only hazard tools and maintain CI regression gates.

---

## 4. Candid Limitations

1. **In-Process Thread Sandbox vs. OS Container Isolation:** `SandboxedToolRunner` enforces Python tool allowlists, `Path.resolve()` read-only boundaries, regex injection checks, and wall-clock timeouts. If future workflows permit dynamic user-written geospatial Python scripts (e.g., custom `rasterio`/`GeoPandas` expressions), execution must move into network-isolated ephemeral containers (`gVisor` / Firecracker microVMs) with cgroup memory/CPU limits.
2. **Deterministic Token/Cost Accounting:** Offline mode estimates token budgets (`~4 chars/token`, `$3.00 / 1M` blended tokens) so budget-cap tests run reproducibly without live API billing; live deployments bind exact usage counters from Anthropic/OpenAI response headers.
