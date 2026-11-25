"""Evaluation & HITL feedback modules."""

from .ablation import (
    compute_mrr,
    compute_ndcg_at_k,
    compute_recall_at_k,
    evaluate_mode,
    run_four_way_ablation,
)
from .hitl_loop import HITLFeedbackStore, paired_bootstrap_ci, run_hitl_feedback_experiment

__all__ = [
    "HITLFeedbackStore",
    "compute_mrr",
    "compute_ndcg_at_k",
    "compute_recall_at_k",
    "evaluate_mode",
    "paired_bootstrap_ci",
    "run_four_way_ablation",
    "run_hitl_feedback_experiment",
]
