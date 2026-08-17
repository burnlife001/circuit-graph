"""Test SPICE netlist → CircuitIR conversion."""

import sys
from pathlib import Path

import pytest

# convert_to_kicad.py (lm321_ccs/, gitignored) imports kicad_sch_api at module
# level; skip the whole module when the optional dependency is absent instead
# of failing at import time (same convention as test_lm321_ccs.py).
pytest.importorskip("kicad_sch_api")


def test_ir_from_spice_parses_ccs_netlist():
    """Verify the SPICE parser extracts components and nets correctly."""
    spice_path = (
        Path(__file__).parent.parent.parent / "lm321_ccs" / "ccs_v5_bjt.cir"
    )
    if not spice_path.exists():
        pytest.skip(f"SPICE file not found: {spice_path}")

    # Import the converter function from convert_to_kicad.
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lm321_ccs"))
    from convert_to_kicad import _ir_from_spice

    ir = _ir_from_spice(spice_path)

    comp_ids = {c.id for c in ir.components}
    # Real components (not measurement sources).
    assert "RS" in comp_ids
    assert "RLOAD" in comp_ids
    assert "Q1" in comp_ids
    assert "RB" in comp_ids
    assert "CF" in comp_ids
    assert "XU1" in comp_ids  # LM321 subcircuit
    # Skip measurement voltage sources.
    assert "V_AMS" not in comp_ids
    assert "V_AML" not in comp_ids

    # Q1 uses a 4-node SPICE BJT model (C B E substrate) but the KiCad BC547
    # symbol only has C/B/E pins, so the substrate node must be dropped.
    q1 = next(c for c in ir.components if c.id == "Q1")
    assert len(q1.pins) == 3
    assert [p.number for p in q1.pins] == ["1", "2", "3"]

    net_names = {n.name for n in ir.nets}
    assert "GND" in net_names
    assert "N_REF" in net_names
    assert "N_SENSE" in net_names
    assert "N_GATE_OUT" in net_names

    # Each component should have connections.
    assert len(ir.connections) > 0
    for conn in ir.connections:
        assert conn.pin_id
        assert conn.net_id


def test_ir_from_spice_produces_runnable_layout():
    """The parsed IR should be accepted by layout_unit without errors."""
    spice_path = (
        Path(__file__).parent.parent.parent / "lm321_ccs" / "ccs_v5_bjt.cir"
    )
    if not spice_path.exists():
        pytest.skip(f"SPICE file not found: {spice_path}")

    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lm321_ccs"))
    from convert_to_kicad import _ir_from_spice

    ir = _ir_from_spice(spice_path)

    from circuit_graph.layout.config import LayoutConfig
    from circuit_graph.layout.unit_layout import layout_unit

    cfg = LayoutConfig.from_dict(
        {
            "io": {
                "inputs": ["N_REF", "N_SENSE"],
                "outputs": ["N_GATE_OUT"],
                "power_top": ["N_VCC"],
                "power_bottom": ["GND"],
            },
            "symbols": {
                # _ir_from_spice writes metadata["symbol"] as the KiCad lib id,
                # so config keys must match those lib ids.
                "Device:R": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}},
                "Device:C": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}},
                "Transistor_BJT:BC547": {
                    "rotation": 0,
                    "pin_directions": {"1": "up", "2": "left", "3": "down"},
                },
                "Amplifier_Operational:LM321": {
                    "rotation": 0,
                    "pin_directions": {
                        "1": "left",
                        "2": "down",
                        "3": "left",
                        "4": "right",
                        "5": "up",
                    },
                },
            },
            "labels": {
                "reference": {"offset_mm": [0, -2.54], "font_height_mm": 1.27},
                "value": {"offset_mm": [0, 2.54], "font_height_mm": 1.27},
            },
        }
    )

    result = layout_unit(ir, cfg)
    assert result.placements
    assert len(result.placements) > 0
