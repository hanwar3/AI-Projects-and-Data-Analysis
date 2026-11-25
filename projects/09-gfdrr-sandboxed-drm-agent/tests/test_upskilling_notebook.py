"""Verify that every code cell in hands_on_governed_ai_lab.ipynb executes cleanly offline."""

from __future__ import annotations

import json
from pathlib import Path


def test_hands_on_upskilling_notebook_executes_cleanly() -> None:
    nb_path = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "upskilling_roadmap"
        / "hands_on_governed_ai_lab.ipynb"
    )
    assert nb_path.exists()
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4

    ns: dict[str, object] = {}
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) >= 4

    for cell in code_cells:
        src = "".join(cell["source"])
        exec(compile(src, str(nb_path), "exec"), ns)

    assert "approved" in ns
    approved = ns["approved"]
    assert getattr(approved, "status") == "APPROVED_FINAL"
