"""Tests for the PROTEL NETLIST 2.0 parser.

The v2 parser extends ``ProtelNetParser`` with auto-detection of the
``PROTEL NETLIST 2.0`` header and a rich key/value component block plus a
3-token net block format.  These tests cover:

* Header detection & legacy fallback
* v2 component block parsing (DESIGNATOR / FOOTPRINT / PARTTYPE / Manufacturer
  / metadata retention)
* v2 net block parsing (pin name & electrical type promotion)
* End-to-end fixture: ``Netlist_handWarmer_sch_2026-09-26.net``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.ir import DeviceKind, Provenance
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "protel"


# ---------- header detection ----------


def test_is_v2_true_for_v2_header():
    content = "PROTEL NETLIST 2.0\n[\nDESIGNATOR\nR1\n]\n"
    assert ProtelNetParser._is_v2(content) is True


def test_is_v2_false_for_legacy():
    content = "[\nR1\n0805C\n5.1K%1\n]\n(\nNET1\nR1-1\n)"
    assert ProtelNetParser._is_v2(content) is False


def test_is_v2_false_for_legacy_with_blank_lines():
    # Legacy exports can have leading blank lines / BOM markers.
    content = "\n  \n[\nR1\n0805C\n5.1K%1\n]\n"
    assert ProtelNetParser._is_v2(content) is False


def test_is_v2_ignores_non_header_lines_with_v2_token():
    # "PROTEL NETLIST 2.0" inside a component description must not flip.
    content = "[\nR1\n0805C\nuses PROTEL NETLIST 2.0 in desc\n]\n"
    assert ProtelNetParser._is_v2(content) is False


# ---------- v2 component block parsing ----------


def test_v2_component_block_extracts_id_footprint_value():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
R5
FOOTPRINT
R0402
PARTTYPE
FRC0402F1503TS
Device
FRC0402F1503TS
PCB Layer
Default
Symbol
0603WAF1001T5E
Designator
R5
Datasheet
https://example.com/x.pdf
Unique ID
gge10_1
LCSC Part Name
150kΩ ±1% 62.5mW 厚膜电阻
Name
150kΩ
Manufacturer
FOJAN(富捷)
Manufacturer Part
FRC0402F1503TS
Supplier Footprint
0402
Tolerance
±1%
Value
150kΩ
Power(Watts)
62.5mW
*

]
"""
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(content=content))
    r5 = next(c for c in ir.components if c.id == "R5")
    assert r5.id == "R5"
    assert r5.footprint == "R0402"
    assert r5.raw_value == "150kΩ"
    assert r5.manufacturer == "FOJAN(富捷)"
    # Resistors must NOT get a part_number (mirrors legacy parser's intent).
    assert r5.part_number is None
    # Pin list inferred from the (empty) net block — there are no nets.
    assert r5.pins == []
    # Metadata retains all non-promoted keys.
    assert r5.metadata["Datasheet"] == "https://example.com/x.pdf"
    assert r5.metadata["Tolerance"] == "±1%"
    assert r5.metadata["Power(Watts)"] == "62.5mW"
    assert r5.metadata["Unique ID"] == "gge10_1"
    assert r5.metadata["LCSC Part Name"].startswith("150kΩ")
    # Promoted fields must NOT leak into metadata.
    assert "DESIGNATOR" not in r5.metadata
    assert "Manufacturer" not in r5.metadata
    assert "Manufacturer Part" not in r5.metadata


def test_v2_ic_gets_manufacturer_part_as_part_number():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
U1
FOOTPRINT
ESOP-8_L4.9-W3.9-P1.27-LS6.0-BL-EP3.1
PARTTYPE
TP4056_C21713961
Device
TP4056_C21713961
Name
TP4056
Manufacturer
GOODWORK(固得沃克)
Manufacturer Part
TP4056
*

]
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    u1 = next(c for c in ir.components if c.id == "U1")
    assert u1.device_kind == DeviceKind.IC
    assert u1.part_number == "TP4056"
    assert u1.manufacturer == "GOODWORK(固得沃克)"


def test_v2_capacitor_uses_name_field_as_raw_value():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
C4
FOOTPRINT
C0402
PARTTYPE
CL05B104KO5NNNC
Device
CL05B104KO5NNNC
Symbol
Capacitance
Value
100nF
Name
100nF
*

]
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    c4 = next(c for c in ir.components if c.id == "C4")
    assert c4.device_kind == DeviceKind.CAPACITOR
    assert c4.raw_value == "100nF"
    assert c4.part_number is None


