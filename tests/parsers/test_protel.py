from pathlib import Path

import pytest

from circuit_graph.ir import DeviceKind, Provenance
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "protel"


def test_can_parse_by_extension():
    parser = ProtelNetParser()
    assert parser.can_parse(ParserInput(file_path="board.NET")) is True
    assert parser.can_parse(ParserInput(file_path="board.net")) is True
    assert parser.can_parse(ParserInput(file_path="board.txt")) is False


def test_can_parse_by_content():
    parser = ProtelNetParser()
    content = "[\nR1\n0805C\n5.1K%1\n]\n(\nNET1\nR1-1\nR2-2\n)"
    assert parser.can_parse(ParserInput(content=content)) is True
    assert parser.can_parse(ParserInput(content="just some text")) is False


def test_parse_simple_netlist():
    content = """[
R1
0805C
5.1K%1
]
[
C1
0805C
4.7UF16V
]
(
VBUS
R1-1
C1-1
)
(
GND
R1-2
C1-2
)
"""
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(source_id="simple", content=content))

    assert len(ir.components) == 2
    assert ir.components[0].id == "R1"
    assert ir.components[0].device_kind == DeviceKind.RESISTOR
    assert ir.components[0].raw_value == "5.1K%1"
    assert ir.components[1].id == "C1"
    assert ir.components[1].device_kind == DeviceKind.CAPACITOR

    assert len(ir.nets) == 2
    assert {n.name for n in ir.nets} == {"VBUS", "GND"}

    assert len(ir.connections) == 4
    for conn in ir.connections:
        assert conn.provenance == Provenance.NETLIST

    r1 = next(c for c in ir.components if c.id == "R1")
    assert [p.number for p in r1.pins] == ["1", "2"]
    assert r1.pins[0].id == "R1.1"


def test_parse_infer_inductor_from_footprint():
    content = """[
L1
L4*4*1.8
COIL
]
(
NET1
L1-1
)
(
GND
L1-2
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    assert len(ir.components) == 1
    assert ir.components[0].device_kind == DeviceKind.INDUCTOR


def test_parse_sorts_pins_numerically():
    content = """[
U1
QFN32
UNKNOWN
]
(
A
U1-10
U1-2
U1-1
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    u1 = ir.components[0]
    assert [p.number for p in u1.pins] == ["1", "2", "10"]


def test_parse_t268_fixture():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(source_id=str(fixture), file_path=str(fixture)))

    assert len(ir.components) > 0
    assert len(ir.nets) > 0
    assert len(ir.connections) > 0

    # Spot-check known components.
    r70 = next((c for c in ir.components if c.id == "R70"), None)
    assert r70 is not None, "R70 should exist in T268"
    assert r70.device_kind == DeviceKind.RESISTOR
    assert [p.number for p in r70.pins] == ["1", "2"]

    cc2 = next((n for n in ir.nets if n.name == "CC2"), None)
    assert cc2 is not None, "CC2 net should exist in T268"

    # Every connection points to a known component/pin pair.
    component_ids = {c.id for c in ir.components}
    for conn in ir.connections:
        comp_id, pin_num = conn.pin_id.rsplit(".", 1)
        assert comp_id in component_ids
        comp = next(c for c in ir.components if c.id == comp_id)
        assert pin_num in {p.number for p in comp.pins}


def test_parse_skips_malformed_blocks():
    content = """[
]
(
EMPTY
)
[
R1
0805C
1K
]
(
NET1
R1-1
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    assert len(ir.components) == 1
    assert ir.components[0].id == "R1"
    assert len(ir.nets) == 2
    assert len(ir.connections) == 1


def test_infer_usb_c_connector_from_value_signature():
    # USB1: U-prefix designator, footprint TYPE-C1.6, value TYPE_C → CONNECTOR
    kind = ProtelNetParser._infer_device_kind("USB1", "TYPE-C1.6", "TYPE_C")
    assert kind == DeviceKind.CONNECTOR


def test_infer_usb_connector_from_footprint_signature():
    # USB2: U-prefix designator, footprint USB-8.6*1.9, value USB_4PIN_DIP → CONNECTOR
    kind = ProtelNetParser._infer_device_kind("USB2", "USB-8.6*1.9", "USB_4PIN_DIP")
    assert kind == DeviceKind.CONNECTOR


def test_infer_real_ic_u_prefix_unaffected():
    # Genuine ICs with U-prefix and no connector signature stay IC.
    assert ProtelNetParser._infer_device_kind("U7", "SOT23-5", "LM321") == DeviceKind.IC
    assert ProtelNetParser._infer_device_kind("U4", "SOT23-8", "JW5065") == DeviceKind.IC


def test_extract_part_number_only_for_ics():
    """The third line of a Protel component block carries a part number only for ICs."""
    assert ProtelNetParser._extract_part_number(DeviceKind.IC, "HT7533 -23") == "HT7533 -23"
    assert ProtelNetParser._extract_part_number(DeviceKind.IC, "JW5065") == "JW5065"
    assert ProtelNetParser._extract_part_number(DeviceKind.IC, "  LM321  ") == "LM321"
    assert ProtelNetParser._extract_part_number(DeviceKind.IC, "") is None
    assert ProtelNetParser._extract_part_number(DeviceKind.IC, None) is None
    # Passives never get a part number from raw_value (avoids leaking 4.7UF16V as a "part").
    assert ProtelNetParser._extract_part_number(DeviceKind.RESISTOR, "10K") is None
    assert ProtelNetParser._extract_part_number(DeviceKind.CAPACITOR, "4.7UF16V") is None
    assert ProtelNetParser._extract_part_number(DeviceKind.INDUCTOR, "10uH") is None


def test_t268_ic_part_numbers_populated():
    """End-to-end: parsing the T268 .NET yields IC part_numbers, not just raw_values."""
    from pathlib import Path

    fixture = Path("fixtures/protel/FX-T268-V6-10米-5065.NET")
    if not fixture.exists():
        pytest.skip(f"fixture not found: {fixture}")

    ir = ProtelNetParser().parse(ParserInput(source_id=str(fixture), file_path=str(fixture)))
    parts_by_id = {c.id: c.part_number for c in ir.components if c.part_number}

    # HT7533 (LDO) and JW5065 (Buck) are the canonical regulators on this board.
    assert parts_by_id.get("U1") == "HT7533 -23"
    assert parts_by_id.get("U4") == "JW5065"
    # Passives stay without a part_number.
    r_parts = [c for c in ir.components if c.device_kind == DeviceKind.RESISTOR and c.part_number]
    assert r_parts == [], f"resistors must not carry a part_number: {r_parts}"


def test_infer_designator_prefix_unchanged():
    # J-prefix connector and passive prefixes unchanged by the new override.
    assert ProtelNetParser._infer_device_kind("J1", "HDR-2", "HEADER") == DeviceKind.CONNECTOR
    assert ProtelNetParser._infer_device_kind("R70", "0805C", "5.1K%1") == DeviceKind.RESISTOR
    assert ProtelNetParser._infer_device_kind("C2", "0805C", "4.7UF16V") == DeviceKind.CAPACITOR
    assert ProtelNetParser._infer_device_kind("L1", "L4*4*1.8", "COIL") == DeviceKind.INDUCTOR
