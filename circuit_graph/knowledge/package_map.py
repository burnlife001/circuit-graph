"""Standard IC package name library + board-footprint matcher.

Problem: JLCPCB symbol generation returns a JLCPCB-style package name (e.g.
``"TSOT-23-8"``) that does NOT always equal the board-level footprint string
(e.g. ``"SOT23-8"``).  Without normalization we cannot decide whether a JLCPCB
result actually matches the board part, so we either over-assign (wrong pin
maps on the wrong physical part) or never assign at all.

Design:

* :data:`STANDARD_PACKAGES` — the canonical package name library.  Each entry
  is a normalized form like ``"SOT-23-3"`` or ``"LQFP-48"`` that JLCPCB-style
  search results produce.
* :func:`normalize_package` — strip whitespace, unify ``-`` separators, drop
  noise suffixes (e.g. ``"4x4"``, ``"(7x7)"``), uppercase.
* :func:`match_package` — return the canonical name for a board-level string
  when one of the variants in the standard library matches.
* :func:`packages_match` — strict equality after normalization.  Used by
  :func:`circuit_graph.knowledge.db._auto_resolve_ics` to decide if a JLCPCB
  result should overwrite the board component's pin map.

Critical invariant: **TSOT vs SOT** are NOT interchangeable — TSOT is the
thin-shrink variant with a different thermal pad footprint.  The matcher
treats them as distinct canonical names.
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Standard package name library
# ---------------------------------------------------------------------------
#
# Each entry is a canonical name.  ``variants`` lists equivalent strings seen
# on board-level footprints or in JLCPCB search results — first variant
# matching wins (after normalization).  Add new entries here when a part
# shows up with a footprint that does not normalize into any existing entry.

STANDARD_PACKAGES: dict[str, dict[str, tuple[str, ...]]] = {
    # ----- SOT/SC family -----
    "SOT-23-3": {
        "variants": ("SOT-23-3", "SOT23-3", "SOT-23", "SOT23"),
    },
    "SOT-23-5": {
        "variants": ("SOT-23-5", "SOT23-5", "SOT23-5L"),
    },
    "SOT-23-6": {
        "variants": ("SOT-23-6", "SOT23-6"),
    },
    "TSOT-23-5": {
        "variants": ("TSOT-23-5", "TSOT23-5"),
    },
    "TSOT-23-6": {
        "variants": ("TSOT-23-6", "TSOT23-6"),
    },
    "TSOT-23-8": {
        "variants": ("TSOT-23-8", "TSOT23-8", "SOT23-8"),
    },
    "SOT-89-3": {
        "variants": ("SOT-89-3", "SOT89-3", "SOT-89"),
    },
    "SOT-223": {
        "variants": ("SOT-223", "SOT223"),
    },
    "SOD-323": {
        "variants": ("SOD-323", "SOD323"),
    },
    "SOD-123": {
        "variants": ("SOD-123", "SOD123"),
    },
    # ----- QFN / DFN -----
    "QFN-26(4x4)": {
        "variants": ("QFN-26(4x4)", "QFN-26", "QFN26"),
    },
    "QFN-32": {
        "variants": ("QFN-32", "QFN32"),
    },
    "QFN-48": {
        "variants": ("QFN-48", "QFN48"),
    },
    "DFN-8": {
        "variants": ("DFN-8", "DFN8"),
    },
    # ----- QFP / LQFP -----
    "LQFP-48": {
        "variants": ("LQFP-48(7x7)", "LQFP-48", "QFP-48", "LQFP48", "QFP48"),
    },
    "LQFP-64": {
        "variants": ("LQFP-64", "QFP-64", "LQFP64"),
    },
    "LQFP-100": {
        "variants": ("LQFP-100", "QFP-100", "LQFP100"),
    },
    "TSSOP-8": {
        "variants": ("TSSOP-8", "TSSOP8"),
    },
    "TSSOP-14": {
        "variants": ("TSSOP-14", "TSSOP14"),
    },
    "TSSOP-16": {
        "variants": ("TSSOP-16", "TSSOP16"),
    },
    "SOIC-8": {
        "variants": ("SOIC-8", "SOIC8", "SOP-8", "SOP8"),
    },
    "SOIC-16": {
        "variants": ("SOIC-16", "SOIC16", "SOP-16"),
    },
    "MSOP-8": {
        "variants": ("MSOP-8", "MSOP8"),
    },
    # ----- BGA -----
    "BGA-64": {
        "variants": ("BGA-64", "BGA64"),
    },
}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Strip dimensions like "(4x4)", "(7x7)" since they don't change the package
# family.  Also drop leading/trailing whitespace and uppercase.
_DIM_RE = re.compile(r"\(\s*\d+(\.\d+)?\s*[xX×]\s*\d+(\.\d+)?\s*\)")


def normalize_package(raw: Optional[str]) -> Optional[str]:
    """Return a canonical-ish form of *raw* or ``None`` when empty.

    Steps:
      1. ``None``/empty → ``None``.
      2. Strip and uppercase.
      3. Remove parenthetical dimensions ``"(4x4)"`` etc.
      4. Normalise separators — keep ``-`` as the canonical separator and
         collapse runs.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    s = s.upper()
    s = _DIM_RE.sub("", s).strip()
    # Normalise spaces around dashes and collapse repeated dashes.
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s or None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _variant_index() -> dict[str, str]:
    """Build a flat ``{normalized_variant: canonical_name}`` map for lookup."""
    out: dict[str, str] = {}
    for canonical, info in STANDARD_PACKAGES.items():
        canon_norm = normalize_package(canonical) or canonical.upper()
        # The canonical form always maps to itself.
        out.setdefault(canon_norm, canonical)
        for variant in info["variants"]:
            v_norm = normalize_package(variant) or variant.upper()
            out[v_norm] = canonical
    return out


_VARIANT_INDEX: dict[str, str] = _variant_index()


def match_package(raw: Optional[str]) -> Optional[str]:
    """Return the canonical standard package name for *raw* or ``None``.

    Looks up the normalized form of *raw* in the flat variant index.  Returns
    ``None`` when no known standard package matches — callers should treat
    that as "do not auto-assign JLCPCB results to this component".
    """
    norm = normalize_package(raw)
    if norm is None:
        return None
    return _VARIANT_INDEX.get(norm)


def packages_match(a: Optional[str], b: Optional[str]) -> bool:
    """Return True iff *a* and *b* resolve to the same canonical package.

    Used by the auto-resolver to decide whether JLCPCB-returned package info
    matches the board-level footprint before trusting its pin map.

    Either side may be ``None`` — in that case we fall back to string equality
    after normalization (no canonical match found, so accept if both strings
    normalize to the same form).
    """
    if not a or not b:
        return False
    a_norm = normalize_package(a)
    b_norm = normalize_package(b)
    if a_norm is None or b_norm is None:
        return False
    a_canon = _VARIANT_INDEX.get(a_norm)
    b_canon = _VARIANT_INDEX.get(b_norm)
    if a_canon and b_canon:
        return a_canon == b_canon
    # Neither is a known canonical — accept on direct equality.
    return a_norm == b_norm


__all__ = [
    "STANDARD_PACKAGES",
    "normalize_package",
    "match_package",
    "packages_match",
]