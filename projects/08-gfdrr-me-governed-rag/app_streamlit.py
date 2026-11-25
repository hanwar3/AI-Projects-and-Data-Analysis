"""Streamlit workbench for GFDRR M&E Specialists: Governed RAG, PII Diff, Provenance & HITL Labeling."""

from __future__ import annotations

import streamlit as st

from gfdrr_corpus.taxonomy import CLEARANCE_ROLES, THEMATIC_AREAS
from gfdrr_rag.config import RAGConfig
from gfdrr_rag.eval.hitl_loop import HITLFeedbackStore
from gfdrr_rag.pipeline import GovernedRAGPipeline

st.set_page_config(page_title="GFDRR Governed M&E RAG", layout="wide")


@st.cache_resource
def get_pipeline() -> tuple[GovernedRAGPipeline, HITLFeedbackStore]:
    return GovernedRAGPipeline(RAGConfig()), HITLFeedbackStore()


pipeline, fb_store = get_pipeline()

st.title("GFDRR 2026–2030 Governed M&E Grant Portfolio RAG")
st.caption(
    "Pre-retrieval World Bank AMS 6.21A Classification Gate · Hybrid BM25 + Dense + RRF + Reranker · "
    "3-Checkpoint Dynamic PII Redaction · HMAC-SHA256 Provenance Watermarking"
)

col_cfg1, col_cfg2, col_cfg3 = st.columns(3)
with col_cfg1:
    role = st.selectbox("WB Clearance Role", list(CLEARANCE_ROLES), index=2)
with col_cfg2:
    mode = st.selectbox(
        "Retrieval Mode",
        ["hybrid_rrf_rerank", "hybrid_rrf", "bm25_only", "dense_only"],
        index=0,
    )
with col_cfg3:
    thematic = st.selectbox("Optional Thematic Filter", ["(All)"] + list(THEMATIC_AREAS), index=0)

query_text = st.text_input(
    "M&E Portfolio Query",
    value="Compare Nature-based Solutions flood attenuation and World Bank financing informed across Pakistan, Viet Nam, and Kenya.",
)

if st.button("Run Governed Evaluation", type="primary"):
    m_filter = {} if thematic == "(All)" else {"thematic_area": thematic}
    resp = pipeline.query(query_text, clearance_role=role, metadata_filter=m_filter, retrieval_mode=mode)

    st.subheader("1. Classification Policy Gate Verdict")
    st.json(resp.classification_verdict.model_dump())

    st.subheader("2. Grounded M&E Synthesis")
    if resp.abstained:
        st.warning(resp.synthesis.answer_text)
    else:
        st.markdown(resp.synthesis.answer_text)

    st.subheader("3. Retrieved Chunks & HITL Relevance Feedback")
    for ev in resp.retrieved_evidence:
        with st.expander(
            f"{ev.chunk_id} | {ev.grant_id} ({ev.wb_classification}) | Rerank={ev.rerank_score:.3f}"
        ):
            st.write(ev.redacted_text)
            st.caption(
                f"BM25={ev.bm25_score:.2f} · Dense={ev.dense_score:.3f} · RRF={ev.rrf_score:.4f} · SHA256={ev.chunk_sha256[:16]}…"
            )

    st.subheader("4. Cryptographic Provenance Receipt")
    st.code(resp.provenance.model_dump_json(indent=2), language="json")

    brief_md = pipeline.export_brief(resp)
    st.download_button(
        "Download Watermarked M&E Brief (.md)",
        data=brief_md,
        file_name="gfdrr_governed_brief.md",
        mime="text/markdown",
    )
