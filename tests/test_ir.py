import json

import pytest

from circuit_graph.ir import (
    BlockRole,
    CapacitanceValue,
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    EdgeKind,
    FunctionalBlock,
    Net,
    NetType,
    NodeKind,
    Pin,
    Provenance,
    ResistanceValue,
    SemanticEdge,
    UnknownValue,
    ir_from_dict,
    ir_from_json,
    ir_to_dict,
    ir_to_json,
    make_pin_id,
    parse_pin_id,
)


def test_pin_id_helpers():
    assert make_pin_id("R70", 1) == "R70.1"
    assert make_pin_id("R70", "2") == "R70.2"
    assert parse_pin_id("R70.1") == ("R70", "1")
    assert parse_pin_id("R70") == (None, None)
    assert parse_pin_id(".1") == (None, None)


def test_pin_validates_id():
    pin = Pin(id="R70.1", number="1")
    assert pin.id == "R70.1"
    with pytest.raises(ValueError):
        Pin(id="R70", number="1")


def test_enums_serialize_as_strings():
    assert DeviceKind.RESISTOR.value == "Resistor"
    assert str(NetType.GND) == "gnd"
    assert EdgeKind.HAS_PIN.value == "has_pin"
    assert NodeKind.PIN.value == "pin"
    assert BlockRole.PD_CONTROLLER.value == "pd_controller"


def test_circuit_ir_round_trip():
    ir = CircuitIR(
        meta={"source": "test", "format": "manual"},
        components=[
            Component(
                id="R70",
                device_kind=DeviceKind.RESISTOR,
                footprint="0805C",
                raw_value="5.1K%1",
                value=ResistanceValue(magnitude=5100.0, unit="Ω", tolerance=0.01),
                pins=[Pin(id="R70.1", number="1"), Pin(id="R70.2", number="2")],
            ),
            Component(
                id="C1",
                device_kind=DeviceKind.CAPACITOR,
                footprint="0805C",
                raw_value="4.7UF16V",
                value=CapacitanceValue(magnitude=4.7, unit="µF", voltage_rating=16.0),
                pins=[Pin(id="C1.1", number="1"), Pin(id="C1.2", number="2")],
            ),
        ],
        nets=[Net(id="CC2", name="CC2", type=NetType.SIGNAL)],
        connections=[
            Connection(pin_id="R70.1", net_id="CC2", provenance=Provenance.NETLIST),
            Connection(pin_id="R70.2", net_id="GND", provenance=Provenance.NETLIST),
        ],
        blocks=[
            FunctionalBlock(
                id="type-c-cc-pull-cc2",
                name="Type-C CC CC2 Pull-down",
                role=BlockRole.PD_CONTROLLER,
                component_ids=["R70"],
            ),
        ],
    )

    json_text = ir_to_json(ir)
    parsed = json.loads(json_text)
    assert parsed["version"] == "0.1.0"
    assert parsed["meta"]["source"] == "test"
    assert parsed["components"][0]["device_kind"] == "Resistor"
    assert parsed["components"][0]["value"]["magnitude"] == 5100.0
    assert parsed["components"][1]["value"]["voltage_rating"] == 16.0
    assert parsed["nets"][0]["type"] == "signal"
    assert parsed["connections"][0]["provenance"] == "netlist"
    assert parsed["blocks"][0]["role"] == "pd_controller"

    restored = ir_from_json(json_text)
    assert restored.components[0].value == ResistanceValue(
        magnitude=5100.0, unit="Ω", tolerance=0.01
    )
    assert restored.components[1].value == CapacitanceValue(
        magnitude=4.7, unit="µF", voltage_rating=16.0
    )
    assert restored.nets[0].type == NetType.SIGNAL
    assert restored.connections[0].provenance == Provenance.NETLIST
    assert restored.blocks[0].role == BlockRole.PD_CONTROLLER


def test_unknown_value_round_trip():
    comp = Component(
        id="J1",
        device_kind=DeviceKind.CONNECTOR,
        raw_value="NC",
        value=UnknownValue(raw="NC"),
        pins=[Pin(id="J1.1", number="1")],
    )
    ir = CircuitIR(meta={"source": "test", "format": "manual"}, components=[comp])
    restored = ir_from_dict(ir_to_dict(ir))
    assert restored.components[0].value == UnknownValue(raw="NC")


