from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.signal_graph import assign_columns, build_signal_graph


def _ir_one_opamp() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[
            Pin(id="U1.1", number="1", name="+", type="input"),
            Pin(id="U1.2", number="2", name="OUT", type="output"),
        ],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    vref = Net(id="N1", name="VREF")
    out = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1],
        nets=[vref, out],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_build_signal_graph_follows_output_to_input():
    ir = _ir_one_opamp()
    config = LayoutConfig.from_dict({"io": {"inputs": ["VREF"], "outputs": ["OUT"]}})
    g = build_signal_graph(ir, config)
    assert g.has_edge("U1", "R1")


def test_assign_columns_puts_source_left():
    ir = _ir_one_opamp()
    config = LayoutConfig.from_dict({"io": {"inputs": ["VREF"], "outputs": ["OUT"]}})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] == 0
    assert cols["R1"] == 1


def test_passive_device_transmits_signal():
    """A resistor between two ICs should not block signal-flow columns."""
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U1.1", number="1", name="OUT", type="output")],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    u2 = Component(
        id="U2",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U2.1", number="1", name="IN", type="input")],
    )
    n1 = Net(id="N1", name="NET1")
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1, u2],
        nets=[n1],
        connections=[
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="U2.1", net_id="N1", provenance=Provenance.NETLIST),
        ],
    )
    config = LayoutConfig.from_dict({})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] < cols["R1"] < cols["U2"]


def test_center_column_for_unconnected_component():
    """A component with neither input nor output path lands in a center column."""
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    u1 = Component(
        id="U1",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U1.1", number="1", name="OUT", type="output")],
    )
    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    u2 = Component(
        id="U2",
        device_kind=DeviceKind.IC,
        pins=[Pin(id="U2.1", number="1", name="IN", type="input")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="NET1")
    n2 = Net(id="N2", name="NET2")
    n3 = Net(id="N3", name="FLOAT")
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[u1, r1, u2, r2],
        nets=[n1, n2, n3],
        connections=[
            Connection(pin_id="U1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="U2.1", net_id="N2", provenance=Provenance.NETLIST),
            # R2 is only connected to a dangling net.
            Connection(pin_id="R2.1", net_id="N3", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.2", net_id="N3", provenance=Provenance.NETLIST),
        ],
    )
    config = LayoutConfig.from_dict({})
    g = build_signal_graph(ir, config)
    cols = assign_columns(g, config)
    assert cols["U1"] < cols["R1"] < cols["U2"]
    assert 0 < cols["R2"] < cols["U2"]
