"""Tests for circuit_graph.erc.engine."""
from __future__ import annotations

from circuit_graph.erc.engine import (
    ComponentValidator,
    ConnectivityValidator,
    ElectricalRulesChecker,
    PinTypeValidator,
    PowerValidator,
)
from circuit_graph.erc.extractor import ComponentContext, ExtractedSchematic
from circuit_graph.erc.models import ElectricalPinType, PinContext

# ----------------------------------------------------------------- helpers


def make_pin(
    ref: str,
    num: str,
    etype: ElectricalPinType,
    x: float = 0.0,
    y: float = 0.0,
) -> PinContext:
    return PinContext(
        pin_number=num,
        pin_name=None,
        electrical_type=etype,
        component_ref=ref,
        component_lib_id=f"lib:{ref[0]}",
        position=(x, y),
    )


def make_comp(
    ref: str,
    pins: list[PinContext],
    value: str = "1k",
    lib_id: str | None = None,
) -> ComponentContext:
    return ComponentContext(
        reference=ref,
        value=value,
        lib_id=lib_id or f"lib:{ref[0]}",
        position=(0.0, 0.0),
        rotation=0,
        pins=pins,
    )


# ------------------------------------------------------------- PinTypeValidator


def test_pin_type_validator_flags_output_conflict():
    """Two OUTPUT pins on the same net → ERROR."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.OUTPUT),
        make_pin("U2", "1", ElectricalPinType.OUTPUT),
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[],
        nets={"NET1": pins},
    )
    v = PinTypeValidator()
    violations = v.validate(sch)
    assert any(vr.type == "pin_conflict" and vr.severity == "error" for vr in violations)


def test_pin_type_validator_passes_passive_to_output():
    """PASSIVE to OUTPUT is fine."""
    pins = [
        make_pin("R1", "1", ElectricalPinType.PASSIVE),
        make_pin("U1", "1", ElectricalPinType.OUTPUT),
    ]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": pins})
    v = PinTypeValidator()
    assert v.validate(sch) == []


def test_pin_type_validator_handles_stacked_pins():
    """Stacked pins (same ref, num, pos) are not flagged."""
    p1 = make_pin("R1", "1", ElectricalPinType.OUTPUT, 0.0, 0.0)
    p2 = make_pin("R1", "1", ElectricalPinType.OUTPUT, 0.0, 0.0)
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": [p1, p2]})
    v = PinTypeValidator()
    assert v.validate(sch) == []


# ---------------------------------------------------------- ConnectivityValidator


def test_connectivity_validator_flags_single_pin_net():
    """A net with exactly one pin is suspicious (warning)."""
    pins = [make_pin("R1", "1", ElectricalPinType.PASSIVE)]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"N": pins})
    v = ConnectivityValidator()
    violations = v.validate(sch)
    assert any(vr.type == "single_pin_net" for vr in violations)


# ----------------------------------------------------------- ComponentValidator


def test_component_validator_flags_duplicate_references():
    """Two components with the same reference → ERROR."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="1k")
    c2 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="2k")
    sch = ExtractedSchematic(source_file="x", components=[c1, c2], nets={})
    v = ComponentValidator()
    violations = v.validate(sch)
    assert any(vr.type == "duplicate_reference" and vr.severity == "error" for vr in violations)


def test_component_validator_warns_on_missing_value():
    """Component with empty value → WARNING."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="")
    sch = ExtractedSchematic(source_file="x", components=[c1], nets={})
    v = ComponentValidator()
    violations = v.validate(sch)
    assert any(vr.type == "missing_value" and vr.severity == "warning" for vr in violations)


# -------------------------------------------------------------- PowerValidator


def test_power_validator_flags_undriven_power_in():
    """A POWER_IN pin on a net with no POWER_OUT → ERROR."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_IN),
        make_pin("C1", "1", ElectricalPinType.PASSIVE),  # decoup cap, not a driver
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[],
        nets={"VCC": pins},
    )
    v = PowerValidator()
    violations = v.validate(sch)
    assert any(vr.type == "power_pin_not_driven" and vr.severity == "error" for vr in violations)


def test_power_validator_passes_power_out_driven_net():
    """A POWER_OUT driver satisfies any POWER_IN on the same net (no E003).

    I001 (no PWR_FLAG) may still fire as informational — that's a separate
    concern from driver coverage.
    """
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_OUT),
        make_pin("U2", "1", ElectricalPinType.POWER_IN),
    ]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"VCC": pins})
    v = PowerValidator()
    violations = v.validate(sch)
    assert not any(vr.type == "power_pin_not_driven" for vr in violations)


