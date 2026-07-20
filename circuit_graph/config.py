"""Project-wide configuration loaded from ``.env``.

All keys are read via :func:`os.environ.get` so callers receive ``str | None``
and decide their own defaults.  Call :func:`load` once at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def load() -> None:
    """Load ``.env`` into ``os.environ``.  Idempotent — safe to call multiple times."""
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)


# Convenience accessors — prefer these over raw os.environ.get for typed defaults.

def datasheet_dir() -> Path | None:
    """Configured datasheet search directory, or ``None``."""
    raw = os.environ.get("DATASHEET_DIR")
    if raw:
        p = Path(raw.strip())
        if p.is_dir():
            return p
    return None


def camelot_venv() -> Path | None:
    """Path to the Camelot Python venv, or ``None``."""
    raw = os.environ.get("CAMELOT_VENV")
    if raw:
        p = Path(raw.strip())
        if p.is_dir():
            return p
    return None
