"""Phase D — load connector JSON schemas and upsert each connector's pinout.

The connector part_numbers in the FX-T268 netlist (``TYPE_C``, ``USB_4PIN_DIP``,
``ICSW``, ``CON_2P_1.2MM``) have no electronic part number to resolve via
JLCPCB — they are mechanical connectors with industry-standard signal maps.

Each ``*.json`` in ``circuit_graph/knowledge/seed/connectors/`` defines:

* ``connector_type`` — primary key for matching to a board component
* ``package`` — the matching board footprint (used as the canonical name)
* ``pins`` — list of ``{number, name, pin_type}``

Run once to seed the DB.  Idempotent: re-runs are no-ops.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402

DB_PATH = PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"
CONNECTOR_DIR = PROJECT_ROOT / "circuit_graph" / "knowledge" / "seed" / "connectors"


def load_one(db: ComponentDatabase, json_path: Path) -> None:
    n = db.import_connector_json(json_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    ctype = data.get("connector_type", json_path.stem)
    print(f"  {ctype:<16} ({n} pins) <- {json_path.name}")


def main() -> int:
    if not CONNECTOR_DIR.is_dir():
        print(f"ERROR: connector dir missing: {CONNECTOR_DIR}")
        return 1
    db = ComponentDatabase.open(DB_PATH)
    files = sorted(CONNECTOR_DIR.glob("*.json"))
    if not files:
        print(f"ERROR: no connector JSON files in {CONNECTOR_DIR}")
        return 1
    print(f"Loading {len(files)} connector schemas from {CONNECTOR_DIR.relative_to(PROJECT_ROOT)}")
    print(f"  DB: {DB_PATH}")
    for f in files:
        load_one(db, f)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())