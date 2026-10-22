"""Tests for the topology primitive library loader and bipartite encoding."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import (
    PrimitiveValidationError,
    _parse_signal,
    circuit_to_bipartite_graph,
    extract_volts,
    load_primitives,
    primitive_to_bipartite_graph,
    signal_semantic_matches,
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
    """CircuitIR is encoded as component-pin and net nodes with connection edges."""
    r = make_component("R1", DeviceKind.RESISTOR, [("1", "CC1"), ("2", "GND")], raw_value="5.1K")
    cc = make_net("N_CC1", "CC1", NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    ir = build_ir([r], [cc, gnd], [("R1.1", "N_CC1"), ("R1.2", "N_GND")])

    g = circuit_to_bipartite_graph(ir)
    assert "c:R1:1" in g
    assert "c:R1:2" in g
    assert "n:N_CC1" in g
    assert "n:N_GND" in g
    assert g.nodes["c:R1:1"]["kind"] == "component"
    assert g.nodes["c:R1:1"]["pin"].number == "1"
    assert g.nodes["n:N_CC1"]["kind"] == "net"
    assert g.has_edge("c:R1:1", "n:N_CC1")
    assert g.has_edge("c:R1:2", "n:N_GND")


def test_primitive_to_bipartite_graph() -> None:
    """A primitive template is encoded using the same node conventions."""
    template = next(t for t in load_primitives() if t.id == TYPE_C_TEMPLATE_ID)
    g = primitive_to_bipartite_graph(template)

    assert "c:R_pull:1" in g
    assert "c:R_pull:2" in g
    assert "n:cc" in g
    assert "n:gnd" in g
    assert g.nodes["c:R_pull:1"]["kind"] == "component"
    assert g.nodes["c:R_pull:1"]["pin"].number == "1"
    assert g.nodes["n:cc"]["kind"] == "net"
    assert g.has_edge("c:R_pull:1", "n:cc")
    assert g.has_edge("c:R_pull:2", "n:gnd")


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
    assert extract_volts("5V") == Decimal("5")
    assert extract_volts("3.3V") == Decimal("3.3")
    assert extract_volts("+5V") == Decimal("5")
    assert extract_volts("5") == Decimal("5")
    assert extract_volts("5.0V") == Decimal("5.0")


def test_extract_volts_parses_digit_v_digit_shorthand() -> None:
    """extract_volts handles edge_semantic's 'V-as-decimal-point' shorthand."""
    # _extract_level in edge_semantic.py emits "3V3" for 3.3V, "1V8" for 1.8V, "1V25" for 1.25V
    assert extract_volts("3V3") == Decimal("3.3")
    assert extract_volts("1V8") == Decimal("1.8")
    assert extract_volts("1V25") == Decimal("1.25")


def test_extract_volts_rejects_garbage() -> None:
    """extract_volts returns None for unparseable input."""
    assert extract_volts(None) is None
    assert extract_volts("") is None
    assert extract_volts("VCC") is None
    assert extract_volts("3.3.3V") is None


# ---------------------------------------------------------------------------
# signal_semantic_matches
# ---------------------------------------------------------------------------


def test_signal_semantic_matches_all_none_is_unconstrained() -> None:
    """When all expected_* are None, any edge_semantic passes."""
    sem_power = {"role": "power", "direction": "source", "level": "5V"}
    sem_digital = {"role": "digital", "direction": "load", "level": None}
    assert signal_semantic_matches(None, None, None, sem_power)
    assert signal_semantic_matches(None, None, None, sem_digital)


def test_signal_semantic_matches_role_only() -> None:
    """expected_role alone matches against edge_semantic.role."""
    sem_match = {"role": "power", "direction": "source", "level": "5V"}
    sem_miss = {"role": "digital", "direction": "source", "level": "5V"}
    assert signal_semantic_matches("power", None, None, sem_match)
    assert not signal_semantic_matches("power", None, None, sem_miss)


