"""Cryptographic provenance manifest (SHA-256 + HMAC-SHA256) and zero-width watermarking."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from pydantic import BaseModel, ConfigDict, Field

# Four zero-width codepoints encode 2 bits each for steganographic provenance watermarking
_ZW_ALPHABET = ("\u200b", "\u200c", "\u200d", "\u2060")
_ZW_REVERSE = {ch: idx for idx, ch in enumerate(_ZW_ALPHABET)}
_ZW_START = "\ufeff"
_ZW_END = "\u2063"


def encode_zero_width(hex_digest: str) -> str:
    """Encode an ASCII hex string into invisible zero-width Unicode characters."""
    raw_bytes = hex_digest.encode("ascii")
    chars: list[str] = [_ZW_START]
    for byte in raw_bytes:
        chars.append(_ZW_ALPHABET[(byte >> 6) & 0b11])
        chars.append(_ZW_ALPHABET[(byte >> 4) & 0b11])
        chars.append(_ZW_ALPHABET[(byte >> 2) & 0b11])
        chars.append(_ZW_ALPHABET[byte & 0b11])
    chars.append(_ZW_END)
    return "".join(chars)


def decode_zero_width(text: str) -> str | None:
    """Extract and decode the zero-width watermark payload if present."""
    start = text.find(_ZW_START)
    end = text.find(_ZW_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    zw_slice = text[start + 1 : end]
    if not zw_slice or len(zw_slice) % 4 != 0:
        return None
    out = bytearray()
    for idx in range(0, len(zw_slice), 4):
        quad = zw_slice[idx : idx + 4]
        if any(ch not in _ZW_REVERSE for ch in quad):
            return None
        val = (
            (_ZW_REVERSE[quad[0]] << 6)
            | (_ZW_REVERSE[quad[1]] << 4)
            | (_ZW_REVERSE[quad[2]] << 2)
            | _ZW_REVERSE[quad[3]]
        )
        out.append(val)
    try:
        return out.decode("ascii")
    except UnicodeDecodeError:
        return None


def strip_zero_width(text: str) -> str:
    """Remove any embedded zero-width watermark sequence from text."""
    start = text.find(_ZW_START)
    end = text.find(_ZW_END, start + 1) if start != -1 else -1
    if start != -1 and end != -1:
        return text[:start] + text[end + 1 :]
    return text


class ChunkProvenanceEntry(BaseModel):
    """Per-chunk cryptographic hash entry included in the provenance manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    doc_id: str
    grant_id: str
    section_id: str
    page_ref: str
    wb_classification: str
    doc_sha256: str
    chunk_sha256: str


class ProvenanceManifest(BaseModel):
    """HMAC-SHA256 signed provenance receipt for every Governed RAG response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str
    utc_timestamp: str
    model_version: str
    clearance_role: str
    max_classification_used: str
    abstained: bool
    query_sha256: str
    answer_sha256: str
    total_pii_redactions: int
    redactions_by_checkpoint: dict[str, int] = Field(default_factory=dict)
    cited_chunks: list[ChunkProvenanceEntry] = Field(default_factory=list)
    hmac_sha256: str


class ProvenanceVerificationResult(BaseModel):
    """Verification outcome for a signed provenance manifest or exported M&E brief."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verified: bool
    status: str
    manifest_id: str | None = None
    hmac_valid: bool = False
    body_hash_valid: bool = False
    watermark_valid: bool = False
    reason: str


