"""Audit all components.description rows and classify each.

Categories
----------
- empty       : NULL or '' (already known — 11 of 88)
- mojibake    : classic broken-encoding signature (?? + non-ASCII,
                or other tell-tale patterns)
- placeholder : short pure-? / pure-non-printable strings ("??????")
- vendor_label: short ASCII label from the legacy vendor_db.json
                (synchronous_buck, ldo, complementary_mosfet, touch_controller)
                — these are intentionally category-like, NOT broken.
- jlc         : long English description with units / ROHS / category
                keywords — looks like a JLCPCB search result.
- other       : everything else — printed for manual review.

Usage
-----
    python scripts/audit_descriptions.py
    python scripts/audit_descriptions.py --json   # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = Path(__file__).resolve().parents[1] / "circuit_graph" / "knowledge" / "circuit_graph.db"

# Known vendor_db.json labels — these are intentional short category strings,
# not broken data.  Add to this set if more are discovered.
KNOWN_VENDOR_LABELS: frozenset[str] = frozenset({
    "synchronous_buck",
    "ldo",
    "complementary_mosfet",
    "touch_controller",
})

# JLC descriptions are typically long English with units, "ROHS", etc.
_JLC_KEYWORDS = re.compile(
    r"\bROHS\b|\bIndependent\b|\d+\s*(mA|uA|A|mV|V|kHz|MHz|ns|pF|nF|uF|nC|nW|mW|W|nm|cd|Ω|@)",
    re.IGNORECASE,
)

# Mojibake / garbage heuristics.
_NON_PRINTABLE = re.compile(r"[^\x20-\x7e]")
_RUN_OF_QMARKS = re.compile(r"\?{2,}")


def classify(desc: str | None) -> str:
    """Classify a single description into one of the categories above.

    The decision tree prefers the most-specific tag:
      1. empty       — NULL or ''.
      2. mojibake    — runs of ``?`` mixed with non-ASCII (classic
                       replacement-character mojibake from a mis-decoded
                       source).
      3. placeholder — pure ``?`` runs with no other content
                       (``"????"``, ``"??????"``).
      4. jlc         — looks like a JLC search result (has units, ROHS,
                       or non-ASCII content).
      5. vendor_label— a short, intentional category string from
                       ``vendor_db.json`` (whitelisted).
      6. suspicious  — anything else (real JLC thin responses, hand-written
                       categories like ``"Connector"``, or unique junk).
    """
    if not desc:
        return "empty"
    has_nonascii = any(ord(c) > 127 for c in desc)
    has_qmark_runs = bool(_RUN_OF_QMARKS.search(desc))

    # Mojibake signature: ?? adjacent to non-ASCII bytes.
    if has_qmark_runs and has_nonascii:
        return "mojibake"

    # Garbage with `?` runs (no non-ASCII).  JLC descriptions never
    # contain ``?``, so any ``??`` run is data corruption:
    #   - all-? strings  → placeholder
    #   - `?` runs mixed with other text → placeholder if >=50% of the
    #     string is `?`, else still classified as suspicious by the
    #     fallback below (we want the audit to surface it).
    if has_qmark_runs and not has_nonascii:
        qmarks = sum(1 for c in desc if c == "?")
        if _RUN_OF_QMARKS.fullmatch(desc):
            return "placeholder"
        if qmarks / len(desc) >= 0.5:
            return "placeholder"

    # Non-ASCII without the mojibake signature is almost always valid UTF-8
    # from JLC (CJK, °, Ω, ℃, ±, etc.).
    if has_nonascii:
        return "jlc"

    if desc in KNOWN_VENDOR_LABELS:
        return "vendor_label"
    if _JLC_KEYWORDS.search(desc):
        return "jlc"
    # Pure-? strings (e.g. "USB???") that slipped through — any `?{2,}`
    # run on an ASCII string is data corruption.
    if has_qmark_runs:
        return "suspicious"
    if re.fullmatch(r"[a-z_]+", desc) and len(desc) < 30:
        return "vendor_label" if desc in KNOWN_VENDOR_LABELS else "suspicious"
    return "suspicious"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--show",
        default="empty,mojibake,placeholder,suspicious",
        help="categories to print (comma-separated). default: %(default)s",
    )
    args = parser.parse_args()

    show_cats = set(args.show.split(","))

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, part_number, package, description FROM components ORDER BY id"
    )
    rows = cur.fetchall()
    conn.close()

    by_cat: dict[str, list[dict]] = {c: [] for c in
        ("empty", "mojibake", "placeholder", "vendor_label", "jlc", "suspicious")}
    for cid, pn, pkg, desc in rows:
        cat = classify(desc)
        by_cat[cat].append({
            "id": cid, "part_number": pn, "package": pkg, "description": desc,
        })

    if args.json:
        print(json.dumps(by_cat, indent=2, ensure_ascii=False))
        return 0

    counts = Counter({c: len(v) for c, v in by_cat.items()})
    print("=== classification summary ===")
    total = sum(counts.values())
    for cat, n in counts.most_common():
        print(f"  {cat:14s} {n:>3d}  ({100*n/total:.0f}%)")
    print(f"  {'TOTAL':14s} {total:>3d}")
    print()

    for cat in ("empty", "mojibake", "placeholder", "suspicious", "vendor_label", "jlc"):
        if cat not in show_cats:
            continue
        print(f"=== {cat} ({len(by_cat[cat])}) ===")
        for row in by_cat[cat]:
            desc = row["description"] or ""
            shown = desc if len(desc) <= 100 else desc[:100] + "..."
            print(f"  id={row['id']:>3}  pn={row['part_number']!r}  pkg={row['package']!r}")
            print(f"         desc={shown!r}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())