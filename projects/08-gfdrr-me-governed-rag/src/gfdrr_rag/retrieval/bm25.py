"""Okapi BM25 lexical index preserving GFDRR grant IDs, indicator codes, and DRM terminology."""

from __future__ import annotations

from collections import Counter
import math
import re

from ..ingestion.chunker import IngestedChunk

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
        "for", "from", "how", "in", "into", "is", "it", "its", "of", "on", "or",
        "that", "the", "their", "these", "this", "to", "under", "was", "were",
        "what", "where", "which", "who", "with", "can", "are", "show", "report",
        "summarize", "provide", "disclose", "compare", "evaluate", "review",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def tokenize_lexical(text: str) -> list[str]:
    """Tokenize preserving hyphenated codes (e.g. GFDRR-PK-2026-004, RF-2030-IND-08) plus sub-parts."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        tokens.append(raw)
        if "-" in raw and not raw.startswith(("gfdrr-", "rf-2030-", "doc-gfdrr-")):
            for sub in raw.split("-"):
                if len(sub) >= 3 and sub not in _STOPWORDS:
                    tokens.append(sub)
        # Light suffix normalization for plurals
        if len(raw) > 5 and raw.endswith("ies"):
            tokens.append(raw[:-3] + "y")
        elif len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss"):
            tokens.append(raw[:-1])
    return tokens


class BM25Index:
    """Deterministic Okapi BM25 scorer over section chunks."""

    def __init__(self, chunks: list[IngestedChunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._chunk_map: dict[str, IngestedChunk] = {c.chunk_id: c for c in chunks}
        self._doc_tokens: dict[str, Counter[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._df: Counter[str] = Counter()

        for chunk in chunks:
            # BM25 indexes lexical surface text + explicit identifiers + country/title
            lexical_surface = (
                f"{chunk.doc_id} {chunk.grant_id} {chunk.country} "
                f"{chunk.section_title} {chunk.raw_text}"
            )
            toks = tokenize_lexical(lexical_surface)
            counts = Counter(toks)
            self._doc_tokens[chunk.chunk_id] = counts
            self._doc_len[chunk.chunk_id] = max(len(toks), 1)
            for term in counts:
                self._df[term] += 1

        self.n_docs = max(len(chunks), 1)
        self.avgdl = (
            sum(self._doc_len.values()) / self.n_docs if self._doc_len else 1.0
        )

    def score_subset(self, query: str, allowed_chunk_ids: set[str]) -> dict[str, float]:
        q_tokens = tokenize_lexical(query)
        scores: dict[str, float] = {cid: 0.0 for cid in allowed_chunk_ids}
        if not q_tokens:
            return scores

        for cid in allowed_chunk_ids:
            tf_map = self._doc_tokens.get(cid)
            if not tf_map:
                continue
            dl = self._doc_len[cid]
            denom_norm = self.k1 * (1.0 - self.b + self.b * (dl / self.avgdl))
            total = 0.0
            for term in q_tokens:
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))
                total += idf * ((tf * (self.k1 + 1.0)) / (tf + denom_norm))
            scores[cid] = total
        return scores
