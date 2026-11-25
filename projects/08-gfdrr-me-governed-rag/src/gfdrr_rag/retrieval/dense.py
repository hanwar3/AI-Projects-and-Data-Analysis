"""Dual-mode Dense Semantic Retriever (deterministic_local SVD + concept bridge vs live API)."""

from __future__ import annotations

import re
import httpx
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from ..ingestion.chunker import IngestedChunk

# Domain semantic concept descriptors bridging operational DRM paraphrases to canonical taxonomy concepts.
# Applied identically at index and query time in deterministic_local mode.
_CONCEPT_BRIDGES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(mangrove\w*|bioshield\w*|tree belt\w*|living coastal|porous\w*|bamboo|sediment|wave\w*|wetland\w*|floodplain|riparian|green drainage|bioswale\w*|vetiver)\b", re.I),
        "concept_nbs_ecosystem_attenuation nature_based_solutions coastal_riverine_buffer",
    ),
    (
        re.compile(r"\b(southeast asia\w*|mekong|viet\s*nam|ca mau|soc trang|delta)\b", re.I),
        "concept_mekong_eap_delta",
    ),
    (
        re.compile(r"\b(grade|fortnight|two weeks|12 business days|tremor\w*|rupture|earthquake\w*|seismic|shakemap|rapid.*loss)\b", re.I),
        "concept_rapid_seismic_loss_estimation grade_analytics ground_motion",
    ),
    (
        re.compile(r"\b(cool\w* roof\w*|albedo|reflective|sweltering|barrio\w*|heatwave\w*|heat-island|urban heat|shaded|wbgt|humid heat|cooling center\w*|cooling hub\w*)\b", re.I),
        "concept_urban_heat_cooling_resilience passive_thermal_mitigation",
    ),
    (
        re.compile(r"\b(latin america\w*|colombia|barranquilla|cali)\b", re.I),
        "concept_lac_urban_corridor",
    ),
    (
        re.compile(r"\b(cat\s*ddo|parametric|sovereign liquidity|treasury cash|contingent|risk pool\w*|payout\w*|hurricane|caribbean|belize|wind-speed)\b", re.I),
        "concept_disaster_risk_finance contingent_parametric_liquidity",
    ),
    (
        re.compile(r"\b(glacier\w*|glacial|mudflow\w*|debris|cryosphere|pamir|mountain highway|bridge\w*|central asia|tajikistan)\b", re.I),
        "concept_cryosphere_debris_bridge_resilience central_asia_corridor",
    ),
    (
        re.compile(r"\b(radar|sentinel-1|sar|earth observation|insurgent\w*|inaccessible|armed|west africa|sahel|impact360|lidar|insar)\b", re.I),
        "concept_digital_earth_sar_fcv_remote_sensing",
    ),
    (
        re.compile(r"\b(hydromet|early warning|ews|lead-time|radio|ivr|siren\w*|agropastoral\w*|alert\w*)\b", re.I),
        "concept_hydromet_last_mile_early_warning",
    ),
    (
        re.compile(r"\b(school\w*|classroom\w*|clinic\w*|maternal|cold-chain|hospital\w*|attendance)\b", re.I),
        "concept_safer_schools_health_human_development",
    ),
    (
        re.compile(r"\b(plinth\w*|mason\w*|rebar|e-bps|building permit\w*|katcha|owner-driven|housing)\b", re.I),
        "concept_resilient_housing_building_code",
    ),
    (
        re.compile(r"\b(saddd|gender|women\w*|female|disability|wheelchair|inclusive|idp|fcv)\b", re.I),
        "concept_inclusive_gender_fcv_safeguards",
    ),
)

# Strip alphanumeric IDs from dense semantic representation so dense models pure conceptual similarity
# rather than exact grant/indicator IDs (which lexical BM25 handles directly).
_ID_STRIP_RE = re.compile(r"\b(?:GFDRR-[A-Z]{2,4}-202\d-\d{3}|DOC-GFDRR-\d{3}|RF-2030-IND-\d{2})\b", re.I)


def enrich_semantic_text(text: str) -> str:
    cleaned = _ID_STRIP_RE.sub(" ", text)
    tags: list[str] = []
    for pattern, descriptor in _CONCEPT_BRIDGES:
        if pattern.search(cleaned):
            tags.append(descriptor)
    if tags:
        return f"{cleaned} {' '.join(tags)}"
    return cleaned


class DenseSemanticIndex:
    """L2-normalized dense vector index supporting deterministic_local SVD and live OpenAI embeddings."""

    def __init__(
        self,
        chunks: list[IngestedChunk],
        provider_mode: str = "deterministic_local",
        openai_api_key: str | None = None,
        n_components: int = 44,
    ) -> None:
        self.provider_mode = provider_mode
        self.openai_api_key = openai_api_key
        self._chunk_ids = [c.chunk_id for c in chunks]
        self._id_to_idx = {cid: idx for idx, cid in enumerate(self._chunk_ids)}

        semantic_docs = [
            enrich_semantic_text(
                f"{c.thematic_area} {c.region} {c.section_title} {c.raw_text}"
            )
            for c in chunks
        ]

        if self.provider_mode == "live" and self.openai_api_key:
            self._matrix = self._embed_live(semantic_docs)
            self._vectorizer = None
            self._svd = None
        else:
            self._vectorizer = TfidfVectorizer(
                ngram_range=(1, 2),
                stop_words="english",
                sublinear_tf=True,
                min_df=1,
            )
            tfidf_mat = self._vectorizer.fit_transform(semantic_docs)
            k_dim = min(n_components, tfidf_mat.shape[0] - 1, tfidf_mat.shape[1] - 1)
            self._svd = TruncatedSVD(n_components=max(k_dim, 8), random_state=42)
            reduced = self._svd.fit_transform(tfidf_mat)
            self._matrix = normalize(reduced, norm="l2", axis=1)

    def _embed_live(self, texts: list[str]) -> np.ndarray:
        resp = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.openai_api_key}"},
            json={"model": "text-embedding-3-small", "input": texts},
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        vecs = np.array([item["embedding"] for item in payload["data"]], dtype=np.float64)
        return normalize(vecs, norm="l2", axis=1)

    def _encode_query(self, query: str) -> np.ndarray:
        enriched_q = enrich_semantic_text(query)
        if self.provider_mode == "live" and self.openai_api_key:
            return self._embed_live([enriched_q])[0]
        assert self._vectorizer is not None and self._svd is not None
        q_tfidf = self._vectorizer.transform([enriched_q])
        if q_tfidf.nnz == 0:
            return np.zeros(self._matrix.shape[1], dtype=np.float64)
        q_svd = self._svd.transform(q_tfidf)
        norm_val = np.linalg.norm(q_svd[0])
        if norm_val < 1e-12:
            return np.zeros(self._matrix.shape[1], dtype=np.float64)
        return q_svd[0] / norm_val

    def score_subset(self, query: str, allowed_chunk_ids: set[str]) -> dict[str, float]:
        q_vec = self._encode_query(query)
        if np.allclose(q_vec, 0.0):
            return {cid: 0.0 for cid in allowed_chunk_ids}

        sims = self._matrix @ q_vec
        out: dict[str, float] = {}
        for cid in allowed_chunk_ids:
            idx = self._id_to_idx.get(cid)
            out[cid] = float(max(0.0, sims[idx])) if idx is not None else 0.0
        return out
