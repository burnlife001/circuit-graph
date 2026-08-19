from pathlib import Path

from circuit_graph.design import DesignSpec, instantiate
from circuit_graph.exporters.protel_netlist import write_protel_netlist
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "protel" / "add-primitives.NET"
)


def test_write_and_reparse_roundtrip(tmp_path: Path) -> None:
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(source_id=str(FIXTURE), file_path=str(FIXTURE)))
    out = tmp_path / "roundtrip.NET"
    write_protel_netlist(ir, out)
    ir2 = parser.parse(ParserInput(source_id=str(out), file_path=str(out)))
    assert [c.id for c in ir2.components] == [c.id for c in ir.components]
    assert sorted(n.name for n in ir2.nets) == sorted(n.name for n in ir.nets)
    assert sorted(c.pin_id for c in ir2.connections) == sorted(
        c.pin_id for c in ir.connections
    )


def test_designed_ir_writes_valid_netlist(tmp_path: Path) -> None:
    template = next(t for t in load_primitives() if t.id == "voltage-divider")
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    out = tmp_path / "divider.NET"
    write_protel_netlist(result.ir, out)
    text = out.read_text(encoding="utf-8")
    assert "[R1" not in text  # 组件块以 '[' 独占一行
    assert "R1" in text
    assert "14k" in text
    # 重新解析验证结构
    ir2 = ProtelNetParser().parse(ParserInput(source_id=str(out), file_path=str(out)))
    by_id = {c.id: c for c in ir2.components}
    assert set(by_id) == {"R1", "R2"}
    assert by_id["R1"].raw_value == "14k"
    conns = {(c.pin_id, c.net_id) for c in ir2.connections}
    assert ("R1.1", "VIN") in conns
    assert ("R2.2", "GND") in conns
