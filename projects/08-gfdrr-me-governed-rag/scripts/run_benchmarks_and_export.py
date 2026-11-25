"""Execute Project 08 ablations, HITL feedback tuning, Matplotlib chart rendering, and static JSON trace export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

from gfdrr_rag.config import RAGConfig, RetrievalWeights
from gfdrr_rag.eval.ablation import evaluate_mode, run_four_way_ablation
from gfdrr_rag.eval.hitl_loop import HITLFeedbackStore, run_hitl_feedback_experiment
from gfdrr_rag.pipeline import GovernedRAGPipeline
from gfdrr_corpus.loader import load_benchmark_queries

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_DIR.parent.parent
OUTPUTS_DIR = PROJECT_DIR / "outputs"
ASSETS_IMG_DIR = REPO_ROOT / "assets" / "img"
ASSETS_TRACES_DIR = REPO_ROOT / "assets" / "traces"

# Exact topographic portfolio palette from index.html & hma_analysis.ipynb
INK = "#16181d"
MUTED = "#5b6270"
RULE = "#d9dce1"
WATER = "#2f6f9f"
CONTOUR = "#9a5b2e"
PANEL = "#f4f5f6"
SLATE = "#7a8291"

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10.0,
        "axes.edgecolor": RULE,
        "axes.labelcolor": INK,
        "xtick.color": MUTED,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "axes.titlesize": 11.5,
        "figure.dpi": 120,
        "savefig.dpi": 220,
        "savefig.bbox": "tight",
    }
)


def plot_ablation_and_hitl(
    ablation_rows: list[dict[str, object]],
    hitl_summary: dict[str, object],
    out_paths: list[Path],
) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.5), gridspec_kw={"width_ratios": [1.45, 1.0]})
    fig.patch.set_facecolor("#ffffff")

    labels = [
        "(A) Dense-only\n(SVD Semantic)",
        "(B) BM25-only\n(Lexical)",
        "(C) Hybrid RRF\n(Unweighted)",
        "(D) Hybrid RRF +\nReranker + Filter",
    ]
    recall_vals = [float(r["recall_at_5"]) for r in ablation_rows]
    mrr_vals = [float(r["mrr"]) for r in ablation_rows]
    ndcg_vals = [float(r["ndcg_at_5"]) for r in ablation_rows]

    y = np.arange(len(labels))
    h = 0.24

    ax1.barh(y + h, recall_vals, height=h, color="#9ab8d1", label="Recall@5")
    ax1.barh(y, mrr_vals, height=h, color=CONTOUR, label="MRR")
    bars_ndcg = ax1.barh(y - h, ndcg_vals, height=h, color=WATER, label="nDCG@5")

    for bar in bars_ndcg:
        w = bar.get_width()
        ax1.text(w + 0.012, bar.get_y() + bar.get_height() / 2, f"{w:.3f}", va="center", ha="left", fontsize=8.8, fontweight="bold", color=INK)

    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=9.2)
    ax1.invert_yaxis()
    ax1.set_xlim(0, 1.12)
    ax1.set_xlabel("Score (N = 26 answerable + 6 abstention/blocked queries)")
    ax1.set_title("4-Way Retrieval Ablation (GFDRR M&E Benchmark)", loc="left", pad=24)
    ax1.grid(axis="x", color=RULE, linewidth=0.7, linestyle="--", alpha=0.8)
    ax1.set_axisbelow(True)
    ax1.legend(frameon=False, bbox_to_anchor=(0.0, 1.01), loc="lower left", ncol=3, fontsize=8.6)

    # Panel B: Out-of-fold HITL Feedback Weight Tuning Uplift with 95% Bootstrap CI
    untuned_ndcg = float(hitl_summary["untuned_ndcg_at_5"])
    tuned_ndcg = float(hitl_summary["tuned_ndcg_at_5"])
    untuned_mrr = float(hitl_summary["untuned_mrr"])
    tuned_mrr = float(hitl_summary["tuned_mrr"])
    ci_low, ci_high = hitl_summary["ndcg_ci95"]  # type: ignore[misc]
    delta_ndcg = float(hitl_summary["delta_ndcg_at_5"])

    x = np.arange(2)
    width = 0.32
    b1 = ax2.bar(x - width / 2, [untuned_ndcg, untuned_mrr], width=width, color=SLATE, label="Before HITL (Unweighted)")
    b2 = ax2.bar(x + width / 2, [tuned_ndcg, tuned_mrr], width=width, color=WATER, label="After HITL Tuning (4-Fold OOF)")

    for bar in list(b1) + list(b2):
        val = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width() / 2, val + 0.015, f"{val:.3f}", ha="center", va="bottom", fontsize=8.8, fontweight="bold", color=INK)

    ax2.set_xticks(x)
    ax2.set_xticklabels(["nDCG@5", "MRR"], fontsize=9.8)
    ax2.set_ylim(0, 1.15)
    ax2.set_ylabel("Out-of-Fold Metric")
    ax2.set_title("Continuous HITL Reviewer Tuning Uplift", loc="left", pad=24)
    ax2.grid(axis="y", color=RULE, linewidth=0.7, linestyle="--", alpha=0.8)
    ax2.set_axisbelow(True)
    ax2.legend(frameon=False, bbox_to_anchor=(0.0, 1.01), loc="lower left", ncol=2, fontsize=8.1)
    ax2.annotate(
        f"Δ nDCG@5 = +{delta_ndcg:.3f}\n95% Bootstrap CI [+{ci_low:.3f}, +{ci_high:.3f}]",
        xy=(0 + width / 2, tuned_ndcg),
        xytext=(0.06, 0.16),
        fontsize=8.4,
        color=CONTOUR,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.38", "facecolor": "#ffffff", "edgecolor": CONTOUR, "lw": 1.1},
    )

    plt.tight_layout()
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(p)
    plt.close(fig)


def plot_error_decomposition_and_kappa(
    ablation_rows: list[dict[str, object]],
    hitl_summary: dict[str, object],
    out_paths: list[Path],
) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.4), gridspec_kw={"width_ratios": [1.3, 1.0]})
    fig.patch.set_facecolor("#ffffff")

    modes = ["Dense-only", "BM25-only", "Hybrid RRF", "Hybrid+Rerank"]
    c_hit_ok = [int(r["error_decomposition"]["retrieval_hit_synthesis_complete"]) for r in ablation_rows]  # type: ignore[index]
    c_hit_part = [int(r["error_decomposition"]["retrieval_hit_synthesis_partial"]) for r in ablation_rows]  # type: ignore[index]
    c_miss_abs = [int(r["error_decomposition"]["retrieval_miss_abstained"]) for r in ablation_rows]  # type: ignore[index]
    c_miss_off = [int(r["error_decomposition"]["retrieval_miss_off_target"]) for r in ablation_rows]  # type: ignore[index]

    x = np.arange(len(modes))
    w = 0.52
    ax1.bar(x, c_hit_ok, width=w, color=WATER, label="Retrieval Hit + Complete Synthesis")
    b_p = np.array(c_hit_ok)
    ax1.bar(x, c_hit_part, width=w, bottom=b_p, color="#8cb4d2", label="Retrieval Hit + Partial Fact Selection")
    b_m1 = b_p + np.array(c_hit_part)
    ax1.bar(x, c_miss_abs, width=w, bottom=b_m1, color="#c7cdd6", label="Retrieval Miss + Safe Abstention")
    b_m2 = b_m1 + np.array(c_miss_abs)
    ax1.bar(x, c_miss_off, width=w, bottom=b_m2, color=CONTOUR, label="Retrieval Miss + Off-Target Context")

    for idx, val in enumerate(c_hit_ok):
        ax1.text(idx, val / 2, f"{val}/26", ha="center", va="center", color="#ffffff", fontweight="bold", fontsize=9.0)

    ax1.set_xticks(x)
    ax1.set_xticklabels(modes, fontsize=9.2)
    ax1.set_ylim(0, 31)
    ax1.set_ylabel("Answerable M&E Queries (N = 26)")
    ax1.set_title("Retrieval vs. Synthesis Error Decomposition", loc="left", pad=10)
    ax1.legend(frameon=False, loc="upper left", fontsize=8.0, ncol=2)

    # Panel B: 3x3 Confusion Matrix of Human M&E Reviewer vs Automated Rubric Judge
    conf = np.array(hitl_summary["judge_confusion_matrix_3x3"], dtype=int)
    im = ax2.imshow(conf, cmap="Blues", aspect="auto")
    grade_labels = ["Irrelevant (0)", "Partial (1)", "Relevant (2)"]
    ax2.set_xticks(range(3))
    ax2.set_yticks(range(3))
    ax2.set_xticklabels(grade_labels, fontsize=8.6)
    ax2.set_yticklabels(grade_labels, fontsize=8.6)
    ax2.set_xlabel("Automated Rubric Judge Grade")
    ax2.set_ylabel("M&E Specialist Human Grade")
    kq = float(hitl_summary["cohen_kappa_quadratic"])
    ku = float(hitl_summary["cohen_kappa_unweighted"])
    ax2.set_title(f"Human vs. Judge Calibration (κ_w = {kq:.2f}, κ = {ku:.2f})", loc="left", pad=10)

    max_cell = conf.max() if conf.size else 1
    for i in range(3):
        for j in range(3):
            txt_color = "#ffffff" if conf[i, j] > max_cell * 0.55 else INK
            ax2.text(j, i, str(conf[i, j]), ha="center", va="center", fontweight="bold", color=txt_color, fontsize=10.0)

    plt.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
    plt.tight_layout()
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(p)
    plt.close(fig)


def build_interactive_traces(pipeline: GovernedRAGPipeline, tuned_weights: RetrievalWeights) -> list[dict[str, object]]:
    """Export 5 rich interactive query traces for client-side GitHub Pages inspection."""
    scenarios = [
        {
            "trace_id": "rag-case-01-nbs",
            "short_label": "1. Multi-Grant NbS Synthesis",
            "badge": "ALLOWED · 3 Grants Cited",
            "query": "Compare Nature-based Solutions flood attenuation and World Bank financing informed across Pakistan, Viet Nam, and Kenya.",
            "clearance_role": "me_specialist",
            "metadata_filter": {"thematic_area": "Nature-based Solutions for Climate Resilience"},
        },
        {
            "trace_id": "rag-case-02-pii",
            "short_label": "2. 3-Layer PII Redaction Trap",
            "badge": "6 PII Spans Masked · 0% Leak",
            "query": "Report the Dadu Sindh field spot-check mortar strength result and grievance closure details under grant GFDRR-PK-2026-004.",
            "clearance_role": "me_specialist",
            "metadata_filter": {"country": "Pakistan"},
        },
        {
            "trace_id": "rag-case-03-blocked",
            "short_label": "3. WB Classification Gate Block",
            "badge": "BLOCKED · AMS 6.21A Audit Logged",
            "query": "Disclose the confidential Belize Cat DDO parametric wind-knot threshold and sovereign liquidity terms in GFDRR-BZ-2026-005.",
            "clearance_role": "public_external",
            "metadata_filter": {"country": "Belize"},
        },
        {
            "trace_id": "rag-case-04-fcv-gender",
            "short_label": "4. FCV & Gender EWS Verification",
            "badge": "Official Use · PII Masked",
            "query": "How are women's radio warning relays and solar handsets verified in Mopti under Mali Hydromet grant GFDRR-ML-2026-006?",
            "clearance_role": "wbg_analyst",
            "metadata_filter": {"country": "Mali"},
        },
        {
            "trace_id": "rag-case-05-abstain",
            "short_label": "5. Out-of-Scope Abstention Gate",
            "badge": "ABSTAINED · Below Confidence Gate",
            "query": "What sovereign geothermal catastrophe bond coupon spread was priced for Iceland's Reykjanes volcanic rift in FY2028?",
            "clearance_role": "me_specialist",
            "metadata_filter": {},
        },
    ]

    exported: list[dict[str, object]] = []
    for sc in scenarios:
        resp = pipeline.query(
            str(sc["query"]),
            clearance_role=str(sc["clearance_role"]),
            metadata_filter=dict(sc["metadata_filter"]),  # type: ignore[arg-type]
            weights=tuned_weights,
            include_synthetic_demo_diff=True,
        )
        verify_check = pipeline.provenance_signer.verify_manifest(
            resp.provenance, answer_text=resp.synthesis.answer_text
        )
        exported.append(
            {
                "trace_id": sc["trace_id"],
                "short_label": sc["short_label"],
                "badge": sc["badge"],
                "query": resp.query,
                "clearance_role": resp.clearance_role,
                "classification_verdict": resp.classification_verdict.model_dump(),
                "abstained": resp.abstained,
                "abstention_reason": resp.abstention_reason,
                "max_confidence": resp.max_confidence,
                "latency_ms": resp.latency_ms,
                "checkpoint_redaction_counts": resp.checkpoint_redaction_counts,
                "total_pii_redactions": resp.total_pii_redactions,
                "zero_pii_leakage_verified": resp.zero_pii_leakage_verified,
                "retrieved_chunks": [
                    {
                        "chunk_id": ev.chunk_id,
                        "doc_id": ev.doc_id,
                        "grant_id": ev.grant_id,
                        "country": ev.country,
                        "thematic_area": ev.thematic_area,
                        "wb_classification": ev.wb_classification,
                        "section_id": ev.section_id,
                        "section_title": ev.section_title,
                        "page_ref": ev.page_ref,
                        "bm25_score": ev.bm25_score,
                        "dense_score": ev.dense_score,
                        "rrf_score": ev.rrf_score,
                        "rerank_score": ev.rerank_score,
                        "redacted_text": ev.redacted_text,
                        "chunk_sha256": ev.chunk_sha256,
                    }
                    for ev in resp.retrieved_evidence
                ],
                "redaction_diffs": [d.model_dump() for d in resp.redaction_diffs],
                "answer_text": resp.synthesis.answer_text,
                "citation_precision": resp.synthesis.citation_precision,
                "provenance": resp.provenance.model_dump(),
                "verification": verify_check.model_dump(),
            }
        )
    return exported


def main() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_IMG_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_TRACES_DIR.mkdir(parents=True, exist_ok=True)

    pipeline = GovernedRAGPipeline(RAGConfig())
    hitl_store = HITLFeedbackStore()
    hitl_summary = run_hitl_feedback_experiment(pipeline=pipeline, store=hitl_store)
    tuned_weights = RetrievalWeights.model_validate(hitl_summary["production_tuned_weights"])

    ablation_rows = run_four_way_ablation(pipeline)
    # Also evaluate Config D with production HITL-tuned weights for full reporting
    queries = load_benchmark_queries()
    tuned_eval = evaluate_mode(
        pipeline,
        queries,
        retrieval_mode="hybrid_rrf_rerank",
        weights=tuned_weights,
        label="(E) Hybrid RRF + Reranker + Filter (HITL-Tuned)",
    )
    all_rows = [*ablation_rows, tuned_eval]

    # Save CSVs
    csv_fields = [
        "config_label",
        "retrieval_mode",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "citation_precision",
        "abstention_f1",
        "pii_redaction_recall",
        "classification_compliance_rate",
        "p95_latency_ms",
        "mean_latency_ms",
    ]
    with (OUTPUTS_DIR / "ablation_metrics.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row[k] for k in csv_fields})

    with (OUTPUTS_DIR / "error_decomposition.csv").open("w", encoding="utf-8", newline="") as fh:
        decomp_fields = [
            "config_label",
            "retrieval_hit_synthesis_complete",
            "retrieval_hit_synthesis_partial",
            "retrieval_miss_abstained",
            "retrieval_miss_off_target",
        ]
        writer = csv.DictWriter(fh, fieldnames=decomp_fields)
        writer.writeheader()
        for row in all_rows:
            d = row["error_decomposition"]  # type: ignore[index]
            writer.writerow({"config_label": row["config_label"], **d})  # type: ignore[arg-type]

    (OUTPUTS_DIR / "hitl_tuning_summary.json").write_text(
        json.dumps(hitl_summary, indent=2), encoding="utf-8"
    )

    # Render charts to both project outputs/ and portfolio assets/img/
    plot_ablation_and_hitl(
        ablation_rows,
        hitl_summary,
        [OUTPUTS_DIR / "fig_rag_ablation_hitl.png", ASSETS_IMG_DIR / "gfdrr-rag-ablation.png"],
    )
    plot_error_decomposition_and_kappa(
        ablation_rows,
        hitl_summary,
        [OUTPUTS_DIR / "fig_rag_error_kappa.png", ASSETS_IMG_DIR / "gfdrr-rag-diagnostics.png"],
    )

    # Export interactive static traces
    traces = build_interactive_traces(pipeline, tuned_weights)
    trace_bundle = {
        "generated_by": "projects/08-gfdrr-me-governed-rag/scripts/run_benchmarks_and_export.py",
        "ablation_summary": [
            {k: row[k] for k in csv_fields} for row in all_rows
        ],
        "hitl_summary": hitl_summary,
        "traces": traces,
    }
    for target in (
        OUTPUTS_DIR / "rag_interactive_traces.json",
        ASSETS_TRACES_DIR / "rag_interactive_traces.json",
    ):
        target.write_text(json.dumps(trace_bundle, indent=2), encoding="utf-8")

    print("=== PROJECT 08 BENCHMARK SUMMARY ===")
    for row in all_rows:
        print(
            f"{row['config_label']:46s} | R@5={row['recall_at_5']:.3f} | "
            f"MRR={row['mrr']:.3f} | nDCG@5={row['ndcg_at_5']:.3f} | "
            f"AbstainF1={row['abstention_f1']:.3f} | PII={row['pii_redaction_recall']:.3f} | "
            f"Cls={row['classification_compliance_rate']:.3f} | P95={row['p95_latency_ms']:.2f}ms"
        )
    print("\n=== HITL TUNING & JUDGE AGREEMENT ===")
    print(json.dumps({k: v for k, v in hitl_summary.items() if "weights" not in k}, indent=2))


if __name__ == "__main__":
    main()