def test_power_validator_pwr_flag_satisfies_undriven_power_in():
    """A PWR_FLAG on the net suppresses the E003 'undriven power in' error.

    PWR_FLAG declares external power (connector, off-board regulator, etc.),
    so POWER_IN pins without an on-schematic POWER_OUT driver are still OK.
    The I001 info-level hint is also suppressed when PWR_FLAG is present.
    """
    pins = [
        make_pin("PWR1", "1", ElectricalPinType.PASSIVE),  # PWR_FLAG pin
        make_pin("U1", "1", ElectricalPinType.POWER_IN),
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[
            make_comp(
                "PWR1",
                [pins[0]],
                value="#FLG",
                lib_id="power:PWR_FLAG",
            ),
            make_comp("U1", [pins[1]], value="LM321", lib_id="lib:U"),
        ],
        nets={"VCC": pins},
    )
    v = PowerValidator()
    violations = v.validate(sch)
    assert violations == [], f"Expected no violations, got {violations}"


def test_power_validator_emits_i001_for_power_net_without_pwr_flag():
    """A power net (has POWER_IN pins) without PWR_FLAG → I001 info hint."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_OUT),
        make_pin("U2", "1", ElectricalPinType.POWER_IN),
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[
            make_comp("U1", [pins[0]], value="REG", lib_id="lib:U"),
            make_comp("U2", [pins[1]], value="LM321", lib_id="lib:U"),
        ],
        nets={"VCC": pins},
    )
    v = PowerValidator()
    violations = v.validate(sch)
    i001 = [vr for vr in violations if vr.code == "I001"]
    assert len(i001) == 1
    assert i001[0].severity == "info"
    assert i001[0].type == "no_power_flag"
    assert i001[0].net_name == "VCC"


def test_power_validator_no_i001_for_signal_net():
    """A net without POWER_IN pins must not get a PWR_FLAG hint."""
    pins = [
        make_pin("U1", "1", ElectricalPinType.OUTPUT),
        make_pin("U2", "1", ElectricalPinType.INPUT),
    ]
    sch = ExtractedSchematic(
        source_file="x",
        components=[
            make_comp("U1", [pins[0]], value="MCU", lib_id="lib:U"),
            make_comp("U2", [pins[1]], value="SENSOR", lib_id="lib:U"),
        ],
        nets={"SIG": pins},
    )
    v = PowerValidator()
    violations = v.validate(sch)
    assert not any(vr.code == "I001" for vr in violations)


# --------------------------------------------------- error code stability


def test_error_codes_match_documented_table():
    """Error codes must match the docstring table.

    Regression guard for the E002 / E003 / E004 conflict that was fixed in
    todolist_fix.md #1. Any drift here will break callers filtering by code.
    """
    # duplicate_reference → E002
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="1k")
    c2 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="2k")
    sch = ExtractedSchematic(
        source_file="x", components=[c1, c2], nets={}
    )
    dup_codes = {v.code for v in ComponentValidator().validate(sch)}
    assert "E002" in dup_codes
    assert "E003" not in dup_codes  # was the old, wrong code

    # power_pin_not_driven → E003
    pins = [
        make_pin("U1", "1", ElectricalPinType.POWER_IN),
        make_pin("C1", "1", ElectricalPinType.PASSIVE),
    ]
    sch = ExtractedSchematic(source_file="x", components=[], nets={"VCC": pins})
    pwr_codes = {v.code for v in PowerValidator().validate(sch)}
    assert "E003" in pwr_codes
    assert "E004" not in pwr_codes  # was the old, wrong code

    # unconnected_input → W003 (warning, not error)
    c = make_comp(
        "U1",
        [make_pin("U1", "1", ElectricalPinType.INPUT)],
        value="x",
    )
    sch = ExtractedSchematic(source_file="x", components=[c], nets={})
    conn = ConnectivityValidator().validate(sch)
    unconn = [v for v in conn if v.type == "unconnected_input"]
    assert unconn and unconn[0].code == "W003"
    assert unconn[0].severity == "warning"


# ------------------------------------------------------ ElectricalRulesChecker


def test_orchestrator_runs_all_validators_and_returns_report():
    """Engine wires up the 4 validators and returns an ErcReport."""
    c1 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="")
    c2 = make_comp("R1", [make_pin("R1", "1", ElectricalPinType.PASSIVE)], value="2k")  # duplicate
    sch = ExtractedSchematic(source_file="x", components=[c1, c2], nets={})
    report = ElectricalRulesChecker().run(sch)
    assert report.engine == "native"
    assert any(vr.type == "duplicate_reference" for vr in report.violations)
