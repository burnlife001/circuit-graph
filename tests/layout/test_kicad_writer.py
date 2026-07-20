import pytest

ksa = pytest.importorskip("kicad_sch_api")

from circuit_graph.layout.kicad_writer import apply_layout
from circuit_graph.layout.page_layout import PageLayoutResult


def test_apply_layout_moves_component():
    from circuit_graph.ir import CircuitIR, CircuitMeta, Component, DeviceKind
    from circuit_graph.layout.config import LayoutConfig
    from circuit_graph.layout.unit_layout import layout_unit

    sch = ksa.create_schematic("test")
    try:
        sch.components.add("Device:R", "R1", "10k", position=(0, 0))
    except Exception:
        pytest.skip("KiCad symbol library 'Device' not available")

    r1 = Component(id="R1", device_kind=DeviceKind.RESISTOR)
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict({})
    unit = layout_unit(ir, cfg)
    page = PageLayoutResult(units={"main": unit})

    apply_layout(sch, page)
    assert sch.components["R1"].position.x == unit.placements["R1"].x


def test_apply_layout_normalizes_reference_value_rotation():
    """Reference and Value properties should be readable at 0° after rotation."""
    from circuit_graph.layout.placer import Placement
    from circuit_graph.layout.unit_layout import UnitLayoutResult

    sch = ksa.create_schematic("test")
    try:
        sch.components.add("Device:R", "R1", "10k", position=(0, 0))
    except Exception:
        pytest.skip("KiCad symbol library 'Device' not available")

    unit = UnitLayoutResult(
        placements={"R1": Placement(x=10.0, y=20.0, rotation=90.0)},
        pin_positions={"R1": {}},
        wires=[],
        labels=[],
    )
    page = PageLayoutResult(units={"main": unit})

    apply_layout(sch, page)
    assert sch.components["R1"].get_property_effects("Reference")["rotation"] == 0
    assert sch.components["R1"].get_property_effects("Value")["rotation"] == 0