def test_signal_semantic_matches_direction_only() -> None:
    """expected_direction alone matches against edge_semantic.direction."""
    sem_src = {"role": "power", "direction": "source", "level": "5V"}
    sem_load = {"role": "power", "direction": "load", "level": "5V"}
    assert signal_semantic_matches(None, "source", None, sem_src)
    assert not signal_semantic_matches(None, "source", None, sem_load)


def test_signal_semantic_matches_level_compares_decimally() -> None:
    """expected_level is compared as Decimal volts, not raw string."""
    # "3.3V" in the primitive should match "3V3" from edge_semantic
    sem_3v3 = {"role": "power", "direction": "source", "level": "3V3"}
    sem_5v = {"role": "power", "direction": "source", "level": "5V"}
    assert signal_semantic_matches(None, None, "3.3V", sem_3v3)
    assert signal_semantic_matches(None, None, "5V", sem_5v)
    assert not signal_semantic_matches(None, None, "5V", sem_3v3)


def test_signal_semantic_matches_none_field_skips_check() -> None:
    """None expected_* fields are skipped, others still enforced."""
    sem_5v = {"role": "power", "direction": "load", "level": "5V"}
    sem_3v3 = {"role": "power", "direction": "load", "level": "3V3"}
    # role matches, direction is None (skip), level matches
    assert signal_semantic_matches("power", None, "5V", sem_5v)
    # role matches, direction is None (skip), level mismatches
    assert not signal_semantic_matches("power", None, "5V", sem_3v3)


def test_signal_semantic_matches_unparseable_level_is_mismatch() -> None:
    """If either side's level fails to parse, treat as mismatch (strict)."""
    sem_5v = {"role": "power", "direction": "source", "level": "5V"}
    sem_none_level = {"role": "power", "direction": "source", "level": None}
    # Primitive side unparseable → mismatch
    assert not signal_semantic_matches(None, None, "garbage", sem_5v)
    # Circuit side unparseable (None) → mismatch
    assert not signal_semantic_matches(None, None, "5V", sem_none_level)


# ---------------------------------------------------------------------------
# PrimitiveSignal expected_* parsing
# ---------------------------------------------------------------------------


def test_parse_signal_reads_expected_fields() -> None:
    """_parse_signal picks up expected_role/direction/level when present."""
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
    sig = _parse_signal({"id": "vcc", "type": "net", "name_pattern": "^VCC"})
    assert sig.expected_role is None
    assert sig.expected_direction is None
    assert sig.expected_level is None


def test_parse_signal_rejects_invalid_role() -> None:
    """expected_role outside the canonical set is rejected."""
    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_role": "invalid_role",
        })


def test_parse_signal_rejects_invalid_direction() -> None:
    """expected_direction outside the canonical set is rejected."""
    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "vcc", "type": "net", "name_pattern": "^VCC",
            "expected_direction": "invalid_direction",
        })


def test_parse_signal_accepts_role_list() -> None:
    """expected_role may be a list of roles; normalised to a tuple."""
    sig = _parse_signal({
        "id": "pwr", "type": "net", "name_pattern": ".*",
        "expected_role": ["power", "gnd"],
    })
    assert sig.expected_role == ("power", "gnd")


def test_parse_signal_rejects_invalid_role_in_list() -> None:
    """A list containing an unknown role is rejected."""
    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "pwr", "type": "net", "name_pattern": ".*",
            "expected_role": ["power", "bogus"],
        })


def test_parse_signal_rejects_empty_role_list() -> None:
    """An empty role list is rejected."""
    with pytest.raises(PrimitiveValidationError):
        _parse_signal({
            "id": "pwr", "type": "net", "name_pattern": ".*",
            "expected_role": [],
        })


