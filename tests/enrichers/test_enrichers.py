from pathlib import Path

import pytest

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.enrichers.value_normalizer import ValueNormalizer
from circuit_graph.ir import (
    BlockRole,
    CapacitanceValue,
    CircuitIR,
    Component,
    Connection,
    DeviceKind,
    EdgeKind,
    Net,
    NetType,
    Pin,
    ResistanceValue,
    UnknownValue,
)
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "protel"


def _sample_ir() -> CircuitIR:
    return CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="R70",
                device_kind=DeviceKind.RESISTOR,
                raw_value="5.1K%1",
                pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
            ),
            Component(
                id="C1",
                device_kind=DeviceKind.CAPACITOR,
                raw_value="4.7UF16V",
                pins=[Pin(id="C1.1", number="1"), Pin(id="C1.2", number="2")],
            ),
            Component(
                id="L1",
                device_kind=DeviceKind.INDUCTOR,
                raw_value="4.7uH",
                pins=[Pin(id="L1.1", number="1"), Pin(id="L1.2", number="2")],
            ),
            Component(
                id="R71",
                device_kind=DeviceKind.RESISTOR,
                raw_value="NC",
                pins=[Pin(id="R71.1", number="1"), Pin(id="R71.2", number="2")],
            ),
        ],
        nets=[
            Net(id="CC2", name="CC2"),
            Net(id="PGND", name="PGND"),
            Net(id="VBUS", name="VBUS"),
            Net(id="Mystery", name="Mystery"),
        ],
        connections=[
            Connection(pin_id="R70.1", net_id="CC2"),
            Connection(pin_id="R70.2", net_id="PGND"),
            Connection(pin_id="R71.1", net_id="CC2"),
            Connection(pin_id="R71.2", net_id="PGND"),
        ],
    )


def test_value_normalizer():
    ir = _sample_ir()
    result = run_enrichers(ir, [ValueNormalizer()])
    assert not result.warnings
    r70 = next(c for c in result.ir.components if c.id == "R70")
    assert r70.value == ResistanceValue(magnitude=5100.0, unit="Ω", tolerance=0.01)
    c1 = next(c for c in result.ir.components if c.id == "C1")
    assert c1.value == CapacitanceValue(magnitude=4.7, unit="µF", voltage_rating=16.0)
    l1 = next(c for c in result.ir.components if c.id == "L1")
    assert l1.value.magnitude == 4.7
    r71 = next(c for c in result.ir.components if c.id == "R71")
    assert r71.value == UnknownValue(raw="NC")


def test_value_normalizer_resistor_kilo_mega_suffixes():
    """K/M suffixes must be treated as unit multipliers, not tolerance codes."""
    ir = CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="R100",
                device_kind=DeviceKind.RESISTOR,
                raw_value="100K",
                pins=[Pin(id="R100.1", number="1"), Pin(id="R100.2", number="2")],
            ),
            Component(
                id="R101",
                device_kind=DeviceKind.RESISTOR,
                raw_value="3K",
                pins=[Pin(id="R101.1", number="1"), Pin(id="R101.2", number="2")],
            ),
            Component(
                id="R97",
                device_kind=DeviceKind.RESISTOR,
                raw_value="1K",
                pins=[Pin(id="R97.1", number="1"), Pin(id="R97.2", number="2")],
            ),
            Component(
                id="R1M",
                device_kind=DeviceKind.RESISTOR,
                raw_value="1M",
                pins=[Pin(id="R1M.1", number="1"), Pin(id="R1M.2", number="2")],
            ),
            Component(
                id="R10R",
                device_kind=DeviceKind.RESISTOR,
                raw_value="10R%1",
                pins=[Pin(id="R10R.1", number="1"), Pin(id="R10R.2", number="2")],
            ),
            Component(
                id="R002",
                device_kind=DeviceKind.RESISTOR,
                raw_value="0.02R%1",
                pins=[Pin(id="R002.1", number="1"), Pin(id="R002.2", number="2")],
            ),
        ],
        nets=[],
        connections=[],
    )
    result = run_enrichers(ir, [ValueNormalizer()])
    values = {c.id: c.value for c in result.ir.components}
    assert values["R100"] == ResistanceValue(magnitude=100_000.0, unit="Ω")
    assert values["R101"] == ResistanceValue(magnitude=3000.0, unit="Ω")
    assert values["R97"] == ResistanceValue(magnitude=1000.0, unit="Ω")
    assert values["R1M"] == ResistanceValue(magnitude=1_000_000.0, unit="Ω")
    assert values["R10R"] == ResistanceValue(magnitude=10.0, unit="Ω", tolerance=0.01)
    assert values["R002"] == ResistanceValue(magnitude=0.02, unit="Ω", tolerance=0.01)


