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
