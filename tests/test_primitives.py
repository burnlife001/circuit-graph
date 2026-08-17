"""Tests for the topology primitive library loader and bipartite encoding."""

from __future__ import annotations

import pytest

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import (
    PrimitiveValidationError,
    circuit_to_bipartite_graph,
    load_primitives,
    primitive_to_bipartite_graph,
)
from tests.fir_helpers import build_ir, make_component, make_net

TYPE_C_TEMPLATE_ID = "type-c-cc-pull-down"


def test_load_primitives_finds_type_c_template() -> None:
    """The default primitive library contains the Type-C CC pull-down template."""
    templates = load_primitives()
    ids = [t.id for t in templates]
    assert TYPE_C_TEMPLATE_ID in ids

    template = next(t for t in templates if t.id == TYPE_C_TEMPLATE_ID)
    assert template.name == "Type-C CC Pull-down"
    assert template.role == "pd_controller"
    assert len(template.signals) == 2
    assert len(template.components) == 1
    assert template.components[0].type == "Resistor"


def test_parse_primitive_requires_mandatory_keys() -> None:
    """A primitive missing required keys is rejected with a clear error."""
    from circuit_graph.primitives import _parse_primitive

    with pytest.raises(PrimitiveValidationError):
        _parse_primitive({"id": "incomplete"})


def test_parse_primitive_rejects_undefined_signal_reference() -> None:
    """A component pin must reference a signal declared by the primitive."""
    from circuit_graph.primitives import _parse_primitive

    raw = {
        "id": "bad",
        "name": "Bad",
        "signals": [{"id": "cc", "type": "net", "name_pattern": "CC"}],
        "components": [
            {
                "id": "R",
                "type": "Resistor",
                "pins": [{"number": 1, "net": "gnd"}],
            }
        ],
    }
    with pytest.raises(PrimitiveValidationError):
        _parse_primitive(raw)