def test_v2_led_classified_as_diode():
    """LED* designators must NOT be misclassified as Inductor."""
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
LED3
FOOTPRINT
0402LED8
PARTTYPE
E6X0402CWAY1UDA(4500-5500K)
Device
E6X0402CWAY1UDA(4500-5500K)
*

]
(
L3
LED3-1 E6X0402CWAY1UDA(4500-5500K)-A Passive
LED3-2 E6X0402CWAY1UDA(4500-5500K)-K Passive
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    led = next(c for c in ir.components if c.id == "LED3")
    assert led.device_kind == DeviceKind.DIODE
    assert led.part_number == "E6X0402CWAY1UDA(4500-5500K)"


# ---------- v2 net block parsing ----------


def test_v2_net_block_promotes_pin_name_and_type():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
LED3
FOOTPRINT
0402LED8
*
]
(
LED_NET
LED3-1 E6X0402CWAY1UDA(4500-5500K)-A Passive
LED3-2 E6X0402CWAY1UDA(4500-5500K)-K Passive
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    led = next(c for c in ir.components if c.id == "LED3")
    pins = {p.number: p for p in led.pins}
    assert pins["1"].name == "A"
    assert pins["2"].name == "K"
    assert pins["1"].type == "passive"
    assert pins["2"].type == "passive"


def test_v2_net_block_maps_input_to_input_type():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
R5
FOOTPRINT
R0402
PARTTYPE
Dummy150k
*
]
[
DESIGNATOR
U2
FOOTPRINT
SOP-16
PARTTYPE
mcu_sop16
*
]
(
DET_BUS
R5-2 FRC0402F1503TS-2 Input
U2-2 mcu_sop16-2 Input
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    r5 = next(c for c in ir.components if c.id == "R5")
    pins = {p.number: p for p in r5.pins}
    # pin2 is on DET_BUS, type "Input" → "input".
    assert pins["2"].type == "input"
    # The name is "2" which equals the pin number; we suppress that name so
    # the PinNameResolver enricher can still drive KB enrichment for ICs.
    assert pins["2"].name is None


def test_v2_net_block_accepts_legacy_single_token_form():
    """A legacy-style 1-token line (``R70-1``) must still produce a connection."""
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
R70
FOOTPRINT
R0402
PARTTYPE
Dummy-123
*
]
[
DESIGNATOR
R71
FOOTPRINT
R0402
PARTTYPE
Dummy-456
*
]
(
NET1
R70-1
R71-2
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    assert {c.id for c in ir.components} == {"R70", "R71"}
    assert len([n for n in ir.nets if n.name == "NET1"]) == 1
    assert all(conn.provenance == Provenance.NETLIST for conn in ir.connections)
    assert len(ir.connections) == 2
    for comp in ir.components:
        for p in comp.pins:
            # Legacy 1-token lines have no name/type info — both stay None.
            assert p.name is None
            assert p.type is None


def test_v2_net_block_unknown_elec_type_becomes_none():
    content = """PROTEL NETLIST 2.0
[
DESIGNATOR
R1
FOOTPRINT
R0402
PARTTYPE
Dummy
*
]
(
NET1
R1-1 R0402-1 MagicTag
)
"""
    ir = ProtelNetParser().parse(ParserInput(content=content))
    r1 = next(c for c in ir.components if c.id == "R1")
    assert r1.pins[0].type is None


# ---------- end-to-end fixture ----------


def test_v2_handwarmer_fixture_parses_all_components_nets_pins():
    fixture = FIXTURE_DIR / "Netlist_handWarmer_sch_2026-09-26.net"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(
        ParserInput(source_id=str(fixture), file_path=str(fixture))
    )

    assert ir.meta.format == "protel-net-v2"
    assert len(ir.components) > 0
    assert len(ir.nets) > 0
    assert len(ir.connections) > 0

    # Spot-check focal components for the user's primary query path.
    r5 = next((c for c in ir.components if c.id == "R5"), None)
    assert r5 is not None
    assert r5.device_kind == DeviceKind.RESISTOR
    assert r5.footprint == "R0402"
    assert r5.raw_value == "150kΩ"
    assert [p.number for p in r5.pins] == ["1", "2"]

    u1 = next((c for c in ir.components if c.id == "U1"), None)
    assert u1 is not None
    assert u1.device_kind == DeviceKind.IC
    assert u1.part_number == "TP4056"
    # TP4056 is ESOP-8 with an exposed-pad pin 9 → expect pins 1..9.
    assert [p.number for p in u1.pins] == [
        str(i) for i in range(1, 10)
    ]

    # LEDs must be classified as DIODE (not INDUCTOR).
    led3 = next((c for c in ir.components if c.id == "LED3"), None)
    assert led3 is not None
    assert led3.device_kind == DeviceKind.DIODE

    # Every connection points to a real component/pin.
    component_ids = {c.id for c in ir.components}
    for conn in ir.connections:
        comp_id, pin_num = conn.pin_id.rsplit(".", 1)
        assert comp_id in component_ids, f"orphan conn {conn.pin_id}"
        comp = next(c for c in ir.components if c.id == comp_id)
        assert pin_num in {p.number for p in comp.pins}


def test_v2_handwarmer_r5_det_bus_pin2_is_input_type():
    """R5 is on the DET_BUS net; its pin 2 must be type 'input' (from v2 elec token)."""
    fixture = FIXTURE_DIR / "Netlist_handWarmer_sch_2026-09-26.net"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(
        ParserInput(source_id=str(fixture), file_path=str(fixture))
    )

    det_bus = next((n for n in ir.nets if n.name == "DET_BUS"), None)
    assert det_bus is not None, "DET_BUS net must exist"

    r5 = next(c for c in ir.components if c.id == "R5")
    pin2 = next(p for p in r5.pins if p.number == "2")
    assert pin2.type == "input"


def test_v2_handwarmer_led_pin_names_anode_cathode():
    """LED3 pins must carry semantic names A (anode) and K (cathode) from the v2 netlist."""
    fixture = FIXTURE_DIR / "Netlist_handWarmer_sch_2026-09-26.net"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(
        ParserInput(source_id=str(fixture), file_path=str(fixture))
    )

    led3 = next(c for c in ir.components if c.id == "LED3")
    pins_by_num = {p.number: p for p in led3.pins}
    assert pins_by_num["1"].name == "A"
    assert pins_by_num["2"].name == "K"


def test_v2_legacy_must_still_parse_legacy_fixture():
    """The classic T268 fixture must keep parsing under the legacy code path."""
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = ProtelNetParser().parse(
        ParserInput(source_id=str(fixture), file_path=str(fixture))
    )
    assert ir.meta.format == "protel-net"
    assert len(ir.components) > 0
    # U1 (HT7533) is a well-known IC in this fixture.
    u1 = next((c for c in ir.components if c.id == "U1"), None)
    assert u1 is not None
    assert u1.device_kind == DeviceKind.IC
    assert u1.part_number == "HT7533 -23"