def test_net_classifier():
    ir = _sample_ir()
    result = run_enrichers(ir, [NetClassifier()])
    types = {n.id: n.type for n in result.ir.nets}
    assert types["PGND"] == NetType.GND
    assert types["VBUS"] == NetType.POWER
    assert types["CC2"] == NetType.SIGNAL
    assert types["Mystery"] == NetType.UNKNOWN


def test_net_classifier_records_classification_metadata():
    """Classified nets carry provenance; unknown nets carry nothing."""
    ir = _sample_ir()
    result = run_enrichers(ir, [NetClassifier()])
    by_id = {n.id: n for n in result.ir.nets}
    assert by_id["PGND"].metadata["classification"] == {
        "source": "name_heuristic",
        "confidence": "high",
    }
    assert by_id["VBUS"].metadata["classification"]["confidence"] == "high"
    assert by_id["CC2"].metadata["classification"]["confidence"] == "medium"
    assert "classification" not in by_id["Mystery"].metadata


def test_enricher_pipeline_preserves_top_level_metadata():
    """Enrichers must not drop CircuitIR.metadata (unknown-fields carrier)."""
    from dataclasses import replace

    ir = replace(_sample_ir(), metadata={"_unknown_fields": {"future": 1}})
    result = run_enrichers(ir, [NetClassifier(), ValueNormalizer(), TopologyRecognizer()])
    assert result.ir.metadata == {"_unknown_fields": {"future": 1}}


def test_net_classifier_treats_cgnd_as_gnd():
    """CGND (chassis / common ground) is a ground net, not digital/unknown.

    Without this, R96 (GND<->CGND bridge) on the T268 fixture never reaches
    primitives that accept gnd-role nets (e.g. current-shunt-monitor).
    """
    ir = CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[],
        nets=[Net(id="N1", name="CGND")],
        connections=[],
    )
    result = run_enrichers(ir, [NetClassifier()])
    assert result.ir.nets[0].type == NetType.GND


def test_topology_recognizer_cc_pull_down():
    ir = _sample_ir()
    result = run_enrichers(ir, [NetClassifier(), TopologyRecognizer()])
    block_ids = {b.id for b in result.ir.blocks}
    assert "type-c-cc-pull-down-r70" in block_ids
    block = next(b for b in result.ir.blocks if b.id == "type-c-cc-pull-down-r70")
    assert block.role == BlockRole.PD_CONTROLLER
    assert "R70" in block.component_ids
    assert "R71" in block.component_ids
    assert "confidence=medium" in block.description


def test_enricher_dependency_missing():
    ir = _sample_ir()
    # TopologyRecognizer requires NetClassifier; running it alone should warn.
    recognizer = TopologyRecognizer()
    recognizer.requires = ["NetClassifier"]
    result = run_enrichers(ir, [recognizer])
    assert any("ENRICH_DEPENDENCY_MISSING" in w for w in result.warnings)


def test_enricher_skips_when_can_enrich_false():
    ir = _sample_ir()
    nets = [Net(id=n.id, name=n.name, type=NetType.SIGNAL) for n in ir.nets]
    ir = CircuitIR(
        meta=ir.meta,
        components=ir.components,
        nets=nets,
        connections=ir.connections,
    )
    result = run_enrichers(ir, [NetClassifier()])
    assert any("skipped (canEnrich=false)" in w for w in result.warnings)


