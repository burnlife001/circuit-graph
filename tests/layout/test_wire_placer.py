from circuit_graph.ir import Component, Connection, DeviceKind, Pin, Provenance
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.wire_placer import is_orthogonal, place_wires


def test_place_wires_orthogonal():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    pin_positions = {
        "R1": {"1": (0.0, 0.0), "2": (10.0, 0.0)},
        "R2": {"1": (10.0, 0.0), "2": (10.0, 10.0)},
    }
    cfg = LayoutConfig.from_dict({})

    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[
            Component(
                id="R1",
                device_kind=DeviceKind.RESISTOR,
                pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
            ),
            Component(
                id="R2",
                device_kind=DeviceKind.RESISTOR,
                pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
            ),
        ],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.1", net_id="N1", provenance=Provenance.NETLIST),
        ],
    )
    placements = {}

    segments = place_wires(ir, placements, pin_positions, cfg)
    assert segments
    for seg in segments:
        assert is_orthogonal(seg)
