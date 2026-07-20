"""Fill empty ``description`` columns in the components table from JLCPCB search.

Reads each component row whose ``description`` is NULL or empty, runs
``jlc search <part_number> --limit 1 --json``, takes the first hit's
``description`` field, and writes it back.  Idempotent — rows that already
have a description are skipped.

Robustness:
  * ``jlc`` CLI lookup follows the same Windows convention as
    ``circuit_graph.knowledge.chip_resolver._jlc_cmd`` (prefers ``jlc.cmd``).
  * Garbage / unsearchable part numbers (CJK mojibake, single-letter tokens)
    are skipped without hitting the network.
  * Per-part retries handle transient failures; rate-limited via
    ``--sleep`` so we don't hammer the JLCPCB API.
  * Commits per row so a crash mid-run loses at most one update.

Usage:
    python scripts/fill_descriptions_from_jlc.py            # fill all empty
    python scripts/fill_descriptions_from_jlc.py --dry-run  # just print what would be done
    python scripts/fill_descriptions_from_jlc.py --limit 5  # only first 5 rows (smoke test)
    python scripts/fill_descriptions_from_jlc.py --id 32    # re-fetch one row (mojibake fix)
    python scripts/fill_descriptions_from_jlc.py --force    # overwrite existing descriptions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB_PATH = (
    Path(__file__).resolve().parents[1] / "circuit_graph" / "knowledge" / "circuit_graph.db"
)


def _jlc_cmd() -> str:
    """Match the Windows-aware lookup used by chip_resolver."""
    if os.name == "nt":
        cmd = shutil.which("jlc.cmd")
        if cmd:
            return cmd
    return "jlc"


# ---------------------------------------------------------------------------
# Heuristics for unsearchable part numbers
# ---------------------------------------------------------------------------

# Mojibake-style non-ASCII noise (e.g. "��¼") — never a JLC match.
_NON_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def _is_searchable(part_number: str) -> tuple[bool, str]:
    """Return ``(searchable, cleaned_part_number)``.

    Cleans obvious noise (double spaces) and rejects tokens that can't
    usefully hit JLCPCB: mojibake, single letters, pure punctuation.
    """
    pn = re.sub(r"\s+", " ", (part_number or "")).strip()
    if not pn:
        return False, ""
    if _NON_PRINTABLE.search(pn):
        return False, pn  # keep visible so user can spot it
    # Too short to disambiguate on JLC — "TX", "R", "C", etc.
    if len(pn) < 3:
        return False, pn
    return True, pn


# ---------------------------------------------------------------------------
# JLC lookup
# ---------------------------------------------------------------------------

def jlc_fetch_description(
    part_number: str,
    *,
    timeout: float = 30.0,
    retries: int = 2,
    sleep_s: float = 0.4,
) -> str | None:
    """Return the description of the first JLCPCB hit, or ``None`` on failure."""
    cmd = [_jlc_cmd(), "search", part_number, "--limit", "1", "--json"]
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
            )
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            time.sleep(sleep_s)
            continue
        except FileNotFoundError:
            print(
                "  error: jlc CLI not found. Install with: npm install -g @jlcpcb/cli",
                file=sys.stderr,
            )
            return None
        if proc.returncode != 0:
            last_err = f"exit={proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:120]}"
            time.sleep(sleep_s)
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            last_err = f"bad-json: {exc}"
            time.sleep(sleep_s)
            continue
        if not data.get("success"):
            last_err = f"success=false: {data.get('error') or '?'}"
            time.sleep(sleep_s)
            continue
        results = data.get("results") or []
        if not results:
            last_err = "no-results"
            time.sleep(sleep_s)
            continue
        desc = (results[0].get("description") or "").strip()
        return desc or None
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing to the DB.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N rows (0 = all). Useful for smoke tests.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.4,
        help="Seconds to sleep between JLC calls (default: 0.4).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print full descriptions (default: truncated to 100 chars).",
    )
    parser.add_argument(
        "--id",
        type=int,
        default=0,
        help="Process only this components.id (re-fetches and overwrites).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing descriptions (default: only fill NULL/empty).",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"error: database not found at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if args.id:
        cur.execute(
            "SELECT id, part_number, package FROM components WHERE id=?",
            (args.id,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"error: no row with id={args.id}", file=sys.stderr)
            return 1
        rows = [(row[0], row[1])]
        print(f"--id {args.id}: targeting part_number={row[1]!r} (package={row[2]!r})")
    else:
        if args.force:
            cur.execute(
                "SELECT id, part_number FROM components ORDER BY id"
            )
            print("--force: will overwrite ALL existing descriptions")
        else:
            cur.execute(
                "SELECT id, part_number FROM components "
                "WHERE description IS NULL OR description='' ORDER BY id"
            )
        rows = cur.fetchall()
    print(f"Components with empty description: {len(rows)}")

    if args.limit:
        rows = rows[: args.limit]
        print(f"--limit {args.limit}: processing first {len(rows)}")

    filled = 0
    skipped = 0
    failed = 0

    for comp_id, part_number in rows:
        ok, cleaned = _is_searchable(part_number)
        if not ok:
            skipped += 1
            print(f"[{comp_id:>3}] {part_number!r}: SKIP (unsearchable)")
            continue

        desc = jlc_fetch_description(cleaned, sleep_s=args.sleep)
        if desc is None:
            failed += 1
            print(f"[{comp_id:>3}] {cleaned}: NO MATCH")
            continue

        shown = desc if args.verbose else (desc[:100] + ("..." if len(desc) > 100 else ""))
        print(f"[{comp_id:>3}] {cleaned}: {shown}")

        if args.dry_run:
            filled += 1
            continue

        if args.force:
            cur.execute(
                "UPDATE components SET description=?, updated_at=datetime('now') "
                "WHERE id=?",
                (desc, comp_id),
            )
        else:
            cur.execute(
                "UPDATE components SET description=?, updated_at=datetime('now') "
                "WHERE id=? AND (description IS NULL OR description='')",
                (desc, comp_id),
            )
        if cur.rowcount == 0:
            # Race / already filled — count as success rather than failure.
            print(f"  (already filled, no-op)")
        else:
            filled += 1
        conn.commit()

    conn.close()
    print(
        f"\nSummary: filled={filled}, skipped={skipped}, failed={failed}, "
        f"total={len(rows)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())