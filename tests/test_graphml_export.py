"""Tests for GraphML export."""

from pathlib import Path

import networkx as nx
import pytest

from circuit_graph.exporters.graphml import write_graphml
from circuit_graph.ir import (
    BlockRole,
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    FunctionalBlock,
    Net,
    NetType,
    Pin,
    ResistanceValue,
)
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protel"


def _sample_ir() -> CircuitIR:
    """Return a small IR with a resistor and a net."""
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[
            Component(
                id="R70",
                device_kind=DeviceKind.RESISTOR,
                footprint="0402R",
                value=ResistanceValue(magnitude=5.1, unit="kΩ"),
                raw_value="5.1K",
                pins=[
                    Pin(id="R70.1", number="1"),
                    Pin(id="R70.2", number="2"),
                ],
            )
        ],
        nets=[Net(id="CC2", name="CC2", type=NetType.SIGNAL)],
        connections=[
            Connection(pin_id="R70.1", net_id="CC2"),
        ],
        blocks=[
            FunctionalBlock(
                id="type-c-cc-pull-cc2",
                name="Type-C CC pull",
                role=BlockRole.UNKNOWN,
                component_ids=["R70"],
            )
        ],
    )


def test_write_graphml_creates_file(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)
    assert output.exists()
    assert output.stat().st_size > 0


def test_graphml_node_coverage(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    assert "R70" in g.nodes
    assert "R70.1" in g.nodes
    assert "R70.2" in g.nodes
    assert "CC2" in g.nodes
    assert "type-c-cc-pull-cc2" in g.nodes


def test_graphml_node_kinds(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    assert g.nodes["R70"]["kind"] == "component"
    assert g.nodes["R70.1"]["kind"] == "pin"
    assert g.nodes["CC2"]["kind"] == "net"
    assert g.nodes["type-c-cc-pull-cc2"]["kind"] == "functional_block"


def test_graphml_component_attributes(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    r70 = g.nodes["R70"]
    assert r70["device_kind"] == "Resistor"
    assert r70["raw_value"] == "5.1K"
    assert "5.1" in r70["value"]


def test_graphml_has_pin_edge(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    assert g.has_edge("R70", "R70.1")
    assert g.edges["R70", "R70.1"]["kind"] == "has_pin"


def test_graphml_connects_to_edge(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    assert g.has_edge("R70.1", "CC2")
    assert g.edges["R70.1", "CC2"]["kind"] == "connects_to"


def test_graphml_belongs_to_edge(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    assert g.has_edge("R70", "type-c-cc-pull-cc2")
    assert g.edges["R70", "type-c-cc-pull-cc2"]["kind"] == "belongs_to"


def test_graphml_net_attributes(tmp_path: Path) -> None:
    output = tmp_path / "out.graphml"
    write_graphml(_sample_ir(), output)

    g = nx.read_graphml(output)
    cc2 = g.nodes["CC2"]
    assert cc2["net_name"] == "CC2"
    assert cc2["net_type"] == "signal"


def test_graphml_xml_safety(tmp_path: Path) -> None:
    """Ensure GraphML is valid XML even with special characters."""
    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[
            Component(
                id="R1",
                device_kind=DeviceKind.RESISTOR,
                raw_value="1K < 2K & 3K",
                pins=[Pin(id="R1.1", number="1")],
            )
        ],
        nets=[Net(id="NET1", name="NET1")],
        connections=[Connection(pin_id="R1.1", net_id="NET1")],
    )
    output = tmp_path / "special.graphml"
    write_graphml(ir, output)

    # Should be parseable by ElementTree (strict XML)
    import xml.etree.ElementTree as ET

    tree = ET.parse(output)
    root = tree.getroot()
    assert root.tag.endswith("graphml")


def test_graphml_from_t268(tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    ir = run_enrichers(ir, [ValueNormalizer(), NetClassifier(), TopologyRecognizer()]).ir

    output = tmp_path / "t268.graphml"
    write_graphml(ir, output)

    g = nx.read_graphml(output)
    assert "R70" in g.nodes
    assert g.nodes["R70"]["kind"] == "component"
    assert g.nodes["R70"]["device_kind"] == "Resistor"

    # Functional blocks should be present after enrichment.
    block_nodes = [n for n, d in g.nodes(data=True) if d.get("kind") == "functional_block"]
    assert len(block_nodes) > 0

    # Edges of each required kind should exist.
    kinds = {d["kind"] for _u, _v, d in g.edges(data=True)}
    assert "has_pin" in kinds
    assert "connects_to" in kinds
    assert "belongs_to" in kinds