class ProvenanceSigner:
    """Signs RAG outputs and embeds/verifies cryptographic watermarks in exported briefs."""

    def __init__(self, secret_key: str) -> None:
        self._secret = secret_key.encode("utf-8")

    def _canonical_payload(self, fields: dict[str, object]) -> bytes:
        return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign_manifest(
        self,
        *,
        query: str,
        answer_text: str,
        model_version: str,
        clearance_role: str,
        max_classification_used: str,
        abstained: bool,
        total_pii_redactions: int,
        redactions_by_checkpoint: dict[str, int],
        cited_chunks: list[ChunkProvenanceEntry],
        utc_timestamp: str | None = None,
    ) -> ProvenanceManifest:
        ts = utc_timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        q_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
        clean_answer = strip_zero_width(answer_text).strip()
        a_sha = hashlib.sha256(clean_answer.encode("utf-8")).hexdigest()
        manifest_id = f"PROV-{hashlib.sha256((q_sha + ts).encode('utf-8')).hexdigest()[:12].upper()}"

        unsigned = {
            "manifest_id": manifest_id,
            "utc_timestamp": ts,
            "model_version": model_version,
            "clearance_role": clearance_role,
            "max_classification_used": max_classification_used,
            "abstained": abstained,
            "query_sha256": q_sha,
            "answer_sha256": a_sha,
            "total_pii_redactions": total_pii_redactions,
            "redactions_by_checkpoint": redactions_by_checkpoint,
            "cited_chunks": [c.model_dump() for c in cited_chunks],
        }
        sig = hmac.new(self._secret, self._canonical_payload(unsigned), hashlib.sha256).hexdigest()
        return ProvenanceManifest(**unsigned, hmac_sha256=sig)

    def verify_manifest(
        self, manifest: ProvenanceManifest, answer_text: str | None = None
    ) -> ProvenanceVerificationResult:
        unsigned = manifest.model_dump(exclude={"hmac_sha256"})
        expected_sig = hmac.new(
            self._secret, self._canonical_payload(unsigned), hashlib.sha256
        ).hexdigest()
        hmac_ok = hmac.compare_digest(expected_sig, manifest.hmac_sha256)

        body_ok = True
        if answer_text is not None:
            clean_answer = strip_zero_width(answer_text).strip()
            recomputed_a_sha = hashlib.sha256(clean_answer.encode("utf-8")).hexdigest()
            body_ok = hmac.compare_digest(recomputed_a_sha, manifest.answer_sha256)

        verified = hmac_ok and body_ok
        if verified:
            reason = "Cryptographic HMAC-SHA256 signature and answer SHA-256 digest match."
            status = "VERIFIED"
        elif not hmac_ok:
            reason = "TAMPER_DETECTED: Manifest HMAC-SHA256 signature invalid."
            status = "TAMPER_DETECTED"
        else:
            reason = "TAMPER_DETECTED: Answer text modified after provenance signing."
            status = "TAMPER_DETECTED"

        return ProvenanceVerificationResult(
            verified=verified,
            status=status,
            manifest_id=manifest.manifest_id,
            hmac_valid=hmac_ok,
            body_hash_valid=body_ok,
            watermark_valid=True,
            reason=reason,
        )

    def export_watermarked_brief(
        self,
        title: str,
        query: str,
        answer_text: str,
        manifest: ProvenanceManifest,
    ) -> str:
        """Export an M&E Markdown brief carrying both a zero-width watermark and JSON manifest footer."""
        clean_answer = strip_zero_width(answer_text).strip()
        # Watermark binds answer_sha256[:24] + hmac_sha256[:24] (48 hex chars)
        wm_token = f"{manifest.answer_sha256[:24]}{manifest.hmac_sha256[:24]}"
        zw_watermark = encode_zero_width(wm_token)

        lines = [
            f"# {title}{zw_watermark}",
            "",
            f"**M&E Query:** {query}",
            f"**Clearance Scope:** `{manifest.clearance_role}` (Max Tier Used: `{manifest.max_classification_used}`)",
            "",
            "<!-- BEGIN_GOVERNED_ANSWER -->",
            clean_answer,
            "<!-- END_GOVERNED_ANSWER -->",
            "",
            "---",
            "## Cryptographic Provenance Receipt",
            "```json",
            json.dumps(manifest.model_dump(), indent=2),
            "```",
            "",
        ]
        return "\n".join(lines)

    def verify_exported_brief(self, brief_markdown: str) -> ProvenanceVerificationResult:
        """Verify an exported Markdown brief against post-generation edits or watermark stripping."""
        ans_match = re.search(
            r"<!-- BEGIN_GOVERNED_ANSWER -->\n(.*?)\n<!-- END_GOVERNED_ANSWER -->",
            brief_markdown,
            flags=re.DOTALL,
        )
        json_match = re.search(
            r"## Cryptographic Provenance Receipt\s*```json\s*(\{.*?\})\s*```",
            brief_markdown,
            flags=re.DOTALL,
        )
        if not ans_match or not json_match:
            return ProvenanceVerificationResult(
                verified=False,
                status="TAMPER_DETECTED",
                reason="Missing governed answer delimiters or JSON provenance receipt block.",
            )

        extracted_answer = ans_match.group(1).strip()
        try:
            manifest = ProvenanceManifest.model_validate_json(json_match.group(1))
        except Exception as exc:
            return ProvenanceVerificationResult(
                verified=False,
                status="TAMPER_DETECTED",
                reason=f"Malformed provenance JSON block: {exc}",
            )

        base_check = self.verify_manifest(manifest, answer_text=extracted_answer)
        decoded_wm = decode_zero_width(brief_markdown)
        expected_wm = f"{manifest.answer_sha256[:24]}{manifest.hmac_sha256[:24]}"
        wm_ok = decoded_wm is not None and hmac.compare_digest(decoded_wm, expected_wm)

        all_ok = base_check.hmac_valid and base_check.body_hash_valid and wm_ok
        if all_ok:
            status = "VERIFIED"
            reason = (
                "Brief body SHA-256, HMAC-SHA256 provenance receipt, and zero-width "
                "cryptographic watermark all verified intact."
            )
        elif not base_check.hmac_valid:
            status = "TAMPER_DETECTED"
            reason = "TAMPER_DETECTED: Provenance receipt HMAC-SHA256 failed verification."
        elif not base_check.body_hash_valid:
            status = "TAMPER_DETECTED"
            reason = "TAMPER_DETECTED: Brief body content was altered after signing."
        else:
            status = "TAMPER_DETECTED"
            reason = "TAMPER_DETECTED: Zero-width cryptographic watermark missing or corrupted."

        return ProvenanceVerificationResult(
            verified=all_ok,
            status=status,
            manifest_id=manifest.manifest_id,
            hmac_valid=base_check.hmac_valid,
            body_hash_valid=base_check.body_hash_valid,
            watermark_valid=wm_ok,
            reason=reason,
        )
