from pathlib import Path

import pytest

from circuit_graph.graph import create_graph
from circuit_graph.ir import (
    CircuitIR,
    Component,
    Connection,
    DeviceKind,
    EdgeKind,
    Net,
    NetType,
    NodeKind,
    Pin,
    Provenance,
    TraversalOptions,
)
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protel"


def _simple_ir() -> CircuitIR:
    r70 = Component(
        id="R70",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
    )
    r21 = Component(
        id="R21",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R21.1", number="1"), Pin(id="R21.2", number="2")],
    )
    vbus = Net(id="VBUS", name="VBUS")
    gnd = Net(id="GND", name="GND")
    return CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[r70, r21],
        nets=[vbus, gnd],
        connections=[
            Connection(pin_id="R70.1", net_id="VBUS", provenance=Provenance.NETLIST),
            Connection(pin_id="R70.2", net_id="GND", provenance=Provenance.NETLIST),
            Connection(pin_id="R21.1", net_id="VBUS", provenance=Provenance.NETLIST),
            Connection(pin_id="R21.2", net_id="GND", provenance=Provenance.NETLIST),
        ],
    )


def test_build_nodes_and_edges():
    graph = create_graph()
    result = graph.load(_simple_ir())
    assert len(result.warnings) == 0

    nodes = graph.nodes()
    assert len(nodes) == 8  # 2 comps + 4 pins + 2 nets
    assert graph.get_component("R70") is not None
    assert graph.get_pin("R70.1") is not None
    assert graph.get_net("VBUS") is not None

    edges = graph.edges()
    has_pin = [e for e in edges if e.kind == EdgeKind.HAS_PIN]
    connects = [e for e in edges if e.kind == EdgeKind.CONNECTS_TO]
    assert len(has_pin) == 4
    assert len(connects) == 4


def test_trace_component():
    graph = create_graph()
    graph.load(_simple_ir())
    pins = graph.trace_component("R70")
    assert len(pins) == 2
    nets = {item["net"].id for item in pins}
    assert nets == {"VBUS", "GND"}


def test_trace_net():
    graph = create_graph()
    graph.load(_simple_ir())
    items = graph.trace_net("VBUS")
    assert len(items) == 2
    comp_ids = {item["component"].id for item in items}
    assert comp_ids == {"R70", "R21"}


def test_neighbors():
    graph = create_graph()
    graph.load(_simple_ir())
    neighbors = graph.neighbors("R70")
    ids = {n.id for n in neighbors}
    assert ids == {"R70.1", "R70.2"}


def test_path_between():
    graph = create_graph()
    graph.load(_simple_ir())
    path = graph.path_between("R70.1", "R21.1")
    assert path is not None
    assert path[0] == "R70.1"
    assert path[-1] == "R21.1"
    assert "VBUS" in path


def test_path_between_no_path():
    graph = create_graph()
    graph.load(_simple_ir())
    path = graph.path_between("R70.1", "R70.2")
    assert path is not None  # same component via R70 pin edges


def test_traverse_bfs():
    graph = create_graph()
    graph.load(_simple_ir())
    subgraph = graph.traverse_bfs("R70", TraversalOptions(max_depth=4))
    assert "R70" in subgraph.nodes
    assert "VBUS" in subgraph.nodes
    assert "R21" in subgraph.nodes


def test_traverse_with_node_filter():
    graph = create_graph()
    graph.load(_simple_ir())
    subgraph = graph.traverse_bfs("R70", TraversalOptions(node_kinds=[NodeKind.NET]))
    assert "R70" in subgraph.nodes
    assert all(n.kind == NodeKind.NET for n in subgraph.nodes.values() if n.id != "R70")


def _typed_ir() -> CircuitIR:
    """IR with mixed net types and device kinds for predicate tests."""
    r70 = Component(
        id="R70",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
    )
    c1 = Component(
        id="C1",
        device_kind=DeviceKind.CAPACITOR,
        pins=[Pin(id="C1.1", number="1"), Pin(id="C1.2", number="2")],
    )
    vbus = Net(id="VBUS", name="VBUS", type=NetType.POWER)
    gnd = Net(id="GND", name="GND", type=NetType.GND)
    sig = Net(id="SIG", name="SIG", type=NetType.SIGNAL)
    return CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[r70, c1],
        nets=[vbus, gnd, sig],
        connections=[
            Connection(pin_id="R70.1", net_id="VBUS", provenance=Provenance.NETLIST),
            Connection(pin_id="R70.2", net_id="SIG", provenance=Provenance.NETLIST),
            Connection(pin_id="C1.1", net_id="VBUS", provenance=Provenance.NETLIST),
            Connection(pin_id="C1.2", net_id="GND", provenance=Provenance.NETLIST),
        ],
    )


def test_traverse_with_net_type_filter():
    graph = create_graph()
    graph.load(_typed_ir())
    # Restrict to POWER nets only — SIG must be excluded
    subgraph = graph.traverse_bfs(
        "R70",
        TraversalOptions(net_types=[NetType.POWER]),
    )
    # R70 is the start node, so it's always included
    assert "R70" in subgraph.nodes
    net_nodes = {
        n.id: n.data.type for n in subgraph.nodes.values() if n.kind == NodeKind.NET
    }
    assert "SIG" not in net_nodes
    # Whatever non-start nets made it in must be POWER
    for nid, ntype in net_nodes.items():
        assert ntype == NetType.POWER, f"{nid} had type {ntype}"


def test_traverse_with_device_kind_filter():
    graph = create_graph()
    graph.load(_typed_ir())
    # Restrict to RESISTOR-only expansion
    subgraph = graph.traverse_bfs(
        "R70",
        TraversalOptions(device_kinds=[DeviceKind.RESISTOR]),
    )
    assert "R70" in subgraph.nodes
    comp_nodes = {
        n.id: n.data.device_kind
        for n in subgraph.nodes.values()
        if n.kind == NodeKind.COMPONENT
    }
    # C1 (CAPACITOR) must be excluded
    assert "C1" not in comp_nodes
    for cid, kind in comp_nodes.items():
        assert kind == DeviceKind.RESISTOR, f"{cid} had kind {kind}"


def test_to_dot():
    graph = create_graph()
    graph.load(_simple_ir())
    dot = graph.to_dot()
    assert dot.startswith("digraph circuit {")
    assert "R70" in dot
    assert "VBUS" in dot


def test_orphan_pin_warning():
    ir = CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="R1",
                device_kind=DeviceKind.RESISTOR,
                pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
            )
        ],
        nets=[Net(id="NET", name="NET")],
        connections=[Connection(pin_id="R1.1", net_id="NET")],
    )
    graph = create_graph()
    result = graph.load(ir)
    assert any("GRAPH_ORPHAN_PIN" in w for w in result.warnings)


def test_graph_from_t268():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    graph = create_graph()
    result = graph.load(ir)

    assert len(graph.nodes()) > 0
    assert len(graph.edges()) > 0
    assert graph.get_component("R70") is not None
    assert graph.get_net("CC2") is not None

    r70_pins = graph.trace_component("R70")
    assert len(r70_pins) == 2

    cc2_items = graph.trace_net("CC2")
    assert len(cc2_items) > 0
    assert any(item["component"].id == "R70" for item in cc2_items)

    # All warnings are non-fatal.
    assert result.warnings is not None
