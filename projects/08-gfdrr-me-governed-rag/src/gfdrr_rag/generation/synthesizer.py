"""Dual-mode Citation-Grounded M&E Synthesizer and Citation Verifier."""

from __future__ import annotations

import re
import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..retrieval.bm25 import tokenize_lexical


class RedactedEvidenceItem(BaseModel):
    """Retrieved chunk after Checkpoint A (text) and Checkpoint B (snippet) PII redaction."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    doc_id: str
    grant_id: str
    title: str
    country: str
    region: str
    thematic_area: str
    wb_classification: str
    section_id: str
    section_title: str
    page_ref: str
    redacted_text: str
    redacted_snippet: str
    bm25_score: float
    dense_score: float
    rrf_score: float
    rerank_score: float
    doc_sha256: str
    chunk_sha256: str


class SynthesisResult(BaseModel):
    """Generated answer with verified inline citations."""

    model_config = ConfigDict(extra="forbid")

    answer_text: str
    cited_chunk_ids: list[str] = Field(default_factory=list)
    total_citations: int = 0
    verified_citations: int = 0
    citation_precision: float = 1.0


_CITATION_RE = re.compile(r"\[(DOC-GFDRR-\d{3})\s+§(S\d+\.\d+),\s*(p\.\d+(?:-\d+)?)\]")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class GroundedSynthesizer:
    """Synthesizes citation-backed M&E answers in deterministic_local or live API mode."""

    def __init__(
        self,
        provider_mode: str = "deterministic_local",
        anthropic_api_key: str | None = None,
        openai_api_key: str | None = None,
    ) -> None:
        self.provider_mode = provider_mode
        self.anthropic_api_key = anthropic_api_key
        self.openai_api_key = openai_api_key

    def synthesize_abstention(
        self,
        reason: str,
        blocked_classification: bool = False,
        clearance_role: str = "public_external",
        max_tier: str = "Public",
    ) -> SynthesisResult:
        if blocked_classification:
            msg = (
                f"ABSTAINED (WB AMS 6.21A Classification Policy Gate): Request cannot be fulfilled "
                f"under clearance role '{clearance_role}' (authorized up to '{max_tier}'). {reason} "
                f"This access attempt has been recorded in the immutable classification audit log."
            )
        else:
            msg = (
                f"ABSTAINED (Insufficient Corpus Evidence): No authorized GFDRR 2026-2030 corpus "
                f"records met the minimum grounding threshold. {reason} No speculative figures or "
                f"unverified claims were generated."
            )
        return SynthesisResult(
            answer_text=msg,
            cited_chunk_ids=[],
            total_citations=0,
            verified_citations=0,
            citation_precision=1.0,
        )

    def synthesize(
        self,
        query: str,
        evidence: list[RedactedEvidenceItem],
    ) -> SynthesisResult:
        if not evidence:
            return self.synthesize_abstention("No evidence chunks supplied.")

        if self.provider_mode == "live" and (self.anthropic_api_key or self.openai_api_key):
            raw_answer = self._generate_live(query, evidence)
        else:
            raw_answer = self._generate_deterministic(query, evidence)

        return self.verify_citations(raw_answer, evidence)

    def _generate_deterministic(
        self,
        query: str,
        evidence: list[RedactedEvidenceItem],
    ) -> str:
        q_tokens = set(tokenize_lexical(query))
        selected_items = [
            ev for ev in evidence[:4] if ev.rerank_score >= max(0.18, evidence[0].rerank_score * 0.52)
        ]
        if not selected_items:
            selected_items = evidence[:2]

        bullets: list[str] = []
        seen_chunks: set[str] = set()
        for ev in selected_items:
            if ev.chunk_id in seen_chunks:
                continue
            seen_chunks.add(ev.chunk_id)

            sentences = [s.strip() for s in _SENT_SPLIT_RE.split(ev.redacted_text) if s.strip()]
            # Rank sentences by query lexical overlap + numeric M&E density
            scored_sents: list[tuple[float, int, str]] = []
            for idx, sent in enumerate(sentences):
                s_toks = set(tokenize_lexical(sent))
                overlap = len(q_tokens & s_toks)
                has_metric = 1.0 if re.search(r"\d+(?:\.\d+)?%|\$\d+|\b\d{1,3}(?:,\d{3})+\b", sent) else 0.0
                scored_sents.append((overlap * 2.0 + has_metric, -idx, sent))
            scored_sents.sort(reverse=True)
            best_sents = [item[2] for item in sorted(scored_sents[:2], key=lambda x: -x[1])]
            concise = " ".join(best_sents)
            cite_tag = f"[{ev.doc_id} §{ev.section_id}, {ev.page_ref}]"
            bullets.append(
                f"- **{ev.country} — {ev.grant_id} ({ev.section_title}):** {concise} {cite_tag}"
            )

        lead = (
            f"Grounded synthesis across {len(bullets)} verified GFDRR M&E evidence section(s):"
        )
        return "\n".join([lead, *bullets])

    def _generate_live(self, query: str, evidence: list[RedactedEvidenceItem]) -> str:
        ctx_blocks = [
            f"SOURCE {ev.doc_id} §{ev.section_id} ({ev.page_ref}) | Grant {ev.grant_id} | {ev.country}:\n{ev.redacted_text}"
            for ev in evidence[:4]
        ]
        system_prompt = (
            "You are a World Bank / GFDRR M&E synthesis engine. Answer strictly using the provided "
            "redacted evidence blocks. Every bullet MUST end with an exact citation in the format "
            "[DOC-GFDRR-XXX §S1.0, p.1-3]. Never invent numbers or extrapolate beyond the text."
        )
        user_prompt = f"Query: {query}\n\nEvidence:\n" + "\n\n".join(ctx_blocks)

        if self.anthropic_api_key:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-3-5-sonnet-latest",
                    "max_tokens": 600,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return str(resp.json()["content"][0]["text"])

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.openai_api_key}"},
            json={
                "model": "gpt-4o-mini",
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])

    @staticmethod
    def verify_citations(
        answer_text: str, evidence: list[RedactedEvidenceItem]
    ) -> SynthesisResult:
        ev_lookup = {(ev.doc_id, ev.section_id): ev for ev in evidence}
        matches = list(_CITATION_RE.finditer(answer_text))
        if not matches:
            return SynthesisResult(
                answer_text=answer_text,
                cited_chunk_ids=[],
                total_citations=0,
                verified_citations=0,
                citation_precision=0.0,
            )

        verified = 0
        cited_ids: list[str] = []
        for match in matches:
            doc_id, sec_id, page_ref = match.group(1), match.group(2), match.group(3)
            ev = ev_lookup.get((doc_id, sec_id))
            if ev is not None and ev.page_ref == page_ref:
                verified += 1
                if ev.chunk_id not in cited_ids:
                    cited_ids.append(ev.chunk_id)

        precision = verified / len(matches)
        return SynthesisResult(
            answer_text=answer_text,
            cited_chunk_ids=cited_ids,
            total_citations=len(matches),
            verified_citations=verified,
            citation_precision=round(precision, 4),
        )