def test_circuit_to_bipartite_graph() -> None:
    """CircuitIR is encoded as component and net nodes with connection edges."""
    r = make_component("R1", DeviceKind.RESISTOR, [("1", "CC1"), ("2", "GND")], raw_value="5.1K")
    cc = make_net("N_CC1", "CC1", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ir = build_ir([r], [cc, gnd], [("R1.1", "N_CC1"), ("R1.2", "N_GND")])

    g = circuit_to_bipartite_graph(ir)
    assert "c:R1" in g
    assert "n:N_CC1" in g
    assert "n:N_GND" in g
    assert g.nodes["c:R1"]["kind"] == "component"
    assert g.nodes["n:N_CC1"]["kind"] == "net"
    assert g.has_edge("c:R1", "n:N_CC1")
    assert g.has_edge("c:R1", "n:N_GND")


def test_primitive_to_bipartite_graph() -> None:
    """A primitive template is encoded using the same node conventions."""
    template = next(t for t in load_primitives() if t.id == TYPE_C_TEMPLATE_ID)
    g = primitive_to_bipartite_graph(template)

    assert "c:R_pull" in g
    assert "n:cc" in g
    assert "n:gnd" in g
    assert g.nodes["c:R_pull"]["kind"] == "component"
    assert g.nodes["n:cc"]["kind"] == "net"
    assert g.has_edge("c:R_pull", "n:cc")
    assert g.has_edge("c:R_pull", "n:gnd")


def test_load_primitives_skips_schema_file() -> None:
    """The JSON schema file must not be loaded as a primitive template."""
    templates = load_primitives()
    assert not any(t.id == "schema" for t in templates)


def test_load_primitives_missing_directory_raises() -> None:
    """Loading from a non-existent directory raises FileNotFoundError."""
    from pathlib import Path

    with pytest.raises(FileNotFoundError):
        load_primitives(Path("/nonexistent/primitives"))


# ---------------------------------------------------------------------------
# extract_volts
# ---------------------------------------------------------------------------


def test_extract_volts_parses_plain_voltage_labels() -> None:
    """extract_volts handles '5V' / '3.3V' / '+5V' / '5' → Decimal."""
    from decimal import Decimal
    from circuit_graph.primitives import extract_volts

    assert extract_volts("5V") == Decimal("5")
    assert extract_volts("3.3V") == Decimal("3.3")
    assert extract_volts("+5V") == Decimal("5")
    assert extract_volts("5") == Decimal("5")
    assert extract_volts("5.0V") == Decimal("5.0")


def test_extract_volts_parses_digit_v_digit_shorthand() -> None:
    """extract_volts handles edge_semantic's 'V-as-decimal-point' shorthand."""
    from decimal import Decimal
    from circuit_graph.primitives import extract_volts

    # _extract_level in edge_semantic.py emits "3V3" for 3.3V, "1V8" for 1.8V, "1V25" for 1.25V
    assert extract_volts("3V3") == Decimal("3.3")
    assert extract_volts("1V8") == Decimal("1.8")
    assert extract_volts("1V25") == Decimal("1.25")


def test_extract_volts_rejects_garbage() -> None:
    """extract_volts returns None for unparseable input."""
    from circuit_graph.primitives import extract_volts

    assert extract_volts(None) is None
    assert extract_volts("") is None
    assert extract_volts("VCC") is None
    assert extract_volts("3.3.3V") is None


# ---------------------------------------------------------------------------
# signal_semantic_matches
# ---------------------------------------------------------------------------


def test_signal_semantic_matches_all_none_is_unconstrained() -> None:
    """When all expected_* are None, any edge_semantic passes."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches(None, None, None, {"role": "power", "direction": "source", "level": "5V"})
    assert signal_semantic_matches(None, None, None, {"role": "digital", "direction": "load", "level": None})


def test_signal_semantic_matches_role_only() -> None:
    """expected_role alone matches against edge_semantic.role."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches("power", None, None, {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches("power", None, None, {"role": "digital", "direction": "source", "level": "5V"})


def test_signal_semantic_matches_direction_only() -> None:
    """expected_direction alone matches against edge_semantic.direction."""
    from circuit_graph.primitives import signal_semantic_matches

    assert signal_semantic_matches(None, "source", None, {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches(None, "source", None, {"role": "power", "direction": "load", "level": "5V"})


def test_signal_semantic_matches_level_compares_decimally() -> None:
    """expected_level is compared as Decimal volts, not raw string."""
    from circuit_graph.primitives import signal_semantic_matches

    # "3.3V" in the primitive should match "3V3" from edge_semantic
    assert signal_semantic_matches(None, None, "3.3V", {"role": "power", "direction": "source", "level": "3V3"})
    assert signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": "5V"})
    assert not signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": "3V3"})


def test_signal_semantic_matches_none_field_skips_check() -> None:
    """None expected_* fields are skipped, others still enforced."""
    from circuit_graph.primitives import signal_semantic_matches

    # role matches, direction is None (skip), level matches
    assert signal_semantic_matches("power", None, "5V", {"role": "power", "direction": "load", "level": "5V"})
    # role matches, direction is None (skip), level mismatches
    assert not signal_semantic_matches("power", None, "5V", {"role": "power", "direction": "load", "level": "3V3"})


def test_signal_semantic_matches_unparseable_level_is_mismatch() -> None:
    """If either side's level fails to parse, treat as mismatch (strict)."""
    from circuit_graph.primitives import signal_semantic_matches

    # Primitive side unparseable → mismatch
    assert not signal_semantic_matches(None, None, "garbage", {"role": "power", "direction": "source", "level": "5V"})
    # Circuit side unparseable (None) → mismatch
    assert not signal_semantic_matches(None, None, "5V", {"role": "power", "direction": "source", "level": None})


# ---------------------------------------------------------------------------
# PrimitiveSignal expected_* parsing
# ---------------------------------------------------------------------------


def test_parse_signal_reads_expected_fields() -> None:
    """_parse_signal picks up expected_role/direction/level when present."""
    from circuit_graph.primitives import _parse_signal

    raw = {
        "id": "vcc",
        "type": "net",
        "name_pattern": "^VCC",
        "expected_role": "power",
        "expected_direction": "source",
        "expected_level": "5V",
    }
    sig = _parse_signal(raw)
    assert sig.expected_role == "power"
    assert sig.expected_direction == "source"
    assert sig.expected_level == "5V"


def test_parse_signal_defaults_expected_fields_to_none() -> None:
    """Omitted expected_* fields default to None (backwards compatible)."""
    from circuit_graph.primitives import _parse_signal

    sig = _parse_signal({"id": "vcc", "type": "net", "name_pattern": "^VCC"})
    assert sig.expected_role is None
    assert sig.expected_direction is None
    assert sig.expected_level is None


def test_parse_signal_rejects_invalid_role() -> None:
    """expected_role outside the canonical set is rejected."""
    from circuit_graph.primitives import _parse_signal

    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_role": "invalid_role",
        })


def test_parse_signal_rejects_invalid_direction() -> None:
    """expected_direction outside the canonical set is rejected."""
    from circuit_graph.primitives import _parse_signal

    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_direction": "invalid_direction",
        })
