"""Phase C — manual entries for parts the JLCPCB resolver cannot fill.

C1 — HT7533-23 (SOT-23-3 LDO)
   JLCPCB only stocks HT7533 in SOT-89-3 and SOT-23-5, never in SOT-23-3.
   We register the canonical HT75xx-series SOT-23-3 LDO pinout:
     1 = VIN, 2 = GND, 3 = VOUT
   Source: HT7533 series datasheet (industry-standard pinout for the
   3-pin SOT-23 LDO variant).  pin_source = "manual".

C2 — MM32F014X-QFP48 → MM32F0144C6P (C5143545, LQFP-48)
   The board part number carries an ``X`` placeholder; the actual silicon
   is MM32F0144.  Search MM32F0144C6P directly via the LCSC id, which
   succeeds (EasyEDA component exists).  JLCPCB-returned package
   (LQFP-48(7x7)) matches the board LQFP-48 footprint, so the pin map
   writes straight to the board-level component.

JW5065 (C5141731) — deferred.  JLCPCB has it but EasyEDA's symbol store
has no uuid for the LCSC id yet (extended-catalogue timing gap).  Marked
in provenance but with no pin data; revisit once EasyEDA catches up.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402
from circuit_graph.knowledge.chip_resolver import (  # noqa: E402
    generate_symbol, parse_kicad_sym,
)

DB_PATH = PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"


def main() -> int:
    db = ComponentDatabase.open(DB_PATH)

    # ----- C1 — HT7533-23 manual pinout (3-pin SOT-23 LDO) -----
    ht_pn = "HT7533-23"
    db._upsert_component(
        ht_pn,
        package="SOT-23-3",
        pin_count=3,
        pin_source="manual",
        resolved_package="SOT-23-3",
    )
    ht_comp_id = db.get_component(ht_pn).id
    ht_pins = {"1": "VIN", "2": "GND", "3": "VOUT"}
    ht_types = {"1": "power_in", "2": "ground", "3": "power_out"}
    for pin_no, pin_name in ht_pins.items():
        db.conn.execute(
            """
            INSERT INTO pinouts (component_id, pin_number, pin_name,
                                 pin_type, source_ref)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(component_id, pin_number, source_ref)
            DO UPDATE SET pin_name=excluded.pin_name,
                          pin_type=excluded.pin_type
            """,
            (ht_comp_id, pin_no, pin_name, ht_types[pin_no]),
        )
    db._record_provenance(ht_comp_id, "manual_datasheet",
                          "HT7533 SOT-23-3 series datasheet")
    print(f"  C1 OK   {ht_pn}: 3 pins written (manual/datasheet)")

    # ----- C2 — MM32F014X-QFP48 → MM32F0144C6P (C5143545) -----
    board_pn = "MM32F014X-QFP48"
    board_pkg = "LQFP-48"
    lcsc_id = "C5143545"
    sym_file, cleanup = generate_symbol(lcsc_id)
    sym_name, pins = parse_kicad_sym(sym_file)
    pin_dict = {p["number"]: p["name"] for p in pins}
    from circuit_graph.knowledge.chip_resolver import (
        classify_pin_types,
    )
    pin_types = classify_pin_types(pin_dict)
    jlc_pkg = "LQFP-48(7x7)"
    from circuit_graph.knowledge.package_map import packages_match
    print(f"  C2 match LQFP-48<->LQFP-48(7x7) -> {packages_match(board_pkg, jlc_pkg)}")
    db._upsert_from_pin2json(
        board_pn,
        {
            "symbol": sym_name,
            "package": jlc_pkg,
            "source": lcsc_id,
            "pin_count": len(pins),
            "pins": pin_dict,
            "pin_types": pin_types,
        },
        pin_source="jlcpcb",
        board_package=board_pkg,
    )
    import shutil
    shutil.rmtree(cleanup, ignore_errors=True)
    print(f"  C2 OK   {board_pn}: {len(pins)} pins written via {lcsc_id}")

    # ----- JW5065 placeholder -----
    jw_pn = "JW5065"
    existing = db.get_component(jw_pn)
    if existing is None:
        db._upsert_component(
            jw_pn, package="SOT23-8", pin_count=8,
            pin_source=None, resolved_package="TSOT-23-8",
        )
        db._record_provenance(
            db.get_component(jw_pn).id, "manual_pending",
            "EasyEDA uuid not yet resolvable for C5141731",
        )
        print(f"  JW  SKIP {jw_pn}: placeholder row, awaiting EasyEDA")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())