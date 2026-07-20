"""Regression tests for the LM321 CCS auto-layout flow.

These tests exercise the end-to-end path from the SPICE netlist in
``lm321_ccs/ccs_v5_bjt.cir`` to a KiCad schematic produced by
``circuit_graph.layout``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ksa = pytest.importorskip("kicad_sch_api")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LM321_CCS_DIR = _PROJECT_ROOT / "lm321_ccs"

sys.path.insert(0, str(_LM321_CCS_DIR))
from convert_to_kicad import _ir_from_spice, abs_pins, create_ccs_schematic_layout


def _ccs_spice_path() -> Path:
    path = _LM321_CCS_DIR / "ccs_v5_bjt.cir"
    if not path.exists():
        pytest.skip(f"SPICE file not found: {path}")
    return path


def _generate_layout(tmp_path: Path) -> Path:
    """Run the auto-layout flow and return the saved schematic path."""
    out = tmp_path / "test_layout2.kicad_sch"
    create_ccs_schematic_layout(str(out))
    return out


def test_lm321_ccs_bjt_4pin_maps_to_3pin_symbol():
    """The SPICE BJT model has a substrate node that must be dropped.

    The KiCad ``Transistor_BJT:BC547`` symbol only has C/B/E pins, so the
    4-node SPICE line ``Q1 N_COL N_BASE N_SENSE 0 NPN_PWR`` must be reduced
    to three pins before layout.
    """
    ir = _ir_from_spice(_ccs_spice_path())

    q1 = next(c for c in ir.components if c.id == "Q1")
    assert [p.number for p in q1.pins] == ["1", "2", "3"]

    pin_nets = {
        conn.pin_id.split(".")[-1]: conn.net_id
        for conn in ir.connections
        if conn.pin_id.startswith("Q1.")
    }
    assert pin_nets == {"1": "N_COL", "2": "N_BASE", "3": "N_SENSE"}


def _component_pin(
    comp, pin_num: str = "1"
) -> tuple[float, float]:
    """Return the absolute position of ``pin_num`` on ``comp``.

    Power symbols loaded from a saved schematic may have no cached pin list;
    their connection point is the component origin.
    """
    if comp.pins:
        return abs_pins(comp)[pin_num]
    return (comp.position.x, comp.position.y)


def test_lm321_ccs_power_symbols_and_pwr_flags_are_wired(tmp_path: Path) -> None:
    """Power symbols and PWR_FLAGs must be auto-placed and wired to the rails."""
    sch_path = _generate_layout(tmp_path)
    sch = ksa.load_schematic(str(sch_path))

    comps_by_ref = {c.reference: c for c in sch.components}

    for ref, lib_id in [
        ("#PWR01", "power:+12V"),
        ("#PWR02", "power:GND"),
        ("#FLG01", "power:PWR_FLAG"),
        ("#FLG02", "power:PWR_FLAG"),
    ]:
        assert ref in comps_by_ref, f"missing {ref} ({lib_id})"

    # PWR_FLAG pins must connect to their corresponding power-symbol pin.
    wire_endpoints: set[tuple[float, float]] = set()
    for wire in sch.wires:
        wire_endpoints.add((round(wire.start.x, 3), round(wire.start.y, 3)))
        wire_endpoints.add((round(wire.end.x, 3), round(wire.end.y, 3)))

    pwr_12v_pin = _component_pin(comps_by_ref["#PWR01"])
    pwr_gnd_pin = _component_pin(comps_by_ref["#PWR02"])
    assert pwr_12v_pin in wire_endpoints, "+12V power symbol is not wired"
    assert pwr_gnd_pin in wire_endpoints, "GND power symbol is not wired"

    flag_12v_pin = _component_pin(comps_by_ref["#FLG01"])
    flag_gnd_pin = _component_pin(comps_by_ref["#FLG02"])
    assert flag_12v_pin in wire_endpoints, "+12V PWR_FLAG is not wired"
    assert flag_gnd_pin in wire_endpoints, "GND PWR_FLAG is not wired"

    # The PWR_FLAG should sit on the same net as the power symbol it drives.
    assert _wired_together(sch, flag_12v_pin, pwr_12v_pin)
    assert _wired_together(sch, flag_gnd_pin, pwr_gnd_pin)


def _wired_together(sch, a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Return True if two points are connected by wires and junctions."""
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(p: tuple[float, float]) -> tuple[float, float]:
        parent.setdefault(p, p)
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(p: tuple[float, float], q: tuple[float, float]) -> None:
        rp, rq = find(p), find(q)
        if rp != rq:
            parent[rq] = rp

    def snap(p: tuple[float, float]) -> tuple[float, float]:
        return (round(p[0], 3), round(p[1], 3))

    for wire in sch.wires:
        start = snap((wire.start.x, wire.start.y))
        end = snap((wire.end.x, wire.end.y))
        union(start, end)

    for junc in sch.junctions:
        union(snap((junc.position.x, junc.position.y)), snap((junc.position.x, junc.position.y)))

    return find(snap(a)) == find(snap(b))


def test_lm321_ccs_wires_are_orthogonal_and_grid_aligned(tmp_path: Path) -> None:
    """All auto-generated wires must be axis-aligned and snapped to the grid.

    The KiCad library symbols used in this design place pins on a 1.27 mm
    grid, so wire endpoints are validated against the 1.27 mm char-unit grid.
    """
    sch_path = _generate_layout(tmp_path)
    sch = ksa.load_schematic(str(sch_path))

    grid = 1.27
    for wire in sch.wires:
        start = (wire.start.x, wire.start.y)
        end = (wire.end.x, wire.end.y)
        # Axis-aligned.
        assert start[0] == end[0] or start[1] == end[1], (
            f"Wire {start} -> {end} is not orthogonal"
        )
        # Endpoints on the 1.27 mm grid.
        for pt in (start, end):
            for coord in pt:
                snapped = round(coord / grid) * grid
                assert abs(snapped - coord) < 1e-6, (
                    f"Coordinate {coord} is not on the {grid} mm grid"
                )


def test_lm321_ccs_native_erc_is_clean(tmp_path: Path) -> None:
    """The auto-generated schematic must pass the native ERC with 0 errors."""
    sch_path = _generate_layout(tmp_path)

    from circuit_graph.erc.extractor import NetlistExtractor
    from circuit_graph.erc import ElectricalRulesChecker

    extractor = NetlistExtractor()
    sch = extractor.extract(sch_path)
    report = ElectricalRulesChecker().run(sch)

    assert report.error_count == 0, f"ERC errors: {report.violations}"
    assert report.warning_count == 0, f"ERC warnings: {report.violations}"
