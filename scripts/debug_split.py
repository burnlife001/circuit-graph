"""Debug script to show split components and their connections."""
import sys
import tempfile
import os
from pathlib import Path

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser


def parse_net(net_file: Path):
    parser = ProtelNetParser()
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            content = net_file.read_text(encoding=enc)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".NET", delete=False
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                return parser.parse(ParserInput(file_path=tmp_path))
            finally:
                os.unlink(tmp_path)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Cannot decode {net_file}")


def main():
    net_file = Path("fixtures/protel/add-primitives.NET")
    ir = parse_net(net_file)

    print("=== Before split ===")
    u2 = next(c for c in ir.components if c.id == "U2")
    print(f"U2: part={u2.part_number}, pins={[(p.number, p.id) for p in u2.pins]}")
    for conn in ir.connections:
        if conn.pin_id.startswith("U2."):
            net = next(n for n in ir.nets if n.id == conn.net_id)
            print(f"  {conn.pin_id} -> {net.name}")

    # Split
    ir2 = run_enrichers(ir, [MultiUnitSplitter()]).ir

    print("\n=== After split ===")
    for comp in ir2.components:
        if comp.id.startswith("U2"):
            print(f"{comp.id}: part={comp.part_number}, pins={[(p.number, p.id) for p in comp.pins]}")
            for conn in ir2.connections:
                if conn.pin_id.startswith(f"{comp.id}."):
                    net = next(n for n in ir2.nets if n.id == conn.net_id)
                    pin_name = conn.metadata.get("pin_name", "?")
                    print(f"  {conn.pin_id} ({pin_name}) -> {net.name}")


if __name__ == "__main__":
    main()
