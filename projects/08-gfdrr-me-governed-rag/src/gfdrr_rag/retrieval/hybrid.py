"""Multi-stage Hybrid Retriever: Metadata Pre-Filter + BM25 + Dense + RRF + Reranker + Abstention Gate."""

from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict

from ..config import RetrievalWeights
from ..ingestion.chunker import IngestedChunk
from .bm25 import BM25Index, tokenize_lexical
from .dense import DenseSemanticIndex, enrich_semantic_text


class ScoredChunk(BaseModel):
    """Retrieved chunk with transparent stage-by-stage retrieval scores."""

    model_config = ConfigDict(extra="forbid")

    chunk: IngestedChunk
    bm25_score: float
    bm25_rank: int
    dense_score: float
    dense_rank: int
    rrf_score: float
    rerank_score: float


class RetrievalBundle(BaseModel):
    """Output of hybrid retrieval including confidence and abstention gate decision."""

    model_config = ConfigDict(extra="forbid")

    retrieval_mode: str
    results: list[ScoredChunk]
    max_confidence: float
    abstained: bool
    abstention_reason: str | None = None


_SECTION_OUTCOME_CUES = re.compile(
    r"\b(target\w*|beneficiar\w*|indicator\w*|rf-2030|result\w*|compliance|pass-rate|payout|lead-time|uptime|reduction|drop\w*|inform\w*|financing|million|\$\d+)\b",
    re.I,
)
_SECTION_SAFEGUARD_CUES = re.compile(
    r"\b(spot-check|grievance|safeguard\w*|saddd|women\w*|gender|disability|relay\w*|depot|siren\w*|calibration|audit|esf|accessibility)\b",
    re.I,
)

# Proper-noun words that commonly appear capitalized in queries without denoting an out-of-scope country/entity
_KNOWN_QUERY_PROPER_ALLOWLIST = frozenset(
    {
        "world", "bank", "gfdrr", "grade", "cat", "ddo", "cprt", "saddd", "fcv",
        "nbs", "ews", "hydromet", "digital", "earth", "nature-based", "solutions",
        "disaster", "risk", "finance", "urban", "heat", "resilience", "gender",
        "substantive", "departmental", "strictly", "confidential", "official",
        "southeast", "asian", "latin", "american", "central", "asia", "west",
        "africa", "eastern", "caribbean", "sahel", "mekong", "shabelle", "mopti",
        "dadu", "sindh", "karachi", "wadajir", "nairobi", "aden", "sana", "lidar",
        "insar", "sentinel-1", "admin-2", "esf",
    }
)


