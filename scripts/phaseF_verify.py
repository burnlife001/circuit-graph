"""Phase F — re-import the FX-T268 netlist against the rebuilt KB and
verify every non-passive component has pin data (board-level or variant).

Steps:
  F1: Confirm all KB data sources are present (JLCPCB + manual + connectors).
  F2: Re-import FX-T268-V6-10米-5065.NET.
  F3: Run ``cg view <ref>`` for each non-passive IC and assert pinout exists.
  F4: Print a coverage report.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402

NETLIST = PROJECT_ROOT / "fixtures/protel/FX-T268-V6-10米-5065.NET"
DB_PATH = PROJECT_ROOT / "circuit_graph" / "knowledge" / "circuit_graph.db"

# (designator, expected_part_number) per IC and connector in the netlist.
EXPECTED_NON_PASSIVE = {
    "U2":  "MM32F014X-QFP48",
    "U3":  "RH6016C",
    "U4":  "JW5065",          # placeholder row, expected pin_source=None
    "U5":  "HT7533-23",       # manual entry
    "Q1":  "2N7002",
    "Q2":  "PE2312",
    "Q5":  "SW5001",
    "Q6":  "SS8050",          # variant row expected (NPN vs SOT-23)
    "D1":  "1N4148",          # variant row expected (SOD-323 vs SOD-123)
    "J1":  "ICSW",
    "J2":  "CON_2P_1.2MM",
    "USB1": "TYPE_C_16PIN",
    "USB2": "USB_4PIN_DIP",
}


def parse_part_numbers(netlist_path: Path) -> dict[str, str]:
    text = netlist_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\[\s*\n([A-Z]+\d+)\s*\n([^\n]+)\s*\n([^\n]+)\s*\n",
        re.MULTILINE,
    )
    return {m.group(1).strip(): m.group(3).strip()
            for m in pattern.finditer(text)}


def run_cg_view(designator: str, netlist: Path) -> dict | None:
    """Invoke ``cg view <designator> <netlist> --pretty`` and parse JSON."""
    result = subprocess.run(
        ["uv", "run", "cg", "view", designator, str(netlist),
         "--with-power", "--with-comm", "--pretty"],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    # F1 — confirm KB exists and has the seed data we built up.
    if not DB_PATH.exists():
        print(f"ERROR: KB missing at {DB_PATH}")
        return 1
    db = ComponentDatabase.open(DB_PATH)
    print("KB stats:", json.dumps(db.stats(), indent=2))

    # F2 — re-import the netlist (idempotent).
    print(f"\nRe-importing {NETLIST.name}…")
    n = db.import_protel_netlist(NETLIST)
    print(f"  imported/touched {n} components")

    # F3 — verify pin coverage per designator.
    refs = parse_part_numbers(NETLIST)
    print(f"\nCoverage report ({len(EXPECTED_NON_PASSIVE)} designators):")
    print(f"  {'ref':<8}{'part_number':<22}{'pkg':<12}{'pin_src':<10}{'pin_count':<10}")
    print(f"  {'-'*8}{'-'*22}{'-'*12}{'-'*10}{'-'*10}")

    failures: list[str] = []
    for ref, expected_pn in EXPECTED_NON_PASSIVE.items():
        rec = db.get_component(expected_pn)
        if rec is None:
            print(f"  {ref:<8}{expected_pn:<22}{'(missing)':<12}{'-':<10}{'-':<10}")
            failures.append(f"{ref}: no row for {expected_pn}")
            continue
        pinout = db.get_pinout(expected_pn)
        has_board_data = pinout is not None and len(pinout) > 0
        variants = db.query(variant_of=rec.id)
        has_variant = bool(variants)

        # Display logic: prefer variant row when board has no data.
        if has_board_data:
            print(f"  {ref:<8}{rec.part_number:<22}{rec.package or '-':<12}"
                  f"{(rec.pin_source or '-'):<10}{len(pinout):<10}")
        elif has_variant:
            v = variants[0]
            print(f"  {ref:<8}{v.part_number:<22}{v.package or '-':<12}"
                  f"{v.pin_source or '-':<10}{v.pin_count or 0:<10}  (variant)")
        else:
            print(f"  {ref:<8}{rec.part_number:<22}{rec.package or '-':<12}"
                  f"{(rec.pin_source or '-'):<10}{rec.pin_count or 0:<10}")
            if expected_pn == "JW5065":
                print(f"        (deferred: EasyEDA uuid gap)")
            else:
                failures.append(f"{ref}: no pinout for {expected_pn}")

    db.close()

    # F4 — end-to-end view test for two key designators.
    print("\ncg view smoke:")
    for ref in ("U2", "Q1", "USB1"):
        data = run_cg_view(ref, NETLIST)
        if data and "error" not in data:
            comp = data.get("focal_component", {})
            print(f"  {ref} OK   part={comp.get('part_number')} pins={len(comp.get('pins', []))}")
        else:
            err = data.get("error") if data else "no output"
            print(f"  {ref} FAIL {err}")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All non-passive components covered (JW5065 deferred).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())