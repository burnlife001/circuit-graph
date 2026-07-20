import pytest
from pathlib import Path

from circuit_graph.layout import layout_schematic

ksa = pytest.importorskip("kicad_sch_api")


def test_layout_schematic_runs_on_empty_ir():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"))
    sch = ksa.create_schematic("empty")
    layout_schematic(ir, sch)
    assert sch is not None


def test_layout_schematic_uses_lm321_config():
    import sys

    spice_path = Path(__file__).parent.parent.parent / "lm321_ccs" / "ccs_v5_bjt.cir"
    if not spice_path.exists():
        pytest.skip(f"SPICE file not found: {spice_path}")

    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lm321_ccs"))
    from convert_to_kicad import _ir_from_spice

    ir = _ir_from_spice(spice_path)
    sch = ksa.create_schematic("lm321")
    layout_schematic(
        ir,
        sch,
        unit_configs={
            "main": Path(__file__).parent.parent.parent
            / "circuit_graph"
            / "layout"
            / "configs"
            / "lm321_ccs.json"
        },
    )
    assert sch is not None
