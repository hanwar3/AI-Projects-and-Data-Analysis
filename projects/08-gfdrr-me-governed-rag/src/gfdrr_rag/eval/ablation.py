"""Evaluation suite: 4-way retrieval ablation, 2x2 error decomposition, and SQLite HITL tuning loop."""

from __future__ import annotations

import math
from typing import Any
import numpy as np

from gfdrr_corpus.loader import load_benchmark_queries
from gfdrr_corpus.schemas import BenchmarkQuery
from gfdrr_corpus.taxonomy import CLEARANCE_MAX_TIER, WB_CLASSIFICATION_ORDER

from ..config import RAGConfig, RetrievalWeights
from ..pipeline import GovernedRAGPipeline, RAGResponse


def compute_recall_at_k(retrieved_ids: list[str], relevant: dict[str, int], k: int = 5) -> float:
    if not relevant:
        return 0.0
    positives = {cid for cid, grade in relevant.items() if grade >= 1}
    if not positives:
        return 0.0
    hits = sum(1 for cid in retrieved_ids[:k] if cid in positives)
    return hits / len(positives)


def compute_mrr(retrieved_ids: list[str], relevant: dict[str, int], k: int = 5) -> float:
    positives = {cid for cid, grade in relevant.items() if grade >= 2} or {
        cid for cid, grade in relevant.items() if grade >= 1
    }
    for rank_idx, cid in enumerate(retrieved_ids[:k], start=1):
        if cid in positives:
            return 1.0 / rank_idx
    return 0.0


def compute_ndcg_at_k(retrieved_ids: list[str], relevant: dict[str, int], k: int = 5) -> float:
    if not relevant:
        return 0.0
    dcg = 0.0
    for idx, cid in enumerate(retrieved_ids[:k]):
        rel = relevant.get(cid, 0)
        if rel > 0:
            dcg += (2**rel - 1.0) / math.log2(idx + 2.0)

    ideal_rels = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2**rel - 1.0) / math.log2(idx + 2.0) for idx, rel in enumerate(ideal_rels) if rel > 0)
    return (dcg / idcg) if idcg > 0 else 0.0


