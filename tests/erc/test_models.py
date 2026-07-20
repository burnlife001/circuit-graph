"""Tests for circuit_graph.erc.models."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from circuit_graph.erc.models import (
    ElectricalPinType,
    ErcReport,
    ErcViolation,
    PinContext,
)


def test_electrical_pin_type_has_twelve_members():
    """KiCad common/pin_type.h defines 12 pin types."""
    members = list(ElectricalPinType)
    assert len(members) == 12
    # Spot-check the canonical names.
    assert ElectricalPinType.INPUT.value == "input"
    assert ElectricalPinType.OUTPUT.value == "output"
    assert ElectricalPinType.POWER_IN.value == "power_in"
    assert ElectricalPinType.POWER_OUT.value == "power_out"
    assert ElectricalPinType.NO_CONNECT.value == "no_connect"
    assert ElectricalPinType.OPEN_COLLECTOR.value == "open_collector"
    assert ElectricalPinType.OPEN_EMITTER.value == "open_emitter"
    assert ElectricalPinType.PASSIVE.value == "passive"
    assert ElectricalPinType.BIDIRECTIONAL.value == "bidirectional"
    assert ElectricalPinType.TRISTATE.value == "tristate"
    assert ElectricalPinType.FREE.value == "free"
    assert ElectricalPinType.UNSPECIFIED.value == "unspecified"


def test_str_enum_subclass_stringifies_to_value():
    """ElectricalPinType is a StrEnum; str(x) should return the underlying value."""
    assert str(ElectricalPinType.INPUT) == "input"
    assert str(ElectricalPinType.NO_CONNECT) == "no_connect"
    assert ElectricalPinType.OUTPUT == "output"


def _make_violation(severity: str, code: str = "test.code", msg: str = "t") -> ErcViolation:
    return ErcViolation(
        severity=severity,  # type: ignore[arg-type]
        type="test",
        code=code,
        message=msg,
        net_name="Net1",
        component_refs=["R1", "R2"],
        pin_numbers=["1", "2"],
        position=(1.0, 2.0),
    )


def test_erc_violation_to_dict_shape():
    """ErcViolation.to_dict() returns a plain dict mirroring its fields."""
    v = _make_violation("error", code="pin_conflict", msg="Output shorted")
    d = v.to_dict()
    assert isinstance(d, dict)
    assert d["severity"] == "error"
    assert d["type"] == "test"
    assert d["code"] == "pin_conflict"
    assert d["message"] == "Output shorted"
    assert d["net_name"] == "Net1"
    assert d["component_refs"] == ["R1", "R2"]
    assert d["pin_numbers"] == ["1", "2"]
    assert d["position"] == (1.0, 2.0)
    assert d["sheet_path"] == "/"
    assert d["suggested_fix"] is None


def test_erc_report_counts_errors_and_warnings():
    """error_count / warning_count reflect only matching severities."""
    report = ErcReport(
        source_file="test.kicad_pcb",
        engine="native",
        violations=[
            _make_violation("error"),
            _make_violation("error", code="c2"),
            _make_violation("warning"),
            _make_violation("info"),
        ],
    )
    assert report.error_count == 2
    assert report.warning_count == 1


def test_erc_report_to_dict_shape():
    """ErcReport.to_dict() exposes source_file, engine, counts, violations."""
    report = ErcReport(
        source_file="board.kicad_pcb",
        engine="kicad-cli",
        kicad_version="9.0",
        violations=[_make_violation("error")],
        duration_ms=12.5,
        warnings=["deprecated symbol"],
    )
    d = report.to_dict()
    assert d["source_file"] == "board.kicad_pcb"
    assert d["engine"] == "kicad-cli"
    assert d["kicad_version"] == "9.0"
    assert d["error_count"] == 1
    assert d["warning_count"] == 0
    assert d["duration_ms"] == 12.5
    assert d["warnings"] == ["deprecated symbol"]
    assert isinstance(d["violations"], list)
    assert d["violations"][0]["severity"] == "error"


def test_pin_context_is_frozen():
    """PinContext must be immutable; mutation raises FrozenInstanceError."""
    ctx = PinContext(
        pin_number="1",
        pin_name="VCC",
        electrical_type=ElectricalPinType.POWER_IN,
        component_ref="U1",
        component_lib_id="Library:Part",
        position=(0.0, 0.0),
    )
    with pytest.raises(FrozenInstanceError):
        ctx.pin_number = "2"  # type: ignore[misc]
