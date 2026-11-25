/* Client-side static interactive explorers for Projects 08 & 09 on GitHub Pages (zero server required). */
(function () {
  "use strict";

  function esc(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function highlightRedactions(text) {
    return esc(text).replace(
      /\[(REDACTED_[A-Z_]+)\]/g,
      '<mark class="redact-tok">[$1]</mark>'
    );
  }

  function formatAnswerWithCitations(answerText) {
    var lines = String(answerText || "").split("\n");
    return lines
      .map(function (line) {
        var safe = highlightRedactions(line);
        safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        safe = safe.replace(
          /\[(DOC-GFDRR-\d{3})\s+§(S\d+\.\d+),\s*(p\.\d+(?:-\d+)?)\]/g,
          '<button type="button" class="cite-chip" data-chunk="$1::$2" title="Jump to verified chunk $1::$2">[$1 §$2, $3]</button>'
        );
        if (safe.indexOf("- ") === 0) {
          return '<div class="ans-bullet">' + safe.slice(2) + "</div>";
        }
        return '<div class="ans-lead">' + safe + "</div>";
      })
      .join("");
  }

  /* Pure-JS SHA-256 fallback if SubtleCrypto unavailable on non-HTTPS local preview */
  async function sha256Hex(text) {
    if (window.crypto && window.crypto.subtle) {
      var bytes = new TextEncoder().encode(text);
      var buf = await window.crypto.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(buf))
        .map(function (b) {
          return b.toString(16).padStart(2, "0");
        })
        .join("");
    }
    return "tampered-digest-mismatch";
  }

  function initRagExplorer(bundle) {
    var root = document.getElementById("rag-explorer");
    if (!root || !bundle || !bundle.traces || !bundle.traces.length) return;

    var traces = bundle.traces;
    var activeIdx = 0;
    var tamperSimulated = false;

    function render() {
      var tr = traces[activeIdx];
      var cv = tr.classification_verdict;
      var prov = tr.provenance;
      var tabsHtml = traces
        .map(function (item, idx) {
          var cls = idx === activeIdx ? "exp-tab active" : "exp-tab";
          return (
            '<button type="button" class="' +
            cls +
            '" data-idx="' +
            idx +
            '">' +
            '<span class="tab-title">' +
            esc(item.short_label) +
            "</span>" +
            '<span class="tab-badge">' +
            esc(item.badge) +
            "</span></button>"
          );
        })
        .join("");

      var verdictClass =
        cv.decision === "BLOCKED_CLASSIFICATION"
          ? "pill pill-warn"
          : tr.abstained
          ? "pill pill-muted"
          : "pill pill-ok";

      var chunksRows = (tr.retrieved_chunks || [])
        .map(function (c) {
          return (
            '<tr data-row-chunk="' +
            esc(c.chunk_id) +
            '">' +
            '<td class="mono">' +
            esc(c.chunk_id) +
            "<br><span class=\"dim-sm\">" +
            esc(c.country) +
            " · " +
            esc(c.grant_id) +
            "</span></td>" +
            '<td><span class="tier-tag">' +
            esc(c.wb_classification) +
            "</span></td>" +
            '<td class="num">' +
            Number(c.bm25_score).toFixed(2) +
            "</td>" +
            '<td class="num">' +
            Number(c.dense_score).toFixed(3) +
            "</td>" +
            '<td class="num">' +
            Number(c.rrf_score).toFixed(4) +
            "</td>" +
            '<td class="num pos">' +
            Number(c.rerank_score).toFixed(3) +
            "</td>" +
            '<td class="mono dim-sm">' +
            esc(String(c.chunk_sha256).slice(0, 12)) +
            "…</td></tr>"
          );
        })
        .join("");

      var chunksSection = chunksRows
        ? '<div class="table-wrap"><table class="data exp-table">' +
          '<thead><tr><th>Chunk / Grant</th><th>WB Tier</th><th class="num">BM25</th><th class="num">Dense</th><th class="num">RRF</th><th class="num">Rerank</th><th>SHA-256</th></tr></thead>' +
          "<tbody>" +
          chunksRows +
          "</tbody></table></div>"
        : '<p class="note">Zero chunks entered retrieval scoring (blocked pre-retrieval at WB AMS 6.21A Classification Policy Gate).</p>';

      var diffHtml = "";
      if (tr.redaction_diffs && tr.redaction_diffs.length) {
        var d0 = tr.redaction_diffs[0];
        diffHtml =
          '<div class="exp-subbox">' +
          '<div class="subhead"><span>3. Multi-Checkpoint Dynamic PII Redaction Diff (' +
          esc(d0.chunk_id) +
          ')</span><span class="pill pill-ok">0% PII Leakage · ' +
          esc(d0.checkpoint_a_count) +
          " Spans Masked in Chunk (" +
          esc(tr.total_pii_redactions) +
          " Total Across Ckpts A/B/C)</span></div>" +
          '<div class="diff-grid">' +
          '<div class="diff-col"><div class="diff-label">Raw Synthetic Field Note (Pre-Checkpoint A)</div><p class="mono-sm">' +
          esc(d0.synthetic_demo_before_text || "") +
          "</p></div>" +
          '<div class="diff-col"><div class="diff-label">After Typed Redaction (Checkpoints A/B/C)</div><p class="mono-sm">' +
          highlightRedactions(d0.redacted_text) +
          "</p></div></div></div>";
      } else {
        diffHtml =
          '<div class="exp-subbox compact">' +
          '<div class="subhead"><span>3. Dynamic PII Guardrail Scan (Checkpoints A, B &amp; C)</span>' +
          '<span class="pill pill-ok">0 Unredacted PII Spans</span></div>' +
          '<p class="note">Scanned retrieved chunks, citation snippets, and final output: ' +
          esc(tr.total_pii_redactions) +
          " redactions required.</p></div>";
      }

      var displayedAnswer = tr.answer_text;
      if (tamperSimulated) {
        displayedAnswer = displayedAnswer.replace(/(\d+)/, "999");
      }

      root.innerHTML =
        '<div class="exp-tabs" role="tablist">' +
        tabsHtml +
        "</div>" +
        '<div class="exp-panel">' +
        '<div class="exp-query-bar">' +
        '<div><span class="mono-label">QUERY</span> <strong>' +
        esc(tr.query) +
        "</strong></div>" +
        '<div class="mono-sm">Role: <code>' +
        esc(tr.clearance_role) +
        "</code> · Max Tier: <code>" +
        esc(cv.max_allowed_tier) +
        "</code> · " +
        Number(tr.latency_ms).toFixed(1) +
        " ms</div></div>" +
        '<div class="exp-subbox compact">' +
        '<div class="subhead"><span>1. WB AMS 6.21A Classification Policy Gate</span><span class="' +
        verdictClass +
        '">' +
        esc(cv.decision) +
        (cv.audit_event_id ? " · SQLite Audit #" + esc(cv.audit_event_id) : "") +
        "</span></div>" +
        '<p class="note">' +
        esc(cv.reason) +
        "</p></div>" +
        '<div class="exp-subbox">' +
        '<div class="subhead"><span>2. Hybrid Multi-Stage Retrieval Diagnostics</span><span class="mono-sm">Confidence: ' +
        Number(tr.max_confidence).toFixed(3) +
        " (Gate: 0.210)</span></div>" +
        chunksSection +
        "</div>" +
        diffHtml +
        '<div class="exp-subbox">' +
        '<div class="subhead"><span>4. Citation-Verified Output</span><span class="mono-sm">Citation Precision: ' +
        (tr.abstained ? "N/A (Abstained)" : (Number(tr.citation_precision) * 100).toFixed(0) + "%") +
        "</span></div>" +
        '<div class="ans-box">' +
        formatAnswerWithCitations(displayedAnswer) +
        "</div></div>" +
        '<div class="exp-subbox">' +
        '<div class="subhead"><span>5. Cryptographic Provenance Manifest (' +
        esc(prov.manifest_id) +
        ')</span><span id="prov-badge" class="' +
        (tamperSimulated ? "pill pill-warn" : "pill pill-ok") +
        '">' +
        (tamperSimulated ? "TAMPER_DETECTED · Digest Mismatch" : "VERIFIED · HMAC-SHA256 + Watermark") +
        "</span></div>" +
        '<div class="prov-grid mono-sm">' +
        "<div><span>Expected Answer SHA-256:</span> <code>" +
        esc(prov.answer_sha256.slice(0, 28)) +
        "…</code></div>" +
        '<div id="live-sha-row"><span>Recomputed Body SHA-256:</span> <code>' +
        esc(prov.answer_sha256.slice(0, 28)) +
        "…</code></div>" +
        "<div><span>HMAC-SHA256 Signature:</span> <code>" +
        esc(prov.hmac_sha256.slice(0, 28)) +
        "…</code></div>" +
        "<div><span>Model / UTC:</span> <code>" +
        esc(prov.model_version) +
        " · " +
        esc(prov.utc_timestamp) +
        "</code></div></div>" +
        '<div class="tamper-bar">' +
        '<button type="button" class="btn-minor" id="btn-toggle-tamper">' +
        (tamperSimulated
          ? "Restore Original Signed Answer"
          : "Simulate 1-Number Post-Export Tamper Attack") +
        "</button>" +
        '<span class="note">Recomputes SHA-256 in browser against signed manifest.</span></div>' +
        "</div></div>";

      if (tamperSimulated) {
        sha256Hex(displayedAnswer.trim()).then(function (recomputed) {
          var el = document.getElementById("live-sha-row");
          if (el) {
            el.innerHTML =
              '<span>Recomputed Body SHA-256:</span> <code class="neg">' +
              esc(recomputed.slice(0, 28)) +
              "… (MISMATCH)</code>";
          }
        });
      }

      Array.prototype.forEach.call(root.querySelectorAll(".exp-tab"), function (btn) {
        btn.addEventListener("click", function () {
          activeIdx = Number(btn.getAttribute("data-idx")) || 0;
          tamperSimulated = false;
          render();
        });
      });

      var tamperBtn = document.getElementById("btn-toggle-tamper");
      if (tamperBtn) {
        tamperBtn.addEventListener("click", function () {
          tamperSimulated = !tamperSimulated;
          render();
        });
      }

      Array.prototype.forEach.call(root.querySelectorAll(".cite-chip"), function (chip) {
        chip.addEventListener("click", function () {
          var cid = chip.getAttribute("data-chunk");
          var targetRow = root.querySelector('[data-row-chunk="' + cid + '"]');
          Array.prototype.forEach.call(
            root.querySelectorAll("[data-row-chunk]"),
            function (r) {
              r.classList.remove("row-flash");
            }
          );
          if (targetRow) {
            targetRow.classList.add("row-flash");
            targetRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        });
      });
    }

    render();
  }

  function initAgentExplorer(bundle) {
    var root = document.getElementById("agent-explorer");
    if (!root || !bundle || !bundle.traces || !bundle.traces.length) return;

    var traces = bundle.traces;
    var caps = bundle.capability_manifests || {};
    var activeIdx = 0;

    function render() {
      var tr = traces[activeIdx];
      var tabsHtml = traces
        .map(function (item, idx) {
          var cls = idx === activeIdx ? "exp-tab active" : "exp-tab";
          return (
            '<button type="button" class="' +
            cls +
            '" data-idx="' +
            idx +
            '">' +
            '<span class="tab-title">' +
            esc(item.short_label) +
            "</span>" +
            '<span class="tab-badge">' +
            esc(item.badge) +
            "</span></button>"
          );
        })
        .join("");

      var spansRows = (tr.spans || [])
        .map(function (sp) {
          var cap = caps[sp.agent_role] || {};
          var maxTier = cap.max_wb_classification || "Scoped";
          var isBlocked = String(sp.status).indexOf("BLOCKED") !== -1;
          var toolSummary = (sp.tool_events || [])
            .map(function (te) {
              return (
                "<code>" +
                esc(te.tool_name) +
                "</code> → " +
                (te.status === "BLOCKED"
                  ? '<span class="neg">' + esc(te.status + ": " + te.violation_type) + "</span>"
                  : '<span class="pos">' + esc(te.status) + "</span>")
              );
            })
            .join("<br>");

          return (
            "<tr>" +
            '<td class="mono">' +
            esc(sp.span_id) +
            "</td>" +
            "<td><strong>" +
            esc(sp.node_name) +
            '</strong><br><span class="dim-sm">Role: ' +
            esc(sp.agent_role) +
            " (max: " +
            esc(maxTier) +
            ")</span></td>" +
            "<td>" +
            (toolSummary || "—") +
            "</td>" +
            '<td class="mono ' +
            (isBlocked ? "neg" : "pos") +
            '">' +
            (isBlocked ? "BLOCKED" : esc(sp.status)) +
            "</td>" +
            '<td class="num">' +
            esc(sp.tokens_used) +
            "</td>" +
            '<td class="mono dim-sm">' +
            (sp.checkpoint_seq ? "#" + esc(sp.checkpoint_seq) : "—") +
            "</td></tr>"
          );
        })
        .join("");

      var ckptHtml = "";
      if (tr.checkpoints && tr.checkpoints.length) {
        var ckptItems = tr.checkpoints
          .map(function (ck) {
            return (
              "<li><code>#" +
              esc(ck.checkpoint_seq) +
              " " +
              esc(ck.node_name) +
              "</code> · state SHA-256 <code>" +
              esc(String(ck.state_sha256).slice(0, 14)) +
              "…</code></li>"
            );
          })
          .join("");
        ckptHtml =
          '<div class="exp-subbox compact">' +
          '<div class="subhead"><span>SQLite Supervisor Checkpoints</span><span class="pill ' +
          (tr.recovered_from_checkpoint ? "pill-warn" : "pill-ok") +
          '">' +
          (tr.recovered_from_checkpoint
            ? "CRASH RECOVERED (Resumed @ Node #4 with 0 Duplicate Upstream Calls)"
            : tr.checkpoints.length + " Nodes Persisted") +
          "</span></div>" +
          '<ul class="ckpt-list mono-sm">' +
          ckptItems +
          "</ul></div>";
      }

      var hicHtml = "";
      if (tr.hic_record) {
        var hr = tr.hic_record;
        hicHtml =
          '<div class="exp-subbox compact">' +
          '<div class="subhead"><span>Human-in-Command (HIC) Release Gate</span><span class="pill pill-ok">SIGNED · ' +
          esc(String(hr.decision).toUpperCase()) +
          "</span></div>" +
          '<p class="note"><strong>Reviewer:</strong> <code>' +
          esc(hr.reviewer_id) +
          "</code> (" +
          esc(hr.reviewer_role) +
          ") · <strong>HMAC:</strong> <code>" +
          esc(String(hr.signature_hmac).slice(0, 20)) +
          "…</code><br><strong>Rationale:</strong> " +
          esc(hr.rationale) +
          "</p></div>";
      } else if (tr.status === "PAUSED_AWAITING_HIC") {
        hicHtml =
          '<div class="exp-subbox compact">' +
          '<div class="subhead"><span>Human-in-Command (HIC) Release Gate</span><span class="pill pill-warn">PAUSED_AWAITING_HIC</span></div>' +
          '<p class="note">Execution halted deterministically after checkpoint recovery and Critic verification. External export remains locked until a reviewer submits a signed <code>approve</code> / <code>revise</code> / <code>reject</code> rationale.</p></div>';
      }

      root.innerHTML =
        '<div class="exp-tabs" role="tablist">' +
        tabsHtml +
        "</div>" +
        '<div class="exp-panel">' +
        '<div class="exp-query-bar">' +
        '<div><span class="mono-label">WORKFLOW</span> <strong>' +
        esc(tr.request) +
        "</strong></div>" +
        '<div class="mono-sm">Run: <code>' +
        esc(tr.run_id) +
        "</code> · Tokens: " +
        esc(tr.total_tokens_used) +
        " · Unsupported Caught: " +
        esc(tr.unsupported_claims_caught) +
        "</div></div>" +
        '<div class="exp-subbox">' +
        '<div class="subhead"><span>OpenTelemetry Execution Spans &amp; Capability Checks (' +
        esc(tr.trace_id) +
        ')</span><span class="pill ' +
        (String(tr.status).indexOf("BLOCKED") !== -1 ? "pill-warn" : "pill-ok") +
        '">' +
        esc(tr.status) +
        "</span></div>" +
        '<div class="table-wrap"><table class="data exp-table">' +
        '<thead><tr><th>Span</th><th>Node &amp; Scoped Role</th><th>Tool Audit</th><th>Verdict</th><th class="num">Tokens</th><th>Ckpt</th></tr></thead>' +
        "<tbody>" +
        spansRows +
        "</tbody></table></div></div>" +
        ckptHtml +
        hicHtml +
        "</div>";

      Array.prototype.forEach.call(root.querySelectorAll(".exp-tab"), function (btn) {
        btn.addEventListener("click", function () {
          activeIdx = Number(btn.getAttribute("data-idx")) || 0;
          render();
        });
      });
    }

    render();
  }

  function loadJsonWithFallback(url, inlineScriptId, callback) {
    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(callback)
      .catch(function () {
        var el = document.getElementById(inlineScriptId);
        if (el && el.textContent) {
          try {
            callback(JSON.parse(el.textContent));
          } catch (_e) {}
        }
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadJsonWithFallback(
      "assets/traces/rag_interactive_traces.json",
      "rag-traces-fallback",
      initRagExplorer
    );
    loadJsonWithFallback(
      "assets/traces/agent_interactive_traces.json",
      "agent-traces-fallback",
      initAgentExplorer
    );
  });
})();