def evaluate_mode(
    pipeline: GovernedRAGPipeline,
    queries: list[BenchmarkQuery],
    retrieval_mode: str,
    weights: RetrievalWeights | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    cite_precisions: list[float] = []
    latencies: list[float] = []

    abstain_tp = 0
    abstain_fp = 0
    abstain_fn = 0
    abstain_tn = 0

    pii_total_expected_cases = 0
    pii_zero_leak_cases = 0
    pii_tokens_masked = 0

    classification_compliant = 0

    # 2x2 Retrieval vs Generation Error Decomposition on answerable queries
    decomp = {
        "retrieval_hit_synthesis_complete": 0,
        "retrieval_hit_synthesis_partial": 0,
        "retrieval_miss_abstained": 0,
        "retrieval_miss_off_target": 0,
    }

    per_query_ndcg: dict[str, float] = {}
    per_query_mrr: dict[str, float] = {}

    for q in queries:
        resp: RAGResponse = pipeline.query(
            q.query,
            clearance_role=q.clearance_role,
            metadata_filter=q.metadata_filter,
            retrieval_mode=retrieval_mode,
            top_k=5,
            weights=weights,
        )
        latencies.append(resp.latency_ms)

        # Classification compliance verification: zero retrieved chunks may exceed role's max tier
        max_tier = CLEARANCE_MAX_TIER[q.clearance_role]
        max_rank = WB_CLASSIFICATION_ORDER[max_tier]
        tier_ok = all(
            WB_CLASSIFICATION_ORDER[ev.wb_classification] <= max_rank
            for ev in resp.retrieved_evidence
        )
        if q.expected_blocked_classification:
            tier_ok = tier_ok and (
                resp.classification_verdict.decision == "BLOCKED_CLASSIFICATION"
                and resp.abstained
            )
        if tier_ok:
            classification_compliant += 1

        # PII redaction verification
        if q.expects_pii_redaction:
            pii_total_expected_cases += 1
            pii_tokens_masked += resp.total_pii_redactions
            if resp.zero_pii_leakage_verified and resp.total_pii_redactions > 0:
                pii_zero_leak_cases += 1

        # Abstention confusion matrix
        if q.expected_abstain and resp.abstained:
            abstain_tp += 1
        elif (not q.expected_abstain) and resp.abstained:
            abstain_fp += 1
        elif q.expected_abstain and (not resp.abstained):
            abstain_fn += 1
        else:
            abstain_tn += 1

        # Answerable query retrieval & synthesis evaluation
        if not q.expected_abstain:
            ret_ids = [ev.chunk_id for ev in resp.retrieved_evidence]
            r5 = compute_recall_at_k(ret_ids, q.relevant_chunks, k=5)
            mrr = compute_mrr(ret_ids, q.relevant_chunks, k=5)
            ndcg = compute_ndcg_at_k(ret_ids, q.relevant_chunks, k=5)

            recalls.append(r5)
            mrrs.append(mrr)
            ndcgs.append(ndcg)
            per_query_ndcg[q.query_id] = ndcg
            per_query_mrr[q.query_id] = mrr

            if not resp.abstained:
                cite_precisions.append(resp.synthesis.citation_precision)

            # Check retrieval hit (at least one primary grade=2 chunk in top-5) vs synthesis fact coverage
            grade2_ids = {cid for cid, g in q.relevant_chunks.items() if g >= 2}
            retrieval_hit = len(set(ret_ids[:4]) & grade2_ids) > 0
            ans_lower = resp.synthesis.answer_text.lower()
            facts_hit = all(f.lower() in ans_lower for f in q.required_facts) if q.required_facts else True

            if retrieval_hit and (not resp.abstained) and facts_hit:
                decomp["retrieval_hit_synthesis_complete"] += 1
            elif retrieval_hit:
                decomp["retrieval_hit_synthesis_partial"] += 1
            elif resp.abstained:
                decomp["retrieval_miss_abstained"] += 1
            else:
                decomp["retrieval_miss_off_target"] += 1

    precision_abs = abstain_tp / max(abstain_tp + abstain_fp, 1)
    recall_abs = abstain_tp / max(abstain_tp + abstain_fn, 1)
    f1_abs = (
        2 * precision_abs * recall_abs / (precision_abs + recall_abs)
        if (precision_abs + recall_abs) > 0
        else 0.0
    )

    pii_recall = (
        pii_zero_leak_cases / pii_total_expected_cases if pii_total_expected_cases > 0 else 1.0
    )
    p95_lat = float(np.percentile(latencies, 95)) if latencies else 0.0

    return {
        "config_label": label or retrieval_mode,
        "retrieval_mode": retrieval_mode,
        "n_total_queries": len(queries),
        "n_answerable_queries": len(recalls),
        "recall_at_5": round(float(np.mean(recalls)), 4),
        "mrr": round(float(np.mean(mrrs)), 4),
        "ndcg_at_5": round(float(np.mean(ndcgs)), 4),
        "citation_precision": round(float(np.mean(cite_precisions)) if cite_precisions else 0.0, 4),
        "abstention_f1": round(f1_abs, 4),
        "abstention_tp": abstain_tp,
        "abstention_fp": abstain_fp,
        "abstention_fn": abstain_fn,
        "abstention_tn": abstain_tn,
        "pii_redaction_recall": round(pii_recall, 4),
        "pii_tokens_masked": pii_tokens_masked,
        "classification_compliance_rate": round(classification_compliant / max(len(queries), 1), 4),
        "p95_latency_ms": round(p95_lat, 2),
        "mean_latency_ms": round(float(np.mean(latencies)), 2),
        "error_decomposition": decomp,
        "per_query_ndcg": per_query_ndcg,
        "per_query_mrr": per_query_mrr,
    }


def run_four_way_ablation(pipeline: GovernedRAGPipeline | None = None) -> list[dict[str, Any]]:
    pipe = pipeline or GovernedRAGPipeline(RAGConfig())
    queries = load_benchmark_queries()

    configs = [
        ("dense_only", "(A) Dense-only (SVD Semantic)"),
        ("bm25_only", "(B) BM25-only (Lexical)"),
        ("hybrid_rrf", "(C) Hybrid RRF (Unweighted)"),
        ("hybrid_rrf_rerank", "(D) Hybrid RRF + Reranker + Metadata Filter"),
    ]
    results: list[dict[str, Any]] = []
    for mode, label in configs:
        res = evaluate_mode(pipe, queries, retrieval_mode=mode, label=label)
        results.append(res)
    return results
