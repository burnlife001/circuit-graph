from pathlib import Path

import pytest

from circuit_graph.graph import create_graph
from circuit_graph.ir import (
    CircuitIR,
    Component,
    Connection,
    DeviceKind,
    Net,
    Pin,
    Provenance,
)
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.query import (
    neighbors_of,
    trace_component,
    trace_net,
    traverse_bfs,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protel"


def _sample_ir() -> CircuitIR:
    return CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="R70",
                device_kind=DeviceKind.RESISTOR,
                pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
            ),
            Component(
                id="R21",
                device_kind=DeviceKind.RESISTOR,
                pins=[Pin(id="R21.1", number="1"), Pin(id="R21.2", number="2")],
            ),
        ],
        nets=[Net(id="CC2", name="CC2"), Net(id="GND", name="GND")],
        connections=[
            Connection(pin_id="R70.1", net_id="CC2", provenance=Provenance.NETLIST),
            Connection(pin_id="R70.2", net_id="GND", provenance=Provenance.NETLIST),
            Connection(pin_id="R21.1", net_id="CC2", provenance=Provenance.NETLIST),
            Connection(pin_id="R21.2", net_id="GND", provenance=Provenance.NETLIST),
        ],
    )


def test_trace_component_query():
    graph = create_graph()
    graph.load(_sample_ir())
    result = trace_component(graph, "R70")
    assert result is not None
    assert result.component.id == "R70"
    assert len(result.pins) == 2
    net_ids = {item["net"].id for item in result.pins}
    assert net_ids == {"CC2", "GND"}


def test_trace_net_query():
    graph = create_graph()
    graph.load(_sample_ir())
    result = trace_net(graph, "CC2")
    assert result is not None
    assert result.net.id == "CC2"
    assert len(result.connections) == 2
    comp_ids = {item["component"].id for item in result.connections}
    assert comp_ids == {"R70", "R21"}


def test_neighbors_of_component():
    graph = create_graph()
    graph.load(_sample_ir())
    result = neighbors_of(graph, "R70")
    assert len(result) == 2
    ids = {r.node.id for r in result}
    assert ids == {"R70.1", "R70.2"}
    for r in result:
        assert len(r.edges) > 0
        assert "kind" in r.edges[0]
        assert "target" in r.edges[0]


def test_neighbors_of_net():
    graph = create_graph()
    graph.load(_sample_ir())
    result = neighbors_of(graph, "CC2")
    ids = {r.node.id for r in result}
    assert ids == {"R70.1", "R21.1"}


def test_traverse_bfs_query():
    graph = create_graph()
    graph.load(_sample_ir())
    result = traverse_bfs(graph, "R70")
    assert "nodes" in result
    assert "edges" in result
    assert "roots" in result
    assert result["roots"] == ["R70"]
    assert "R70" in result["nodes"]


def test_query_returns_none_for_missing():
    graph = create_graph()
    graph.load(_sample_ir())
    assert trace_component(graph, "MISSING") is None
    assert trace_net(graph, "MISSING") is None
    assert neighbors_of(graph, "MISSING") == []


def test_query_against_t268():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    graph = create_graph()
    graph.load(ir)

    r70 = trace_component(graph, "R70")
    assert r70 is not None
    assert r70.component.id == "R70"
    assert len(r70.pins) == 2

    cc2 = trace_net(graph, "CC2")
    assert cc2 is not None
    assert any(item["component"].id == "R70" for item in cc2.connections)

    neighbors = neighbors_of(graph, "R70")
    assert len(neighbors) == 2
    for n in neighbors:
        assert len(n.edges) > 0
        assert n.edges[0]["kind"] == "has_pin"

    bfs = traverse_bfs(graph, "R70")
    assert len(bfs["nodes"]) > 0
    assert len(bfs["edges"]) > 0
