"""Parse a Protel netlist and dump IR summary + specific component details."""

import sys
import tempfile
import os
from pathlib import Path

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
    net_file = Path(sys.argv[1])
    ir = parse_net(net_file)

    print(f"components={len(ir.components)} nets={len(ir.nets)} connections={len(ir.connections)}")

    print("\n=== components ===")
    for c in ir.components:
        pins = sorted(p.number for p in c.pins)
        print(f"  {c.id}: kind={c.device_kind.value} fp={c.footprint!r} value={c.raw_value!r} pins={pins}")

    print("\n=== nets ===")
    for n in ir.nets:
        conns = sorted(c.pin_id for c in ir.connections if c.net_id == n.id)
        print(f"  {n.name}: type={n.type} pins={conns}")


if __name__ == "__main__":
    main()
