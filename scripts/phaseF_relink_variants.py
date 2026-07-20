"""Phase F cleanup — re-link variant rows to their board components and
recreate any that were lost during phase D / phase F re-imports.

After :func:`ComponentDatabase.import_protel_netlist` runs the
``_auto_resolve_ics`` path again, variant rows created in phase B with
``variant_of=board.id`` referenced the *old* board row id (or were
dropped entirely if the resolver saw ``pin_source=None`` and queued the
part for re-resolution).

This script:

1. Resolves every IC once more through the batch path, looking up
   *board* components by their current ``id``.
2. For each new (board, jlc_package) mismatch pair, recreates the
   ``<part_number>@<jlc_package>`` variant row with the FK set to the
   current board id.
3. Re-runs the resolver's idempotency check before inserting.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402
from circuit_graph.knowledge.chip_resolver import (  # noqa: E402
    resolve_parts_batch,
)
from circuit_graph.knowledge.package_map import (  # noqa: E402
    match_package, packages_match,
)

DB_PATH = PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"

# (board_part_number, board_footprint) per todolist Phase B1 — same list as
# phase B (minus JW5065 which still has no JLCPCB symbol).
MISMATCH_PARTS: list[tuple[str, str]] = [
    ("1N4148", "SOD-323"),
    ("SS8050", "NPN"),
]


def main() -> int:
    db = ComponentDatabase.open(DB_PATH)
    print(f"Re-resolving {len(MISMATCH_PARTS)} known-mismatch parts…")
    batch = resolve_parts_batch(MISMATCH_PARTS)

    for (pn, fp), res in zip(MISMATCH_PARTS, batch):
        if res is None:
            print(f"  SKIP {pn}: JLCPCB returned no data")
            continue
        jlc_pkg = res.get("package", "")
        if packages_match(fp, jlc_pkg):
            print(f"  SKIP {pn}: {fp!r} matches {jlc_pkg!r} now — board row should hold pins")
            continue
        board = db.get_component(pn)
        if board is None:
            print(f"  SKIP {pn}: board component row missing")
            continue
        variant_key = db._variant_key(pn, jlc_pkg)
        existing = db.query(part_number=variant_key)
        if existing:
            # Make sure variant_of points at the current board id.
            v = existing[0]
            if v.variant_of != board.id:
                db.conn.execute(
                    "UPDATE components SET variant_of = ? WHERE id = ?",
                    (board.id, v.id),
                )
                db.conn.commit()
                print(f"  RELINK {pn}: variant {variant_key} -> board id={board.id}")
            else:
                print(f"  OK {pn}: variant {variant_key} already linked")
            continue

        variant_id = db._upsert_component(
            variant_key,
            package=jlc_pkg,
            pin_count=len(res["pins"]),
            pin_source="jlcpcb",
            resolved_package=match_package(jlc_pkg),
            variant_of=board.id,
        )
        with db.conn:
            for pin_no, pin_name in res["pins"].items():
                pt = res.get("pin_types", {}).get(pin_no, "unknown")
                db.conn.execute(
                    """
                    INSERT INTO pinouts (component_id, pin_number, pin_name,
                                         pin_type, source_ref)
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(component_id, pin_number, source_ref)
                    DO UPDATE SET pin_name=excluded.pin_name,
                                  pin_type=excluded.pin_type
                    """,
                    (variant_id, str(pin_no), str(pin_name), pt),
                )
            db._record_provenance(variant_id, "pin2json_variant", pn)
        print(f"  CREATED {pn} -> {variant_key} (variant_of={board.id}, {len(res['pins'])} pins)")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())