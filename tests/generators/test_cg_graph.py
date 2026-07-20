from pathlib import Path

import pytest

from circuit_graph.generators.cg_graph import (
    RAIL_DEGREE_THRESHOLD,
    generate_cg_graph,
    view_component,
    write_cg_graph,
)
from circuit_graph.ir import (
    BlockRole,
    CapacitanceValue,
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

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "protel"


def _sample_ir() -> CircuitIR:
    """Return a small enriched IR for cg-graph tests."""
    return CircuitIR(
        meta=CircuitMeta(source="test/sample.net", format="manual"),
        components=[
            Component(
                id="R70",
                device_kind=DeviceKind.RESISTOR,
                footprint="0805C",
                raw_value="5.1K%1",
                value=ResistanceValue(magnitude=5.1, unit="kΩ", tolerance=0.01),
                pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
            ),
            Component(
                id="C1",
                device_kind=DeviceKind.CAPACITOR,
                footprint="0805C",
                raw_value="4.7UF16V",
                value=CapacitanceValue(magnitude=4.7, unit="µF", voltage_rating=16),
                pins=[Pin(id="C1.1", number="1"), Pin(id="C1.2", number="2")],
            ),
            Component(
                id="U7",
                device_kind=DeviceKind.IC,
                footprint="QFN-32",
                raw_value="LM321",
                pins=[Pin(id="U7.1", number="1"), Pin(id="U7.2", number="2")],
            ),
            Component(
                id="Q1",
                device_kind=DeviceKind.MOSFET,
                footprint="SOT-23",
                raw_value="2N7002",
                pins=[Pin(id="Q1.1", number="1"), Pin(id="Q1.2", number="2")],
            ),
            Component(
                id="X1",
                device_kind=DeviceKind.UNKNOWN,
                footprint=None,
                raw_value=None,
                pins=[Pin(id="X1.1", number="1")],
            ),
        ],
        nets=[
            Net(id="CC2", name="CC2", type=NetType.SIGNAL),
            Net(id="PGND", name="PGND", type=NetType.GND),
            Net(id="VBUS", name="VBUS", type=NetType.POWER),
            Net(id="N1", name="N1", type=NetType.UNKNOWN),
        ],
        connections=[
            Connection(pin_id="R70.1", net_id="CC2"),
            Connection(pin_id="R70.2", net_id="PGND"),
            Connection(pin_id="C1.1", net_id="VBUS"),
            Connection(pin_id="C1.2", net_id="PGND"),
            Connection(pin_id="U7.1", net_id="CC2"),
            Connection(pin_id="U7.2", net_id="VBUS"),
            Connection(pin_id="Q1.1", net_id="CC2"),
            Connection(pin_id="Q1.2", net_id="N1"),
            Connection(pin_id="X1.1", net_id="N1"),
        ],
        blocks=[
            FunctionalBlock(
                id="type-c-cc-pull-cc2",
                name="Type-C CC CC2 Pull-down",
                role=BlockRole.PD_CONTROLLER,
                component_ids=["R70"],
                description="CC pull-down resistors on CC2",
            )
        ],
    )


def test_format_and_version():
    data = generate_cg_graph(_sample_ir())
    assert data["format"] == "cg-graph"
    assert data["version"] == "2.1"
    for key in ("format", "version", "circuit", "components", "nets"):
        assert key in data


def test_component_entry_shape():
    data = generate_cg_graph(_sample_ir())
    r70 = data["components"]["R70"]
    assert r70["id"] == "R70"
    assert r70["type"] == "resistor"
    assert "value" in r70
    assert r70["ohms"] == 5100.0
    assert r70["package"] == "0805C"
    assert "pins" in r70
    assert "topology_hint" not in r70
    assert "part_number" not in r70

    u7 = data["components"]["U7"]
    assert u7["id"] == "U7"
    assert u7["type"] == "ic"
    assert u7["part_number"] == "LM321"
    assert "value" not in u7
    assert "topology_hint" not in u7

    q1 = data["components"]["Q1"]
    assert q1["type"] == "mosfet"
    assert q1["part_number"] == "2N7002"
    assert "value" not in q1


def test_pins_reference_net_only_no_same_net():
    data = generate_cg_graph(_sample_ir())
    r70 = data["components"]["R70"]
    for pin in r70["pins"]:
        assert set(pin.keys()) == {"pin", "net"}
        assert "same_net" not in pin
        assert "net_kind" not in pin
    cc2_pin = next(p for p in r70["pins"] if p["net"] == "CC2")
    assert cc2_pin["pin"] == "1"


def test_net_entry_kind_degree_members_ids_only():
    data = generate_cg_graph(_sample_ir())
    cc2 = data["nets"]["CC2"]
    assert cc2["kind"] == "signal"
    assert set(cc2["members"]) == {"R70", "U7", "Q1"}
    assert cc2["degree"] == len(cc2["members"])
    assert cc2["degree"] == 3
    # members are plain id strings, no inline type/value
    for member in cc2["members"]:
        assert isinstance(member, str)


def test_no_inline_neighbor_type_value_anywhere():
    data = generate_cg_graph(_sample_ir())
    # A neighbour's type/value must live only in its own component entry.
    for comp in data["components"].values():
        for pin in comp["pins"]:
            assert "type" not in pin
            assert "value" not in pin
    for net in data["nets"].values():
        for member in net["members"]:
            assert isinstance(member, str)


def test_gnd_normalized_to_ground():
    data = generate_cg_graph(_sample_ir())
    assert data["nets"]["PGND"]["kind"] == "ground"


def test_unknown_type_and_kind_omitted():
    data = generate_cg_graph(_sample_ir())
    assert "type" not in data["components"]["X1"]
    assert "kind" not in data["nets"]["N1"]


def test_no_unknown_field_values():
    data = generate_cg_graph(_sample_ir())
    for comp in data["components"].values():
        if "type" in comp:
            assert comp["type"] != "unknown"
    for net in data["nets"].values():
        if "kind" in net:
            assert net["kind"] != "unknown"


def test_no_topology_hint_anywhere():
    data = generate_cg_graph(_sample_ir())
    for comp in data["components"].values():
        assert "topology_hint" not in comp


def test_part_number_and_value_are_mutually_exclusive():
    data = generate_cg_graph(_sample_ir())
    for comp in data["components"].values():
        has_value = "value" in comp
        has_part_number = "part_number" in comp
        assert not (has_value and has_part_number)


def test_circuit_block_shape():
    data = generate_cg_graph(_sample_ir())
    circuit = data["circuit"]
    assert circuit["name"] == "sample"
    assert circuit["total_components"] == 5
    assert circuit["total_nets"] == 4
    assert "main_ics" in circuit
    assert circuit["main_ics"] == ["U7"]
    assert "blocks" in circuit
    assert "Type-C CC CC2 Pull-down" in circuit["blocks"]
    assert "(heuristic)" in circuit["summary"]


def test_no_placeholder_values():
    data = generate_cg_graph(_sample_ir())

    def _check(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert v is not None, f"key {k} has null value"
                assert v != -1
                assert v != ""
                _check(v)
        elif isinstance(obj, list):
            for item in obj:
                _check(item)

    _check(data)


def test_view_component_default_hops_and_focal():
    ir = _sample_ir()
    view = view_component(ir, "R70")
    assert view is not None
    assert view["version"] == "2.1"
    assert view["designator"] == "R70"
    assert view["focal"] == "R70"
    assert view["hops"] == 2
    assert view["components"]["R70"]["id"] == "R70"
    assert "topology_hint" not in view["components"]["R70"]
    # Reference-based representation: nets carry degree + id members.
    assert "U7" in view["components"]
    cc2 = view["nets"]["CC2"]
    assert cc2["kind"] == "signal"
    assert cc2["degree"] == len(cc2["members"])
    assert "members" in cc2


def test_view_component_unknown_returns_none():
    assert view_component(_sample_ir(), "R999") is None


def test_write_cg_graph(tmp_path: Path) -> None:
    output = tmp_path / "out.graph.json"
    write_cg_graph(_sample_ir(), output)
    assert output.exists()
    import json

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["format"] == "cg-graph"
    assert data["version"] == "2.1"
    assert "R70" in data["components"]


def test_generate_from_t268_no_placeholders():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)

    def _check(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert v is not None, f"key {k} has null value"
                assert v != -1
                assert v != ""
                _check(v)
        elif isinstance(obj, list):
            for item in obj:
                _check(item)

    _check(data)

    assert data["circuit"]["total_components"] == len(ir.components)
    assert data["circuit"]["total_nets"] == len(ir.nets)


def test_t268_degree_equals_members_everywhere():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    for name, net in data["nets"].items():
        assert net["degree"] == len(net["members"]), name
        for member in net["members"]:
            assert isinstance(member, str)


def test_t268_cgnd_preserved_with_q7():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    assert "CGND" in data["nets"], "CGND must be listed, not collapsed"
    cgnd = data["nets"]["CGND"]
    assert "members" in cgnd
    assert cgnd["degree"] == len(cgnd["members"])
    assert "Q7" in cgnd["members"]


def test_t268_gnd_lists_full_members_and_degree():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    assert "GND" in data["nets"]
    gnd = data["nets"]["GND"]
    assert gnd["kind"] == "ground"
    assert gnd["degree"] == len(gnd["members"])
    assert gnd["degree"] > RAIL_DEGREE_THRESHOLD  # genuine high-fanout rail


def test_t268_rails():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    rails = data["circuit"]["rails"]
    assert isinstance(rails, list)
    assert rails == sorted(rails)
    assert "GND" in rails
    assert "USB_5V" in rails
    assert "CGND" not in rails


def test_t268_ic_part_number_and_no_value():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    u7 = data["components"]["U7"]
    assert u7["type"] == "ic"
    assert u7["part_number"] == "LM321"
    assert "value" not in u7


def test_t268_mosfet_part_number():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    q1 = data["components"]["Q1"]
    assert q1["part_number"] == "2N7002"


def test_t268_passive_value_not_part_number():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    r70 = data["components"]["R70"]
    assert "value" in r70
    assert "ohms" in r70
    assert "part_number" not in r70


def test_t268_no_topology_hint():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    for comp in data["components"].values():
        assert "topology_hint" not in comp


def test_t268_no_unknown_field_values():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    data = generate_cg_graph(ir)
    for comp in data["components"].values():
        if "type" in comp:
            assert comp["type"] != "unknown"
    for net in data["nets"].values():
        if "kind" in net:
            assert net["kind"] != "unknown"


def test_view_component_t268_r100_traversal():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    view = view_component(ir, "R100")
    assert view is not None
    assert view["focal"] == "R100"
    assert view["hops"] == 2

    # GND (high fanout) is listed but its members are NOT all expanded.
    if "GND" in view["nets"]:
        gnd = view["nets"]["GND"]
        assert gnd["degree"] >= RAIL_DEGREE_THRESHOLD
        expanded = set(view["components"].keys())
        # Not every GND member becomes a focal-graph component entry.
        assert not set(gnd["members"]).issubset(expanded)

    # hops=2 through low-fanout signal nets reaches the MCU and op-amp gain net.
    assert "U2" in view["components"], "AIN10 path should reach MCU U2"
    assert "R102" in view["components"]
    assert "C82" in view["components"]


def test_view_component_t268_unknown_returns_none():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    assert view_component(ir, "R999") is None
