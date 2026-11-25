"""CLI utility for Governed RAG querying, brief export, and provenance tamper verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import RAGConfig
from .governance.provenance import ProvenanceSigner
from .pipeline import GovernedRAGPipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gfdrr-rag", description="GFDRR Governed M&E RAG CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    q_cmd = sub.add_parser("query", help="Run a governed M&E query and optionally export a watermarked brief")
    q_cmd.add_argument("question", help="M&E question to evaluate")
    q_cmd.add_argument("--role", default="me_specialist", help="WB clearance role")
    q_cmd.add_argument("--export", type=Path, default=None, help="Optional path to export watermarked Markdown brief")

    v_cmd = sub.add_parser("verify-provenance", help="Verify cryptographic provenance & watermark on an exported brief")
    v_cmd.add_argument("brief_path", type=Path, help="Path to exported Markdown brief")

    args = parser.parse_args(argv)
    cfg = RAGConfig()

    if args.command == "query":
        pipeline = GovernedRAGPipeline(cfg)
        resp = pipeline.query(args.question, clearance_role=args.role)
        if args.export:
            brief_md = pipeline.export_brief(resp)
            args.export.write_text(brief_md, encoding="utf-8")
            print(f"Exported signed & watermarked brief to {args.export}")
        print(json.dumps(resp.model_dump(), indent=2))
        return 0

    if args.command == "verify-provenance":
        if not args.brief_path.exists():
            print(f"File not found: {args.brief_path}", file=sys.stderr)
            return 2
        signer = ProvenanceSigner(secret_key=cfg.hmac_secret)
        content = args.brief_path.read_text(encoding="utf-8")
        result = signer.verify_exported_brief(content)
        print(json.dumps(result.model_dump(), indent=2))
        return 0 if result.verified else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