class HybridRetriever:
    """Executes all 4 benchmark retrieval modes and enforces entity-aware abstention gating."""

    def __init__(
        self,
        chunks: list[IngestedChunk],
        provider_mode: str = "deterministic_local",
        openai_api_key: str | None = None,
    ) -> None:
        self._chunks = chunks
        self._chunk_by_id = {c.chunk_id: c for c in chunks}
        self._known_countries = {c.country.lower() for c in chunks}
        self.bm25 = BM25Index(chunks)
        self.dense = DenseSemanticIndex(
            chunks, provider_mode=provider_mode, openai_api_key=openai_api_key
        )

    def _unmatched_proper_entities(self, query: str) -> list[str]:
        """Detect capitalized proper nouns (countries/regions/sites) absent from the corpus."""
        words = re.findall(r"[A-Za-zÀ-ÿ0-9\-']+", query)
        unmatched: list[str] = []
        for idx, raw_w in enumerate(words):
            if idx == 0:
                continue  # sentence-initial capitalization
            clean = raw_w.split("'")[0].strip("-")
            if not clean or len(clean) < 4:
                continue
            if not (clean[0].isupper() and any(ch.islower() for ch in clean[1:])):
                continue
            low = clean.lower()
            if low in _KNOWN_QUERY_PROPER_ALLOWLIST or low in self._known_countries:
                continue
            if self.bm25._df.get(low, 0) == 0:
                unmatched.append(clean)
        return unmatched

    def _query_grounding_factor(self, query: str) -> tuple[float, list[str]]:
        """Compute calibration multiplier in [0.15, 1.0] penalizing unseen proper nouns & OOV terms."""
        unmatched_entities = self._unmatched_proper_entities(query)
        q_toks = list(dict.fromkeys(tokenize_lexical(query)))
        if not q_toks:
            return 0.2, unmatched_entities

        enriched = enrich_semantic_text(query)
        has_concept_bridge = enriched != query
        in_vocab = sum(1 for t in q_toks if self.bm25._df.get(t, 0) > 0)
        vocab_cov = in_vocab / len(q_toks)

        factor = 1.0
        if unmatched_entities:
            # Explicit out-of-portfolio geography/proper entity (e.g. Iceland, Chile, Saskatchewan)
            factor *= 0.22
        if vocab_cov < 0.45 and not has_concept_bridge:
            factor *= 0.40
        elif vocab_cov < 0.35:
            factor *= 0.50
        return factor, unmatched_entities

    @staticmethod
    def _apply_metadata_filter(
        chunks: list[IngestedChunk], metadata_filter: dict[str, str]
    ) -> list[IngestedChunk]:
        if not metadata_filter:
            return chunks
        filtered: list[IngestedChunk] = []
        for c in chunks:
            ok = True
            for key, val in metadata_filter.items():
                if not val:
                    continue
                if key == "country" and c.country.lower() != val.lower():
                    ok = False
                elif key == "region" and c.region.lower() != val.lower():
                    ok = False
                elif key == "thematic_area" and c.thematic_area.lower() != val.lower():
                    ok = False
                elif key == "priority_area" and c.priority_area.lower() != val.lower():
                    ok = False
                elif key == "crosscutting_tag" and val not in c.crosscutting_tags:
                    ok = False
            if ok:
                filtered.append(c)
        return filtered

    @staticmethod
    def _ranks_from_scores(scores: dict[str, float]) -> dict[str, int]:
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return {cid: idx + 1 for idx, (cid, _) in enumerate(ordered)}

    def _cross_feature_rerank(
        self,
        query: str,
        chunk: IngestedChunk,
        norm_rrf: float,
        norm_bm25: float,
        dense_sim: float,
        metadata_filter: dict[str, str],
        weights: RetrievalWeights,
    ) -> float:
        q_toks = set(tokenize_lexical(query))
        c_toks = set(
            tokenize_lexical(f"{chunk.grant_id} {chunk.country} {chunk.section_title} {chunk.raw_text}")
        )
        overlap = len(q_toks & c_toks) / max(len(q_toks), 1)

        sec_bonus = 0.0
        if _SECTION_OUTCOME_CUES.search(query) and chunk.section_id in ("S2.0", "S3.0"):
            sec_bonus += 0.65
        if _SECTION_SAFEGUARD_CUES.search(query) and chunk.section_id == "S4.0":
            sec_bonus += 0.75
        if chunk.grant_id.lower() in query.lower():
            sec_bonus += 0.35

        meta_bonus = 0.0
        if chunk.country.lower() in query.lower():
            meta_bonus += 0.60
        if metadata_filter:
            matched_keys = sum(
                1
                for k, v in metadata_filter.items()
                if (k == "country" and chunk.country.lower() == v.lower())
                or (k == "thematic_area" and chunk.thematic_area.lower() == v.lower())
                or (k == "region" and chunk.region.lower() == v.lower())
            )
            if matched_keys > 0:
                meta_bonus += 0.40

        lexical_signal = 0.6 * norm_bm25 + 0.4 * overlap
        raw_rerank = (
            weights.rerank_rrf_w * norm_rrf
            + weights.rerank_lexical_w * lexical_signal
            + weights.rerank_semantic_w * dense_sim
            + weights.rerank_section_w * min(sec_bonus, 1.0)
            + weights.rerank_metadata_w * min(meta_bonus, 1.0)
        )
        weight_sum = max(
            weights.rerank_rrf_w
            + weights.rerank_lexical_w
            + weights.rerank_semantic_w
            + weights.rerank_section_w
            + weights.rerank_metadata_w,
            1e-9,
        )
        return float(raw_rerank / weight_sum)

    def retrieve(
        self,
        query: str,
        authorized_chunks: list[IngestedChunk],
        *,
        retrieval_mode: str = "hybrid_rrf_rerank",
        metadata_filter: dict[str, str] | None = None,
        top_k: int = 5,
        abstention_threshold: float = 0.21,
        weights: RetrievalWeights | None = None,
    ) -> RetrievalBundle:
        w = weights or RetrievalWeights()
        meta_filter = metadata_filter or {}

        if not authorized_chunks:
            return RetrievalBundle(
                retrieval_mode=retrieval_mode,
                results=[],
                max_confidence=0.0,
                abstained=True,
                abstention_reason="Zero authorized chunks available after WB Classification Policy Gate.",
            )

        candidate_chunks = (
            self._apply_metadata_filter(authorized_chunks, meta_filter)
            if retrieval_mode == "hybrid_rrf_rerank"
            else authorized_chunks
        )
        if not candidate_chunks:
            return RetrievalBundle(
                retrieval_mode=retrieval_mode,
                results=[],
                max_confidence=0.0,
                abstained=True,
                abstention_reason="No authorized chunks matched requested metadata scope.",
            )

        grounding_factor, unmatched_entities = self._query_grounding_factor(query)

        allowed_ids = {c.chunk_id for c in candidate_chunks}
        bm25_scores = self.bm25.score_subset(query, allowed_ids)
        dense_scores = self.dense.score_subset(query, allowed_ids)

        bm25_ranks = self._ranks_from_scores(bm25_scores)
        dense_ranks = self._ranks_from_scores(dense_scores)

        max_bm25 = max(bm25_scores.values(), default=0.0)
        max_dense = max(dense_scores.values(), default=0.0)
        query_anchor = max(min(max_bm25 / 7.5, 1.0), max_dense)

        rrf_scores: dict[str, float] = {}
        for cid in allowed_ids:
            b_contrib = (
                w.w_bm25 / (w.rrf_k + bm25_ranks[cid]) if bm25_scores[cid] > 0.0 else 0.0
            )
            d_contrib = (
                w.w_dense / (w.rrf_k + dense_ranks[cid]) if dense_scores[cid] > 0.05 else 0.0
            )
            rrf_scores[cid] = b_contrib + d_contrib

        max_rrf = max(rrf_scores.values(), default=0.0)

        scored: list[ScoredChunk] = []
        for cid in allowed_ids:
            chunk = self._chunk_by_id[cid]
            b_val = bm25_scores[cid]
            d_val = dense_scores[cid]
            r_val = rrf_scores[cid]
            norm_b = (b_val / max_bm25) if max_bm25 > 1e-9 else 0.0
            norm_r = (r_val / max_rrf) if max_rrf > 1e-9 else 0.0

            if retrieval_mode == "dense_only":
                final_score = d_val * grounding_factor
            elif retrieval_mode == "bm25_only":
                final_score = min(b_val / 12.0, 1.0) * grounding_factor
            elif retrieval_mode == "hybrid_rrf":
                unweighted_rrf = (
                    (1.0 / (60 + bm25_ranks[cid]) if b_val > 0.0 else 0.0)
                    + (1.0 / (60 + dense_ranks[cid]) if d_val > 0.05 else 0.0)
                )
                final_score = float(
                    min((unweighted_rrf * 30.5) * (0.35 + 0.65 * query_anchor), 1.0)
                    * grounding_factor
                )
                r_val = unweighted_rrf
            else:
                base_rerank = self._cross_feature_rerank(
                    query=query,
                    chunk=chunk,
                    norm_rrf=norm_r,
                    norm_bm25=norm_b,
                    dense_sim=d_val,
                    metadata_filter=meta_filter,
                    weights=w,
                )
                final_score = float(base_rerank * (0.25 + 0.75 * query_anchor) * grounding_factor)

            scored.append(
                ScoredChunk(
                    chunk=chunk,
                    bm25_score=round(b_val, 4),
                    bm25_rank=bm25_ranks[cid],
                    dense_score=round(d_val, 4),
                    dense_rank=dense_ranks[cid],
                    rrf_score=round(r_val, 5),
                    rerank_score=round(final_score, 4),
                )
            )

        scored.sort(key=lambda item: (-item.rerank_score, -item.rrf_score, item.chunk.chunk_id))
        top_results = scored[:top_k]
        max_conf = top_results[0].rerank_score if top_results else 0.0

        top_bm25 = top_results[0].bm25_score if top_results else 0.0
        top_dense = top_results[0].dense_score if top_results else 0.0
        insufficient_signal = (max_conf < abstention_threshold) or (
            top_bm25 < 2.2 and top_dense < 0.24
        )

        if insufficient_signal:
            entity_note = (
                f" Unrecognized entity/geography outside GFDRR corpus: {', '.join(unmatched_entities)}."
                if unmatched_entities
                else ""
            )
            return RetrievalBundle(
                retrieval_mode=retrieval_mode,
                results=top_results,
                max_confidence=round(max_conf, 4),
                abstained=True,
                abstention_reason=(
                    f"Calibrated grounding confidence ({max_conf:.3f}) below abstention threshold "
                    f"({abstention_threshold:.2f}).{entity_note}"
                ),
            )

        return RetrievalBundle(
            retrieval_mode=retrieval_mode,
            results=top_results,
            max_confidence=round(max_conf, 4),
            abstained=False,
            abstention_reason=None,
        )
