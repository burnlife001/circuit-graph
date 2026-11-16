"""Import a ``*_Pin_Assignment_and_Multiplexing.json`` file into the KB.

The JSON is produced by the datasheet pin-extraction tool and holds one
entry per pin with an optional ``number`` field.  Pins without a number are
skipped — the KB maps pins by number, so a nameless pin cannot be stored.

Pin names/types land in the ``pinouts`` table; the per-pin function text
(``main_function``) has no column there, so it goes into the free-form
``properties`` table under the key ``pin_<number>``.

Usage::

    python scripts/import_pin_json.py JW5065 <path-to-json> [--package SOT23-8]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402

# Pin types as spelled in the extraction JSON -> the vocabulary the KB uses.
TYPE_MAP = {
    "": "unknown",
    "input": "input",
    "output": "output",
    "bidirectional": "comm",
    "power_in": "power_in",
    "power_out": "power_out",
    "ground": "ground",
    "no_connect": "no_connect",
    "passive": "passive",
    "analog": "analog",
    "oscillator": "oscillator",
    "open_drain": "open_drain",
    "unspecified": "unknown",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("part_number")
    ap.add_argument("json_path")
    ap.add_argument("--package", default=None, help="board footprint, e.g. SOT23-8")
    ap.add_argument("--manufacturer", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--subcategory", default=None)
    ap.add_argument("--description", default=None)
    args = ap.parse_args()

    payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    pins = payload.get("pins") or []

    numbered = {}
    types = {}
    functions = {}
    for pin in pins:
        number = pin.get("number")
        if number is None:
            continue
        name = (pin.get("name") or "").strip()
        if not name:
            continue
        key = str(number)
        numbered[key] = name
        types[key] = TYPE_MAP.get((pin.get("type") or "").strip().lower(), "unknown")
        function = (pin.get("main_function") or "").strip()
        if function:
            functions[key] = function

    if not numbered:
        print(f"no numbered pins in {args.json_path}", file=sys.stderr)
        return 1

    db = ComponentDatabase.default()
    comp_id = db._upsert_component(
        args.part_number,
        manufacturer=args.manufacturer,
        category=args.category,
        subcategory=args.subcategory,
        description=args.description,
        package=args.package,
        pin_count=len(numbered),
        pin_source="datasheet",
    )
    with db.conn:
        # The generic pinout mapping is stored with source_ref IS NULL, and
        # SQLite treats NULLs as distinct in the UNIQUE(component_id,
        # pin_number, source_ref) constraint — so ON CONFLICT never fires for
        # those rows and re-imports would pile up duplicates.  Clear the
        # generic rows for the pins we are about to write first.
        placeholders = ",".join("?" for _ in numbered)
        db.conn.execute(
            f"""
            DELETE FROM pinouts
            WHERE component_id = ? AND source_ref IS NULL
              AND pin_number IN ({placeholders})
            """,
            (comp_id, *numbered.keys()),
        )
        for number, name in numbered.items():
            db.conn.execute(
                """
                INSERT INTO pinouts (
                    component_id, pin_number, pin_name, pin_type, source_ref
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                    pin_name = excluded.pin_name,
                    pin_type = excluded.pin_type
                """,
                (comp_id, number, name, types[number]),
            )
        db._record_provenance(comp_id, "datasheet_pdf", str(Path(args.json_path).name))

    # 引脚功能描述进 properties 表（pinouts 表没有描述列）。
    # 键名 "pin_<引脚号>"，值为 "<引脚名>：<功能描述>"，便于 `cg kb lookup` 展示。
    for number, function in sorted(functions.items(), key=lambda kv: int(kv[0])):
        db.set_property(
            args.part_number,
            f"pin_{number}",
            f"{numbered[number]}：{function}",
        )

    print(f"{args.part_number}: {len(numbered)} pins imported (component_id={comp_id})")
    for number in sorted(numbered, key=lambda n: int(n)):
        print(f"  pin {number}: {numbered[number]:6s} ({types[number]})")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
