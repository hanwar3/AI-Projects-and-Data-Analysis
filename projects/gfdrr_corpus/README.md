# GFDRR 2026–2030 Synthetic DRM & Grant M&E Corpus

Shared domain corpus backing **Project 08** (`08-gfdrr-me-governed-rag`) and **Project 09** (`09-gfdrr-sandboxed-drm-agent`).

All documents are synthetic/public-domain technical evaluations modeled on GFDRR's **2026–2030 Strategy & Results Framework** and World Bank **AMS 6.21A Information Classification Policy**. Every PII string in the internal field-monitoring sections uses fictional `*-sim.*` domains and synthetic national/beneficiary IDs created solely to stress-test multi-checkpoint redaction.

## Corpus Coverage

- **22 section-numbered grant & M&E documents** (`88` structural sections `S1.0`–`S4.0`) in [`documents/gfdrr_corpus.jsonl`](documents/gfdrr_corpus.jsonl) and [`documents/markdown/`](documents/markdown/)
- **4 GFDRR Priority Areas & all 10 Thematic Areas:**
  1. *Strengthening Risk-Informed Decision Making:* Digital Earth; Disaster Risk Analytics (`GRADE`, `impACT360`)
  2. *Reducing Risk and Mainstreaming DRM:* Building Regulation & Resilient Housing; City Resilience & Urban Heat; Nature-based Solutions for Climate Resilience; Resilient Infrastructure; Resilient Human Development Infra (Safer Schools & Health Systems)
  3. *Enhancing Financial Preparedness:* Disaster Risk Finance (`Cat DDO`, subnational parametric pools)
  4. *Improving Disaster Preparedness & Resilient Recovery:* Emergency Preparedness & Response (`EOC`, `CPRT`); Hydromet Services & Early Warning Systems (`CREWS`, last-mile alerts)
- **2 Crosscutting Areas:** *Disaster–FCV Nexus* (`FCV`) and *Inclusive DRM & Gender Equality* (`Gender/Inclusion`, SADDD)
- **4 World Bank Classification Tiers:** `Public` (9), `Official Use Only` (7), `Confidential` (3), `Strictly Confidential` (3)
- **6 Synthetic PII Trap Documents:** Embed names, emails, international phone numbers, national/beneficiary IDs (`CNIC`, `NID`, `CIN`, `BEN-ID`), and street addresses inside field verification notes
- **14-Country Geospatial Hazard & Exposure Table:** [`tables/country_hazard_exposure.csv`](tables/country_hazard_exposure.csv)
- **32 Graded M&E Benchmark Queries:** [`benchmarks/eval_queries.json`](benchmarks/eval_queries.json)

## Rebuild

```bash
python3 -m gfdrr_corpus.build_corpus
```
