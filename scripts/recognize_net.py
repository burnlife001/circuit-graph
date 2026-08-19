"""Run topology recognition on a netlist and report matched primitives."""

import sys
import tempfile
import os
from pathlib import Path

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives


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
    files = [Path(f) for f in sys.argv[1:]] or [Path("fixtures/protel/add-primitives.NET")]
    templates = load_primitives()

    for net_file in files:
        ir = parse_net(net_file)
        ir2 = run_enrichers(ir, [MultiUnitSplitter(), TopologyRecognizer()]).ir
        print(f"== {net_file.name} (components={len(ir.components)}) ==")
        if not ir2.blocks:
            print("  (no blocks)")
        for b in ir2.blocks:
            print(f"  {b.id}: components={b.component_ids}")
        print()


if __name__ == "__main__":
    main()
