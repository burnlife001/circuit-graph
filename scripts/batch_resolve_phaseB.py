"""Phase B — batch JLCPCB resolution for the 7 non-passive components that
the hardcoded --in-stock flag previously hid.

Run this once after Phase A lands.  It will:

1. Resolve each IC via the new ``--no-in-stock`` search.
2. Compare the JLCPCB-returned package against the board-level footprint
   parsed from FX-T268-V6-10米-5065.NET.
3. Write pin data into the bundled ``circuit_graph.db`` per the
   package-match contract.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402
from circuit_graph.knowledge.chip_resolver import (  # noqa: E402
    resolve_parts_batch,
)

NETLIST = PROJECT_ROOT / "fixtures/protel/FX-T268-V6-10米-5065.NET"
DB_PATH = PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"

# (part_number, board_footprint) per todolist Phase B1.
#
# JW5065 is excluded: JLC2KiCadLib cannot resolve its EasyEDA component
# uuid (C5141731 is in JLCPCB's "New Arrivals" catalogue but not yet on
# EasyEDA).  It will be handled via the datasheet fallback in a later phase.
TARGETS: list[tuple[str, str]] = [
    ("PE2312", "SOT-23-6"),
    ("SW5001", "QFN-26"),
    ("RH6016C", "SOT23-6"),
    ("1N4148", "SOD-323"),
    ("2N7002", "SOT-23-3"),
    ("SS8050", "NPN"),
]


def main() -> int:
    db = ComponentDatabase.open(DB_PATH)

    # The batch resolver writes to the DB itself when called via
    # ``_auto_resolve_ics``.  Replicate the same logic here but with
    # ``auto_resolve_ics_on_import=False`` to skip any pre-existing entries.
    pairs: list[tuple[str, str | None]] = [
        (pn, fp) for pn, fp in TARGETS
    ]
    print(f"Resolving {len(pairs)} parts in batch…")
    results = resolve_parts_batch(pairs)

    from circuit_graph.knowledge.package_map import (
        match_package, packages_match,
    )
    resolved = 0
    variants = 0
    failed: list[str] = []
    for (pn, fp), res in zip(pairs, results):
        if res is None:
            print(f"  FAIL {pn}: no JLCPCB result")
            failed.append(pn)
            continue
        jlc_pkg = res.get("package", "")
        match = packages_match(fp, jlc_pkg)
        if match:
            db._upsert_from_pin2json(
                pn, res, pin_source="jlcpcb", board_package=fp,
            )
            resolved += 1
            print(f"  OK   {pn}: match {fp!r}<->{jlc_pkg!r} -> board pin_source=jlcpcb")
        else:
            board = db.get_component(pn)
            variant_key = db._variant_key(pn, jlc_pkg)
            existing = db.query(part_number=variant_key)
            if existing:
                print(f"  SKIP {pn}: variant already exists ({variant_key})")
                continue
            variant_id = db._upsert_component(
                variant_key,
                package=jlc_pkg,
                pin_count=len(res["pins"]),
                pin_source="jlcpcb",
                resolved_package=match_package(jlc_pkg),
                variant_of=board.id if board else None,
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
            variants += 1
            print(f"  VAR  {pn}: mismatch {fp!r}<->{jlc_pkg!r} -> variant row {variant_key}")

    db.close()
    print(f"\nSummary: {resolved} board-level resolved, {variants} variants created, "
          f"{len(failed)} failed")
    print("(JW5065 deferred: EasyEDA uuid not resolvable for C5141731)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())