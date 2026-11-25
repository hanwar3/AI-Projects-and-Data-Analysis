"""Multi-layered dynamic PII redaction guardrail across chunks, snippets, and generated output."""

from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

CheckpointName = Literal[
    "checkpoint_a_retrieved_chunks",
    "checkpoint_b_citation_snippets",
    "checkpoint_c_generated_output",
]


class RedactionSpan(BaseModel):
    """Single detected and masked PII occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pii_type: str
    replacement_token: str
    checkpoint: CheckpointName
    original_preview: str


class CheckpointRedactionResult(BaseModel):
    """Result of running PII redaction at one checkpoint."""

    model_config = ConfigDict(extra="forbid")

    checkpoint: CheckpointName
    original_text: str
    redacted_text: str
    redaction_count: int
    counts_by_type: dict[str, int] = Field(default_factory=dict)
    spans: list[RedactionSpan] = Field(default_factory=list)


class MultiLayerPIIGuardrail:
    """Typed dynamic PII detector & redactor across 3 pipeline checkpoints."""

    _ROLE_PREFIX_PERSON = re.compile(
        r"\b(Senior Verification Engineer|Regional Hydromet Coordinator|Civil Protection Liaison|"
        r"Biomedical Resilience Specialist|Third-party field monitor|Field Survey Lead|"
        r"Field Coordinator|Beneficiary Claimant|Contact Person)\s+"
        r"((?:Dr\.\s+)?[A-Z][a-z]+(?:\s+(?:Mint|bin|bint|de|al))?(?:[\s\-][A-Z][a-z]+){1,3})\b"
    )

    # Bounded comma-clause street/compound/parcel/flat address detector (never crosses sentence clauses or brackets)
    _ADDRESS_RE = re.compile(
        r"\b(?:"
        r"(?:House|Flat|Plot|Parcelle|Compound|Block|Ilot)\s+[A-Za-z0-9\-]+"
        r"(?:,\s*(?:Lot\s+[A-Za-z0-9\-]+|Block\s+[A-Za-z0-9\-]+|Street\s+[A-Za-z0-9\-]+|"
        r"Rue\s+[A-Za-z0-9\-' ]+|Avenue\s+[A-Za-z0-9\-' ]+|Satellite Town|Gulshan-e-Iqbal|"
        r"Tevragh-Zeina|Wadajir District|Dadu|Sindh|Karachi|Mopti|Nouakchott|Mogadishu|"
        r"Port-au-Prince|[A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+)?)){1,4}"
        r"|\d{1,5}\s+[A-Z][A-Za-z0-9.\- ]+\s(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Lane)"
        r"(?:,\s*[A-Z][A-Za-z\- ]+){1,3}"
        r")"
    )

    _PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "EMAIL",
            "[REDACTED_EMAIL]",
            re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        ),
        (
            "ID",
            "[REDACTED_ID]",
            re.compile(
                r"\b(?:"
                r"CNIC\s*\d{5}-\d{7}-\d"
                r"|NID-[A-Z]{2}-\d{6,10}"
                r"|CIN-[A-Z]{2}-\d{8,12}"
                r"|BEN-ID-[A-Z]{2,3}-(?:\d{4}-)?\d{5,8}"
                r"|SSN\s*\d{3}-\d{2}-\d{4}"
                r")\b"
            ),
        ),
        (
            "PHONE",
            "[REDACTED_PHONE]",
            re.compile(r"(?<!\w)\+\d{1,3}(?:[\s\-]?\d{2,4}){2,4}\b"),
        ),
        (
            "ADDRESS",
            "[REDACTED_ADDRESS]",
            _ADDRESS_RE,
        ),
        (
            "PERSON",
            "[REDACTED_PERSON]",
            re.compile(r"\b(?:Dr\.|Mr\.|Ms\.|Mrs\.)\s+[A-Z][a-z]+(?:[\s\-][A-Z][a-z]+){1,3}\b"),
        ),
    )

    def redact(self, text: str, checkpoint: CheckpointName) -> CheckpointRedactionResult:
        working = text
        counts: dict[str, int] = {}
        spans: list[RedactionSpan] = []

        def _replace_role_person(match: re.Match[str]) -> str:
            role_label = match.group(1)
            person_name = match.group(2)
            counts["PERSON"] = counts.get("PERSON", 0) + 1
            spans.append(
                RedactionSpan(
                    pii_type="PERSON",
                    replacement_token="[REDACTED_PERSON]",
                    checkpoint=checkpoint,
                    original_preview=self._safe_preview(person_name),
                )
            )
            return f"{role_label} [REDACTED_PERSON]"

        working = self._ROLE_PREFIX_PERSON.sub(_replace_role_person, working)

        for pii_type, token, pattern in self._PATTERNS:
            def _repl(match: re.Match[str], _t: str = pii_type, _tok: str = token) -> str:
                matched_str = match.group(0)
                if "[REDACTED_" in matched_str:
                    return matched_str
                counts[_t] = counts.get(_t, 0) + 1
                spans.append(
                    RedactionSpan(
                        pii_type=_t,
                        replacement_token=_tok,
                        checkpoint=checkpoint,
                        original_preview=self._safe_preview(matched_str),
                    )
                )
                return _tok

            working = pattern.sub(_repl, working)

        return CheckpointRedactionResult(
            checkpoint=checkpoint,
            original_text=text,
            redacted_text=working,
            redaction_count=sum(counts.values()),
            counts_by_type=counts,
            spans=spans,
        )

    @staticmethod
    def _safe_preview(raw_val: str) -> str:
        trimmed = raw_val.strip()
        if len(trimmed) <= 6:
            return "***"
        return f"{trimmed[:3]}…{trimmed[-3:]}"

    def contains_unredacted_pii(self, text: str) -> list[str]:
        leaked: list[str] = []
        if self._ROLE_PREFIX_PERSON.search(text):
            leaked.append("PERSON")
        for pii_type, _, pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                if "[REDACTED_" not in m.group(0):
                    leaked.append(pii_type)
                    break
        return sorted(set(leaked))