def test_enricher_pipeline_on_t268():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    result = run_enrichers(ir, [ValueNormalizer(), NetClassifier(), TopologyRecognizer()])

    # Spot-check normalized values.
    r70 = next((c for c in result.ir.components if c.id == "R70"), None)
    assert r70 is not None
    assert r70.value is not None
    assert r70.value.kind == "resistance"

    # Spot-check net classification.
    pgnd = next((n for n in result.ir.nets if n.name == "PGND"), None)
    assert pgnd is not None
    assert pgnd.type == NetType.GND
    power = next((n for n in result.ir.nets if n.name == "USB_5V"), None)
    assert power is not None
    assert power.type == NetType.POWER
    cc2 = next((n for n in result.ir.nets if n.name == "CC2"), None)
    assert cc2 is not None
    assert cc2.type == NetType.SIGNAL

    # Topology recognizer should create CC pull-down blocks.
    block_ids = {b.id for b in result.ir.blocks}
    assert "type-c-cc-pull-down-r1" in block_ids
    assert "type-c-cc-pull-down-r21" in block_ids

    # Each block should carry confidence and score evidence.
    for b in result.ir.blocks:
        assert "confidence=" in b.description
        assert "score=" in b.description

    # Pipeline should not produce dependency warnings.
    assert not any("ENRICH_DEPENDENCY_MISSING" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# EdgeSemanticEnricher tests (todolist_fix.md P2-Task4)


def _ic_ir() -> CircuitIR:
    """IR covering all major edge-semantic classification branches."""
    return CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            # Op-amp with named pins
            Component(
                id="U1",
                device_kind=DeviceKind.IC,
                part_number="LM321",
                pins=[
                    Pin(id="U1.3", number="3", name="+IN"),
                    Pin(id="U1.4", number="4", name="OUT"),
                    Pin(id="U1.5", number="5", name="VCC"),
                    Pin(id="U1.2", number="2", name="GND"),
                ],
            ),
            # Passive resistor
            Component(
                id="R1",
                device_kind=DeviceKind.RESISTOR,
                raw_value="10K",
                pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
            ),
        ],
        nets=[
            Net(id="+5V", name="+5V"),
            Net(id="GND", name="GND"),
            Net(id="SIG", name="SIG"),
        ],
        connections=[
            Connection(pin_id="U1.3", net_id="SIG"),
            Connection(pin_id="U1.4", net_id="SIG"),
            Connection(pin_id="U1.5", net_id="+5V"),
            Connection(pin_id="U1.2", net_id="GND"),
            Connection(pin_id="R1.1", net_id="SIG"),
            Connection(pin_id="R1.2", net_id="GND"),
        ],
    )


def _sem_for(conns, pin_id: str) -> dict:
    conn = next(c for c in conns if c.pin_id == pin_id)
    return conn.metadata["edge_semantic"]


def test_edge_semantic_enricher_classifies_directions():
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher

    result = run_enrichers(_ic_ir(), [NetClassifier(), EdgeSemanticEnricher()])
    conns = result.ir.connections

    # IC input on signal net → load
    assert _sem_for(conns, "U1.3")["direction"] == "load"
    # IC output on signal net → source
    assert _sem_for(conns, "U1.4")["direction"] == "source"
    # Power pin → bidirectional regardless of name
    assert _sem_for(conns, "U1.5")["direction"] == "bidirectional"
    # GND pin → bidirectional
    assert _sem_for(conns, "U1.2")["direction"] == "bidirectional"
    # Resistor on signal net → bidirectional (current flows through)
    assert _sem_for(conns, "R1.1")["direction"] == "bidirectional"
    # Resistor on GND net → bidirectional
    assert _sem_for(conns, "R1.2")["direction"] == "bidirectional"


def test_edge_semantic_enricher_classifies_roles_and_levels():
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher

    result = run_enrichers(_ic_ir(), [NetClassifier(), EdgeSemanticEnricher()])
    conns = result.ir.connections

    assert _sem_for(conns, "U1.5")["role"] == "power"
    assert _sem_for(conns, "U1.5")["level"] == "5V"
    assert _sem_for(conns, "U1.2")["role"] == "gnd"
    assert _sem_for(conns, "U1.2")["level"] is None  # no voltage on GND name
    assert _sem_for(conns, "U1.3")["role"] == "digital"
    assert _sem_for(conns, "U1.3")["level"] is None


def test_edge_semantic_propagates_to_graph_edge_metadata():
    """Connection.metadata['edge_semantic'] must reach GraphEdge.metadata."""
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.graph import create_graph

    ir = NetClassifier().enrich(_ic_ir())
    ir = EdgeSemanticEnricher().enrich(ir)
    g = create_graph()
    g.load(ir)

    # Locate the U1.4 → SIG edge (U1.OUT named pin)
    sem_edges = [
        e
        for e in g.edges()
        if e.kind == EdgeKind.CONNECTS_TO and e.source == "U1.4"
    ]
    assert len(sem_edges) == 1
    sem = sem_edges[0].metadata.get("edge_semantic")
    assert sem is not None
    assert sem["direction"] == "source"
    assert sem["role"] == "digital"


def test_edge_semantic_enricher_is_idempotent():
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher

    ir = NetClassifier().enrich(_ic_ir())
    enricher = EdgeSemanticEnricher()
    once = enricher.enrich(ir)
    twice = enricher.enrich(once)
    # Second run should be a no-op (can_enrich returns False, ir passes through)
    assert once.connections == twice.connections