def test_ir_from_dict_accepts_strings_for_enums():
    data = {
        "version": "0.1.0",
        "meta": {"source": "legacy", "format": "protel-net"},
        "components": [
            {
                "id": "R21",
                "device_kind": "Resistor",
                "pins": [{"id": "R21.1", "number": "1"}],
            }
        ],
        "nets": [{"id": "GND", "name": "GND", "type": "gnd"}],
        "connections": [{"pin_id": "R21.1", "net_id": "GND"}],
    }
    ir = ir_from_dict(data)
    assert ir.components[0].device_kind == DeviceKind.RESISTOR
    assert ir.nets[0].type == NetType.GND
    assert ir.connections[0].provenance == Provenance.NETLIST


def _base_doc() -> dict:
    """A doc shaped exactly like ir_to_dict output (all default fields present)."""
    ir = CircuitIR(
        meta=CircuitMeta(source="legacy", format="protel-net"),
        components=[
            Component(
                id="R21",
                device_kind=DeviceKind.RESISTOR,
                value=ResistanceValue(magnitude=100.0, unit="Ω"),
                pins=[Pin(id="R21.1", number="1")],
            )
        ],
        nets=[Net(id="GND", name="GND", type=NetType.GND)],
        connections=[Connection(pin_id="R21.1", net_id="GND")],
        blocks=[
            FunctionalBlock(id="b1", name="B1", role=BlockRole.UNKNOWN, component_ids=["R21"])
        ],
        semantic_edges=[
            SemanticEdge(source="R21", target="GND", kind="powered_by")
        ],
    )
    return ir_to_dict(ir)


def test_known_only_round_trip_byte_stable():
    """JSON with only known fields must round-trip without gaining new keys."""
    doc = _base_doc()
    assert ir_to_dict(ir_from_dict(doc)) == doc


def test_round_trip_preserves_unknown_fields():
    """Unknown fields at every level survive from_dict → to_dict verbatim."""
    doc = _base_doc()
    doc["version"] = "0.2.0"
    doc["future_top_level"] = {"x": 1}
    doc["meta"]["future_meta"] = "m"
    comp = doc["components"][0]
    comp["future_comp"] = [1, 2]
    comp["value"]["future_value"] = True
    comp["pins"][0]["future_pin"] = "p"
    comp["metadata"]["note"] = "keep me"
    doc["nets"][0]["future_net"] = 3
    doc["connections"][0]["future_conn"] = "c"
    doc["blocks"][0]["future_block"] = {"nested": [1]}
    doc["semantic_edges"][0]["future_edge"] = 0.5

    ir = ir_from_dict(doc)
    # unknowns are held in metadata, not dropped
    assert ir.metadata["_unknown_fields"]["future_top_level"] == {"x": 1}
    assert ir.meta.metadata["_unknown_fields"]["future_meta"] == "m"
    assert ir.components[0].metadata["note"] == "keep me"
    assert ir.components[0].metadata["_unknown_fields"]["future_comp"] == [1, 2]
    assert ir.components[0].pins[0].metadata["_unknown_fields"]["future_pin"] == "p"
    assert ir.components[0].value.metadata["_unknown_fields"]["future_value"] is True
    assert ir.blocks[0].metadata["_unknown_fields"]["future_block"] == {"nested": [1]}
    assert ir.version == "0.2.0"
    # and restored verbatim on serialization
    assert ir_to_dict(ir) == doc


def test_unknown_fields_do_not_override_known_schema():
    doc = {
        "version": "0.1.0",
        "meta": {"source": "legacy", "format": "protel-net"},
        "components": [],
        "nets": [],
        "connections": [],
    }
    ir = ir_from_dict(doc)
    ir_to_dict(ir)  # smoke: no crash
    assert ir.meta.source == "legacy"


def test_t268_fixture_parse_round_trip():
    """Real netlist: parse → to_dict → from_dict → to_dict is stable."""
    from pathlib import Path

    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    fixture = (
        Path(__file__).parent.parent
        / "fixtures"
        / "protel"
        / "FX-T268-V6-10米-5065.NET"
    )
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(source_id=str(fixture), file_path=str(fixture)))
    once = ir_to_dict(ir)
    twice = ir_to_dict(ir_from_dict(once))
    assert once == twice