def test_signal_semantic_matches_role_set() -> None:
    """Tuple role matches when actual role is a member of the set."""
    sem_gnd = {"role": "gnd", "direction": "bidirectional", "level": None}
    sem_power = {"role": "power", "direction": "source", "level": "5V"}
    sem_digital = {"role": "digital", "direction": "load", "level": None}
    assert signal_semantic_matches(("power", "gnd"), None, None, sem_gnd)
    assert signal_semantic_matches(("power", "gnd"), None, None, sem_power)
    assert not signal_semantic_matches(("power", "gnd"), None, None, sem_digital)


# ---------------------------------------------------------------------------
# PrimitiveTemplate._signal_to_components derived index
# ---------------------------------------------------------------------------


def test_template_signal_to_components_derived() -> None:
    """PrimitiveTemplate derives signal_id → [component_id, ...] from pins."""
    template = next(t for t in load_primitives() if t.id == "voltage-divider")
    # voltage-divider 中：vin 连到 R_top, vout 连到 R_top+R_bottom, gnd 连到 R_bottom
    assert template._signal_to_components["vin"] == ["R_top"]
    assert template._signal_to_components["vout"] == ["R_bottom", "R_top"]
    assert template._signal_to_components["gnd"] == ["R_bottom"]


def test_template_signal_to_components_deterministic() -> None:
    """Derived index is sorted so repeated loads give identical lists."""
    t1 = next(t for t in load_primitives() if t.id == "opamp-unit-voltage-follower")
    t2 = next(t for t in load_primitives() if t.id == "opamp-unit-voltage-follower")
    assert t1._signal_to_components == t2._signal_to_components


def test_template_signal_to_components_empty_for_unconnected_signal() -> None:
    """A signal no component pin references gets an empty list."""
    from circuit_graph.primitives import _parse_primitive

    raw = {
        "id": "orphan-signal",
        "name": "Orphan",
        "signals": [
            {"id": "unused", "type": "net", "name_pattern": "^UNUSED$"},
            {"id": "used", "type": "net", "name_pattern": "^USED$"},
        ],
        "components": [
            {
                "id": "R1", "type": "Resistor",
                "pins": [{"number": "1", "net": "used"}, {"number": "2", "net": "used"}],
            }
        ],
    }
    template = _parse_primitive(raw)
    assert template._signal_to_components["unused"] == []
    assert template._signal_to_components["used"] == ["R1"]


