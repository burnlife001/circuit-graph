from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.unit_layout import layout_unit
from circuit_graph.layout.wire_placer import is_orthogonal


def _ir_simple() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="IN")
    n2 = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1, r2],
        nets=[n1, n2],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.1", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_layout_unit_produces_orthogonal_wires():
    ir = _ir_simple()
    cfg = LayoutConfig.from_dict({"io": {"inputs": ["IN"], "outputs": ["OUT"]}})
    result = layout_unit(ir, cfg)
    assert result.placements
    for wire in result.wires:
        assert is_orthogonal(wire)
