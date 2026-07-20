import pytest

from circuit_graph.ir import (
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    Net,
    Pin,
    Provenance,
)
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.exceptions import LayoutPageOverflowError
from circuit_graph.layout.page_layout import layout_page
from circuit_graph.layout.unit_layout import UnitLayoutResult, layout_unit


def test_dangling_net_emits_warning():
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1],
        nets=[Net(id="N1", name="FLOAT")],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
        ],
    )
    with pytest.warns(UserWarning, match="fewer than 2 pins"):
        layout_unit(ir, LayoutConfig.from_dict({}))


def test_multiple_units_raise_page_overflow():
    empty = UnitLayoutResult(placements={}, pin_positions={}, wires=[], labels=[])
    with pytest.raises(LayoutPageOverflowError):
        layout_page({"u1": empty, "u2": empty})
