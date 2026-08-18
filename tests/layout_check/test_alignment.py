"""Alignment assertion tests (design doc §5 layout rules)."""
from __future__ import annotations

from circuit_graph.layout_check.assertions.alignment import (
    GridAlignment,
    SameOrientation,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutComponent,
    LayoutSchematic,
)


def _comp(
    id_,
    rotation=None,
    pos=None,
    type_="ic",
    value="X",
):
    return LayoutComponent(
        id=id_,
        type=type_,
        value=value,
        position_mm=pos,
        rotation_deg=rotation,
        refdes_prefix="".join(c for c in id_ if c.isalpha()) or id_,
        sheet=None,
        page=None,
        pins=[],
    )


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(components=list(components), nets=list(nets), wires=list(wires))


# -- SameOrientation --


def test_same_orientation_passes_when_all_rotations_equal():
    s = _sch([_comp("U1", rotation=0.0), _comp("U2", rotation=0.0)])
    assert SameOrientation().evaluate(s, None, {"tolerance_deg": 5}) == []


def test_same_orientation_fails_when_range_exceeds_tolerance():
    s = _sch(
        [
            _comp("U1", rotation=0.0),
            _comp("U2", rotation=10.0),
            _comp("U3", rotation=45.0),
        ]
    )
    vs = SameOrientation().evaluate(s, None, {"tolerance_deg": 5})
    # U1 (min) and U3 (max) are the boundary culprits → 2 violations.
    assert len(vs) == 2
    assert all(v.assert_kind == "same-orientation" for v in vs)
    assert vs[0].context["range_deg"] == 45.0
    assert vs[0].context["tolerance_deg"] == 5
    assert vs[0].context["applied_to_count"] == 3


def test_same_orientation_boundary_passes_at_exactly_tolerance():
    # range == tolerance → PASS (boundary semantics)
    s = _sch([_comp("U1", rotation=0.0), _comp("U2", rotation=5.0)])
    assert SameOrientation().evaluate(s, None, {"tolerance_deg": 5}) == []


def test_same_orientation_filter_narrows_match():
    # Resistors at varied rotations; filter to U* → 1 IC, no comparison possible.
    s = _sch(
        [
            _comp("R1", rotation=0.0, type_="resistor"),
            _comp("R2", rotation=90.0, type_="resistor"),
            _comp("U1", rotation=10.0),
        ]
    )
    applies = AppliesTo(refdes_prefix=["U*"], type=[], net=[], kind=[])
    assert SameOrientation().evaluate(s, applies, {"tolerance_deg": 5}) == []


def test_same_orientation_vacuous_when_rotation_none():
    # kicad-sch-api currently returns rotation_deg=None for all components.
    # Same-orientation should emit zero violations in that case (no fabrication).
    s = _sch([_comp("U1", rotation=None), _comp("U2", rotation=None)])
    assert SameOrientation().evaluate(s, None, {"tolerance_deg": 5}) == []


# -- GridAlignment --


def test_grid_alignment_passes_when_on_grid():
    s = _sch(
        [
            _comp("R1", pos=(0.0, 0.0)),
            _comp("R2", pos=(2.54, 5.08)),
        ]
    )
    assert GridAlignment().evaluate(s, None, {"grid_mm": 2.54}) == []


def test_grid_alignment_fails_when_off_grid():
    s = _sch(
        [
            _comp("R1", pos=(1.0, 1.0)),
            _comp("R2", pos=(2.54, 5.0)),
        ]
    )
    vs = GridAlignment().evaluate(s, None, {"grid_mm": 2.54})
    assert len(vs) == 2
    assert all(v.assert_kind == "grid-alignment" for v in vs)
    offsets = [(v.context["x_offset_mm"], v.context["y_offset_mm"]) for v in vs]
    # Components are off-grid by more than 1e-6 in at least one axis.
    assert all(abs(x) > 1e-6 or abs(y) > 1e-6 for x, y in offsets)


def test_grid_alignment_boundary_passes_at_exactly_grid():
    s = _sch(
        [
            _comp("R1", pos=(0.0, 2.54)),
            _comp("R2", pos=(5.08, 0.0)),
        ]
    )
    assert GridAlignment().evaluate(s, None, {"grid_mm": 2.54}) == []


def test_grid_alignment_vacuous_when_position_none():
    # Protel input has all positions None → no violations, no fabrication.
    s = _sch([_comp("R1", pos=None), _comp("R2", pos=None)])
    assert GridAlignment().evaluate(s, None, {"grid_mm": 2.54}) == []


def test_grid_alignment_filter_narrows_match():
    # 1 cap off-grid, 1 IC on-grid. Filter to cap → 1 violation. Filter to IC → 0.
    s = _sch(
        [
            _comp("C1", pos=(1.0, 1.0), type_="capacitor"),
            _comp("U1", pos=(2.54, 5.08)),
        ]
    )
    applies_caps = AppliesTo(refdes_prefix=["C*"], type=[], net=[], kind=[])
    applies_ics = AppliesTo(refdes_prefix=["U*"], type=[], net=[], kind=[])
    cap_violations = GridAlignment().evaluate(s, applies_caps, {"grid_mm": 2.54})
    assert len(cap_violations) == 1
    assert cap_violations[0].context["component_id"] == "C1"
    assert GridAlignment().evaluate(s, applies_ics, {"grid_mm": 2.54}) == []
