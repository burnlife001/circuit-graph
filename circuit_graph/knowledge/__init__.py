"""Component part-number knowledge base.

This module exposes two layers:

* :class:`ComponentDatabase` — the new SQLite-backed general-purpose store
  (the recommended entry point).  Use ``query(part_number=...)`` /
  ``query(category=...)`` to find components, ``get_pinout`` for pins,
  ``import_kicad_netlist`` / ``import_protel_netlist`` to ingest data, and
  ``resolve_pins`` to call the chip resolver against missing entries.

* :class:`KnowledgeBase` — the legacy JSON-prefix loader (JW → dcdc, HT →
  ldo, ...).  Kept for backwards compatibility only; new code should call
  :class:`ComponentDatabase` instead.  Importing :class:`KnowledgeBase`
  emits a :class:`DeprecationWarning`.

Typical usage::

    from circuit_graph.knowledge import ComponentDatabase

    db = ComponentDatabase.default()
    db.init_db()                                       # idempotent
    db.import_kicad_netlist("netlist.net")             # bulk ingest
    hits = db.query(category="dcdc")                   # generic query
    pinout = db.get_pinout("JW5065")                   # {pin: name, ...}
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Optional

from circuit_graph.knowledge.db import (
    ComponentDatabase,
    ComponentRecord,
)

__all__ = ["ComponentDatabase", "ComponentRecord", "KnowledgeBase"]


# ---------------------------------------------------------------------------
# Legacy ``KnowledgeBase`` — JSON-prefix loader (DEPRECATED)
#
# Kept verbatim from the old implementation so existing call sites keep
# working.  Importing this class raises a DeprecationWarning directing
# callers to :class:`ComponentDatabase`.
# ---------------------------------------------------------------------------


import json as _json


_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "vendor_db.json"


def _warn_legacy() -> None:
    warnings.warn(
        "circuit_graph.knowledge.KnowledgeBase is deprecated; use "
        "circuit_graph.knowledge.ComponentDatabase.query() instead.",
        DeprecationWarning,
        stacklevel=3,
    )


class KnowledgeBase:
    """In-memory view over the component prefix knowledge file.

    .. deprecated::
        Use :class:`ComponentDatabase` for new code.  This loader survives as
        a thin compatibility shim that reads the legacy ``vendor_db.json``
        prefix table.
    """

    def __init__(self, data: dict[str, dict[str, Any]], db_path: Path) -> None:
        _warn_legacy()
        self._entries: dict[str, dict[str, Any]] = {
            prefix.upper(): entry for prefix, entry in data.get("entries", {}).items()
        }
        self._db_path = db_path

    @classmethod
    def load(cls, path: Path | None = None) -> "KnowledgeBase":
        db_path = path or _DEFAULT_DB_PATH
        with db_path.open("r", encoding="utf-8") as f:
            data = _json.load(f)
        return cls(data, db_path)

    def reload(self, path: Path | None = None) -> "KnowledgeBase":
        db_path = path or self._db_path
        fresh = self.load(db_path)
        self._entries = fresh._entries
        self._db_path = db_path
        return self

    @staticmethod
    def _extract_prefix(part_number: str) -> str:
        prefix_chars: list[str] = []
        for ch in part_number.strip():
            if ch.isalpha():
                prefix_chars.append(ch)
            else:
                break
        return "".join(prefix_chars).upper()

    def lookup_prefix(self, part_number: str) -> Optional[dict[str, Any]]:
        prefix = self._extract_prefix(part_number)
        if not prefix:
            return None
        candidates = [part_number[:length] for length in range(len(part_number), 0, -1)]
        for candidate in candidates:
            entry = self._entries.get(candidate.upper())
            if entry is not None:
                return entry
        return None

    def get_category(self, part_number: str) -> Optional[str]:
        entry = self.lookup_prefix(part_number)
        return entry.get("category") if entry else None

    def is_dcdc(self, part_number: str) -> bool:
        return self.get_category(part_number) == "dcdc"

    def is_ldo(self, part_number: str) -> bool:
        return self.get_category(part_number) == "ldo"

    def is_bridge_driver(self, part_number: str) -> bool:
        return self.get_category(part_number) == "bridge_driver"

    def is_mosfet_pair(self, part_number: str) -> bool:
        return self.get_category(part_number) == "mosfet_pair"

    def is_touch_ic(self, part_number: str) -> bool:
        return self.get_category(part_number) == "touch_ic"

    @property
    def db_path(self) -> Path:
        return self._db_path

    def known_prefixes(self) -> list[str]:
        return sorted(self._entries)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"KnowledgeBase(prefixes={len(self._entries)}, path={self._db_path})"
