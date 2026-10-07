"""Environment loading. Deliberately dependency-free."""

from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = APP_ROOT / ".env"


def load_env(path: Path | str = ENV_FILE) -> dict[str, str]:
    """Read a ``.env`` file into ``os.environ`` without overwriting real env vars.

    Real environment variables always win, so you can override a stored token
    for a single run without editing the file.
    """
    path = Path(path)
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def status() -> dict[str, bool]:
    """Which optional integrations are configured."""
    load_env()
    return {
        "apify": bool(os.getenv("APIFY_TOKEN")),
        "usajobs": bool(os.getenv("USAJOBS_KEY") and os.getenv("USAJOBS_EMAIL")),
    }