def test_primitive_index_is_up_to_date() -> None:
    """CI guard: regenerate the index from JSON; the committed file must match.

    Pre-commit hook regenerates it locally; this test catches the case where
    the hook is missing or skipped. ``--check`` exits 1 on drift.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/build_primitive_index.py", "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "primitives/__index_primitives.md is out of date.\n"
        f"stderr: {result.stderr}\n"
        "Fix: uv run python scripts/build_primitive_index.py"
    )


def test_primitive_index_lists_all_primitives() -> None:
    """The index file mentions every primitive that load_primitives() returns."""
    from pathlib import Path

    ids = {t.id for t in load_primitives()}
    content = Path("primitives/__index_primitives.md").read_text(encoding="utf-8")
    missing = sorted(pid for pid in ids if f"`{pid}`" not in content)
    assert not missing, f"index does not mention primitives: {missing}"


def test_signal_name_matches_falls_back_to_default_for_protel_nets():
    """signal_name_matches must OR in _DEFAULT_SIGNAL_PATTERNS[sigid]
    when the user pattern misses, so all 21 templates match Protel nets.
    """
    from circuit_graph.primitives import signal_name_matches

    # User pattern miss + id-keyed default hit ⇒ True
    assert signal_name_matches(r"^OUT$", "NetR100_1", signal_id="out") is True
    assert signal_name_matches(r"^VIN\+$", "NetC82_1", signal_id="in_p") is True
    assert signal_name_matches(r"^VIN\-$", "NetR97_1", signal_id="in_n") is True
    # Protel-style power rail
    assert signal_name_matches(r"^VCC$", "USB_5V", signal_id="vcc") is True

    # Two-arg form MUST remain strict (no fallback)
    assert signal_name_matches(r"^OUT$", "NetR100_1") is False
    assert signal_name_matches(r"^VIN\-$", "NetR97_1") is False

    # User hit short-circuits before fallback
    assert signal_name_matches(r"^GND$", "GND") is True
    assert signal_name_matches(r"^GND$", "GND", signal_id="gnd") is True

    # Unknown signal_id with no defaults ⇒ False
    assert signal_name_matches(r"^X$", "anything", signal_id="__no_such_id__") is False


def test_signal_name_matches_does_not_treat_cgnd_as_gnd_fallback():
    """CGND (chassis / signal ground) must NOT match signal_id='gnd'.

    Regression for the false positive where R96 — a GND↔CGND bridge
    resistor on the T268 board — was matched as the R_shunt of
    rc-high-pass.json, generating a spurious `rc-high-pass-c82`
    FunctionalBlock with [C82, R96]. The CGND end of R96 is *not* the
    circuit-reference ground; allowing CGND as a 'gnd' fallback gave
    the high-pass pattern a structural leg to stand on.

    After the fix, only true GND/PGND/AGND/DGND/VSS/VEE/... names match
    the 'gnd' signal. A primitive that genuinely needs CGND-as-ground
    should declare it under a dedicated signal id (e.g. `cgnd`).
    """
    from circuit_graph.primitives import signal_name_matches

    # CGND must NOT match signal_id='gnd' fallback
    assert signal_name_matches(r"^X$", "CGND", signal_id="gnd") is False
    # But an explicit CGND user pattern still works (caller's choice)
    assert signal_name_matches(r"^CGND$", "CGND", signal_id="gnd") is True
    # Sanity: GND itself still matches the fallback
    assert signal_name_matches(r"^X$", "GND", signal_id="gnd") is True


# ---------------------------------------------------------------------------
# Component type as a list (multi-type acceptance)
# ---------------------------------------------------------------------------


def test_parse_component_accepts_type_list() -> None:
    """Component ``type`` may be a list; normalised to a tuple."""
    from circuit_graph.primitives import _parse_component, _parse_signal

    sig = _parse_signal({"id": "s", "type": "net", "name_pattern": ".*"})
    comp = _parse_component(
        {
            "id": "BD",
            "type": ["Diode", "BridgeRectifier"],
            "pins": [{"number": "1", "net": "s"}],
        },
        [sig],
    )
    assert comp.type == ("Diode", "BridgeRectifier")


def test_parse_component_rejects_empty_type_list() -> None:
    from circuit_graph.primitives import _parse_component, _parse_signal

    sig = _parse_signal({"id": "s", "type": "net", "name_pattern": ".*"})
    with pytest.raises(PrimitiveValidationError):
        _parse_component(
            {"id": "BD", "type": [], "pins": [{"number": "1", "net": "s"}]},
            [sig],
        )


def test_component_type_matches_tuple() -> None:
    """component_type_matches accepts any member of a type tuple."""
    from circuit_graph.primitives import component_type_matches

    diode = make_component("D1", DeviceKind.DIODE, [], raw_value="US1M")
    bridge = make_component("BD1", DeviceKind.BRIDGE_RECTIFIER, [], raw_value="10A")
    ic = make_component("U1", DeviceKind.IC, [], raw_value="LM358")

    accepted = ("Diode", "BridgeRectifier")
    assert component_type_matches(accepted, diode)
    assert component_type_matches(accepted, bridge)
    assert not component_type_matches(accepted, ic)
    # Single-string form still works
    assert component_type_matches("Diode", diode)
    assert not component_type_matches("Diode", ic)


def test_protel_parser_classifies_bridge_footprint() -> None:
    """footprint containing 'BRIDGE' yields BRIDGE_RECTIFIER regardless of ref."""
    from circuit_graph.parsers.protel import ProtelNetParser

    kind = ProtelNetParser._infer_device_kind("BD1", "Bridge", "10A/1000V")
    assert kind is DeviceKind.BRIDGE_RECTIFIER
    # Prefix heuristics unaffected
    assert ProtelNetParser._infer_device_kind("D1", "SMA", "US1M") is DeviceKind.DIODE
    assert ProtelNetParser._infer_device_kind("B1", "SIP-4", None) is DeviceKind.UNKNOWN


# --- design output extension (Task 1) ---


def _write_primitive(tmp_path, name: str, raw: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def test_load_primitive_with_design_fields(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "div.json",
        {
            "id": "div",
            "name": "分压",
            "signals": [
                {"id": "vin", "type": "net", "name_pattern": "VIN"},
                {"id": "vout", "type": "net", "name_pattern": "VOUT"},
                {"id": "gnd", "type": "net", "name_pattern": "GND"},
            ],
            "components": [
                {
                    "id": "R_top",
                    "type": "Resistor",
                    "pins": [
                        {"number": "1", "net": "vin"},
                        {"number": "2", "net": "vout"},
                    ],
                },
                {
                    "id": "R_bottom",
                    "type": "Resistor",
                    "pins": [
                        {"number": "1", "net": "vout"},
                        {"number": "2", "net": "gnd"},
                    ],
                },
            ],
            "design_inputs": [
                {"id": "vin", "label": "输入电压", "unit": "V"},
                {"id": "vout", "label": "输出电压", "unit": "V"},
                {"id": "r_bottom", "label": "下电阻", "unit": "Ω", "default": 10000},
            ],
            "parameters": {
                "R_top.value": "(vin - vout) * r_bottom / vout",
                "R_bottom.value": "r_bottom",
            },
            "recommended_parts": {
                "R_top": [{"pn": "0603 1%", "footprint": "0603"}],
                "R_bottom": [{"pn": "0603 1%", "footprint": "0603"}],
            },
        },
    )
    templates = load_primitives(tmp_path)
    assert len(templates) == 1
    t = templates[0]
    assert [d.id for d in t.design_inputs] == ["vin", "vout", "r_bottom"]
    assert t.design_inputs[2].default == 10000
    assert t.parameters == {
        "R_top.value": "(vin - vout) * r_bottom / vout",
        "R_bottom.value": "r_bottom",
    }
    assert t.recommended_parts["R_top"][0].pn == "0603 1%"
    assert t.recommended_parts["R_top"][0].footprint == "0603"


def test_primitive_without_design_fields_still_loads(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "old.json",
        {
            "id": "old",
            "name": "老模板",
            "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
            "components": [
                {
                    "id": "R1",
                    "type": "Resistor",
                    "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                }
            ],
        },
    )
    templates = load_primitives(tmp_path)
    assert len(templates) == 1
    t = templates[0]
    assert t.design_inputs == []
    assert t.parameters == {}
    assert t.recommended_parts == {}


def test_parameter_key_must_reference_known_component(tmp_path: Path) -> None:
    _write_primitive(
        tmp_path,
        "bad.json",
        {
            "id": "bad",
            "name": "坏模板",
            "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
            "components": [
                {
                    "id": "R1",
                    "type": "Resistor",
                    "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                }
            ],
            "parameters": {"R_missing.value": "1000"},
        },
    )
    with pytest.raises(PrimitiveValidationError, match="R_missing"):
        load_primitives(tmp_path)


# ---------------------------------------------------------------------------
# JSON-schema validation against primitives/schema.json (todolist P1-6)


def test_all_primitives_validate_against_schema() -> None:
    """Every primitive JSON must satisfy primitives/schema.json."""
    jsonschema = pytest.importorskip("jsonschema")

    primitives_dir = Path(__file__).resolve().parent.parent / "primitives"
    schema = json.loads((primitives_dir / "schema.json").read_text(encoding="utf-8"))
    files = [
        p
        for p in sorted(primitives_dir.rglob("*.json"))
        if p.name not in ("schema.json",)
    ]
    assert files, "no primitive JSON files found"
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(doc, schema)
