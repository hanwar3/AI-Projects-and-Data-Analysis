# Project 09 · Sandboxed Multi-Agent DRM Workflow & Enterprise Staff AI-Upskilling Playbook

Stateful, capability-scoped multi-agent orchestrator for synthesizing multi-country **GFDRR Resilience & Grant M&E Briefs**—paired with a **3-Tier Enterprise Staff AI-Upskilling Playbook & Runnable Lab**.

- **Design Decisions & Governance Trade-Offs:** [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md)
- **3-Tier DRM Staff AI-Upskilling Playbook:** [`docs/upskilling_roadmap/README.md`](docs/upskilling_roadmap/README.md)
- **Runnable Hands-On Lab Notebook:** [`docs/upskilling_roadmap/hands_on_governed_ai_lab.ipynb`](docs/upskilling_roadmap/hands_on_governed_ai_lab.ipynb)
- **Benchmark Outputs & Traces:** [`outputs/`](outputs/) (`multi_agent_benchmark.csv`, `fig_agent_benchmark.png`, `agent_interactive_traces.json`)

---

## Operational Problem

Drafting a country or regional resilience brief at GFDRR requires joining **three distinct evidence streams**: (1) historical M&E grant performance and World Bank financing informed, (2) subnational multi-hazard geospatial exposure (flood, seismic MMI VI+, extreme heat days), and (3) crosscutting **Disaster–FCV Nexus** and **Inclusive DRM & Gender Equality (SADDD)** safeguards.

Single-pass text RAG misses structured geospatial exposure tables and systematic safeguard gap checks, while unconstrained autonomous agents introduce unacceptable risks: privilege escalation into `Strictly Confidential` records, prompt-injected tool abuse, unverified cross-grant extrapolations, runaway token costs, and unreviewed external outputs. Equally, technical guardrails fail operationally unless non-technical DRM and M&E staff have a clear roadmap to use, audit, and govern them.

---

## Hardened Multi-Agent Architecture

1. **Planner Agent (`orchestrator/planner.py`):** Decomposes requests into typed sub-tasks mapped to GFDRR's 4 Priority Areas, 10 Thematic Areas, and 2 Crosscutting Pillars.
2. **Least-Privilege Scoped Specialists (`orchestrator/specialists.py` & `security/capabilities.py`):**
   - **`Grant M&E Analyst`:** Allowed `rag.query` up to `Official Use Only`; strictly denied `Strictly Confidential`, `shell.exec`, `fs.write`, and `export.external`.
   - **`Geospatial Hazard Summarizer`:** Allowed read-only `geo.compute_exposure_stats` inside the validated `country_hazard_exposure.csv` workspace root; denied RAG, shell, and network access.
   - **`Crosscutting Inclusion & FCV Reviewer`:** Audits retrieved grant evidence and FCV exposure status against `RF-2030-IND-08` SADDD, women's leadership, disability access, and conflict-sensitivity criteria.
3. **Arbitration & Grounding Critic (`orchestrator/critic.py`):** Verifies HMAC-SHA256 provenance manifests from Project 08, checks every draft claim against authorized chunk tuples, strips unverified synthesis extrapolations, and scores a 5-dimension GFDRR M&E rubric.
4. **Sandboxed Tool Runner (`security/sandbox.py`):** Enforces tool allowlists, per-agent call quotas, classification tier ceilings, regex prompt-injection/command-injection blocking, `Path.resolve()` read-only boundary checks, wall-clock execution timeouts, and an immutable SQLite `security_audit_log`.
5. **Stateful Supervisor & Mandatory HIC Gate (`orchestrator/supervisor.py` & `security/hic_gate.py`):**
   - Enforces hard token, step, and USD cost budgets.
   - Commits **SQLite state checkpoints** (`state_sha256`) after every node, enabling deterministic mid-run crash recovery (`resume_from_checkpoint`) with **zero duplicate upstream calls**.
   - Emits OpenTelemetry-style JSON spans and halts deterministically at `PAUSED_AWAITING_HIC` until a human reviewer submits an HMAC-signed `approve` / `revise` / `reject` decision with substantive rationale.

---

## Empirical Multi-Agent Justification Benchmark (`N = 8` Complex Multi-Country Briefs)

| Architecture | 5-Part Rubric Coverage | Unsupported-Claim Catch Rate | FCV & Gender Gap Audit | Security Violation Leak Rate | Mandatory HIC Gate | Mean Tokens | Est. Cost / 100 Briefs |
|---|---:|---:|---:|---:|:---:|---:|---:|
| `(1)` Single-Pass Governed RAG | 60.0% | 0.0% | 0.0% | 0.0% | No | 414 | $0.124 |
| `(2)` Multi-Agent (No Critic, No HIC) | 97.5% | 0.0% | 100.0% | 0.0% | No | 1,475 | $0.443 |
| `(3)` **Sandboxed Multi-Agent + Critic + HIC** | **97.5%** | **100.0%** | **100.0%** | **0.0%** | **Yes** | **2,242** | **$0.673** |

---

## Reproduce Offline (`deterministic_local` mode)

```bash
# Run all 10 security, injection-blocking, crash-recovery, HIC, benchmark, and lab-notebook tests
python3 -m pytest projects/09-gfdrr-sandboxed-drm-agent/tests -v

# Recompute multi-agent benchmark CSV, high-DPI Matplotlib chart, and interactive OTel/security JSON traces
python3 projects/09-gfdrr-sandboxed-drm-agent/scripts/run_benchmarks_and_export.py
```
