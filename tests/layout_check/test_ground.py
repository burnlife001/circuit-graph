"""§3 ground placement assertion tests.

8 tests total: 4 for ``GndAtBottom`` (pass / fail / boundary / no-position)
and 4 for ``SingleStarPoint`` (all-close / one-far / no-AGND-nets / custom-
patterns).
"""
from __future__ import annotations

from circuit_graph.layout_check.assertions.ground import (
    GndAtBottom,
    SingleStarPoint,
)
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutNet,
    LayoutPin,
    LayoutSchematic,
)

# -- shared fixtures ----------------------------------------------------------


def _comp(id_, pos=None, pins=(), type_="ic", value=None):
    return LayoutComponent(
        id=id_,
        type=type_,
        value=value,
        position_mm=pos,
        rotation_deg=None,
        refdes_prefix="".join(c for c in id_ if c.isalpha()) or id_,
        sheet=None,
        page=None,
        pins=list(pins),
    )


def _net(name, kind="ground"):
    return LayoutNet(name=name, kind=kind, members=[])


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components),
        nets=list(nets),
        wires=list(wires),
    )


def _pin(number, net, x=None, y=None):
    return LayoutPin(number=number, net=net, x_mm=x, y_mm=y)


def _pin_agnd():
    return _pin("1", "AGND")


# -- GndAtBottom --------------------------------------------------------------


def test_gnd_at_bottom_passes_when_low_on_page():
    # y=3 mm: within 5 mm tolerance of the page bottom.
    c = _comp("U1", pos=(0.0, 3.0), pins=[_pin_agnd()])
    s = _sch([c], nets=[_net("AGND", "ground")])
    vs = GndAtBottom().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


def test_gnd_at_bottom_fails_high():
    # y=200 mm: way above the 5 mm tolerance from the bottom.
    c = _comp("U1", pos=(0.0, 200.0), pins=[_pin_agnd()])
    s = _sch([c], nets=[_net("AGND", "ground")])
    vs = GndAtBottom().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert len(vs) == 1
    assert vs[0].assert_kind == "gnd-at-bottom"
    assert vs[0].context["actual_y_mm"] == 200.0
    assert vs[0].context["required_y_max_mm"] == 5
    assert vs[0].context["tolerance_mm"] == 5
    assert vs[0].context["page_height_mm"] == 210


def test_gnd_at_bottom_boundary_at_5mm_passes():
    """Boundary semantics: y == tolerance passes (== 5 mm)."""
    c = _comp("U1", pos=(0.0, 5.0), pins=[_pin_agnd()])
    s = _sch([c], nets=[_net("AGND", "ground")])
    vs = GndAtBottom().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


def test_gnd_at_bottom_skips_components_without_position():
    # No fabrication: components with no geometry are skipped silently.
    c = _comp("U1", pos=None, pins=[_pin_agnd()])
    s = _sch([c], nets=[_net("AGND", "ground")])
    vs = GndAtBottom().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


# -- SingleStarPoint ----------------------------------------------------------


def test_single_star_point_passes_when_all_components_close():
    # All four components clustered within 10 mm of the centroid (10, 10).
    # Centroid = (10, 10). Max distance = sqrt(0² + 6²) = 6 mm ≤ 10 mm → pass.
    c1 = _comp("C1", pos=(10.0, 10.0), pins=[_pin("1", "AGND")],
               type_="capacitor", value="100nF")
    c2 = _comp("C2", pos=(14.0, 10.0), pins=[_pin("1", "AGND")],
               type_="capacitor", value="100nF")
    c3 = _comp("C3", pos=(10.0, 14.0), pins=[_pin("1", "DGND")],
               type_="capacitor", value="100nF")
    c4 = _comp("C4", pos=(6.0, 10.0), pins=[_pin("1", "AGND")],
               type_="capacitor", value="100nF")
    s = _sch(
        [c1, c2, c3, c4],
        nets=[_net("AGND", "ground"), _net("DGND", "ground")],
    )
    vs = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 10})
    assert vs == []


def test_single_star_point_fails_when_one_component_too_far():
    # Three components clustered near the centroid; one outlier (C4).
    # Centroid = ((18+20+19+50)/4, 10) = (26.75, 10).
    # C1 dist = |18-26.75| = 8.75 ≤ 10 → pass
    # C2 dist = |20-26.75| = 6.75 → pass
    # C3 dist = |19-26.75| = 7.75 → pass
    # C4 dist = |50-26.75| = 23.25 → fail
    c1 = _comp("C1", pos=(18.0, 10.0), pins=[_pin("1", "AGND")])
    c2 = _comp("C2", pos=(20.0, 10.0), pins=[_pin("1", "AGND")])
    c3 = _comp("C3", pos=(19.0, 10.0), pins=[_pin("1", "DGND")])
    c4 = _comp("C4", pos=(50.0, 10.0), pins=[_pin("1", "AGND")])
    s = _sch(
        [c1, c2, c3, c4],
        nets=[_net("AGND", "ground"), _net("DGND", "ground")],
    )
    vs = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 10})
    assert len(vs) == 1
    assert vs[0].assert_kind == "single-star-point"
    ctx = vs[0].context
    cx, cy = ctx["centroid_mm"]
    assert abs(cx - 26.75) < 1e-9
    assert cy == 10.0
    assert ctx["max_merge_distance_mm"] == 10
    assert ctx["participating_count"] == 4
    assert len(ctx["violations"]) == 1
    far = ctx["violations"][0]
    assert far["component"] == "C4"
    assert far["position"] == [50.0, 10.0]
    assert abs(far["distance_mm"] - 23.25) < 1e-9


def test_single_star_point_vacuous_when_no_agnd_nets():
    # Only a generic "GND" net — default AGND/DGND patterns don't match → vacuous.
    c1 = _comp("R1", pos=(0.0, 50.0), pins=[_pin("1", "GND")])
    c2 = _comp("R2", pos=(0.0, 100.0), pins=[_pin("1", "GND")])
    s = _sch([c1, c2], nets=[_net("GND", "ground")])
    vs = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 5})
    assert vs == []


def test_single_star_point_custom_patterns_match_gnd_prefix():
    # Custom pattern "GND*" matches the plain "GND" net; user opts in.
    c1 = _comp("R1", pos=(0.0, 50.0), pins=[_pin("1", "GND")])
    c2 = _comp("R2", pos=(100.0, 100.0), pins=[_pin("1", "GND")])
    s = _sch([c1, c2], nets=[_net("GND", "ground")])
    vs = SingleStarPoint().evaluate(
        s,
        None,
        {"max_merge_distance_mm": 10, "gnd_net_patterns": ["GND*"]},
    )
    # Centroid = (50, 75); distance to either endpoint ≈ 70.7 mm > 10 → fail.
    assert len(vs) == 1
    ctx = vs[0].context
    assert ctx["gnd_net_patterns"] == ["GND*"]
    assert sorted(ctx["matched_ground_nets"]) == ["GND"]
    assert ctx["participating_count"] == 2
    assert len(ctx["violations"]) == 2


def test_single_star_point_boundary_at_max_distance_passes():
    # Two components exactly at distance 10 mm; max_merge_distance_mm == 10.
    # Centroid = (50, 50); C1 distance = sqrt(50² + 50²) ≈ 70.71 — too far.
    # Use a smaller distance: place components at (0, 0) and (10, 0).
    # Centroid = (5, 0); distances = 5 each → within 5 mm tolerance.
    c1 = _comp("C1", pos=(0.0, 0.0), pins=[_pin("1", "AGND")])
    c2 = _comp("C2", pos=(10.0, 0.0), pins=[_pin("1", "AGND")])
    s = _sch([c1, c2], nets=[_net("AGND", "ground")])
    # Centroid (5, 0). Each component distance = 5. Boundary == 5 → passes.
    vs = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 5})
    assert vs == []
    # And fails when tolerance is just under 5.
    vs2 = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 4.99})
    assert len(vs2) == 1


def test_single_star_point_skips_components_without_position():
    # Two AGND components but only one has a position → centroid is just that
    # one point, but we need ≥2 components to compute. Vacuous.
    c1 = _comp("C1", pos=None, pins=[_pin("1", "AGND")])
    c2 = _comp("C2", pos=(10.0, 10.0), pins=[_pin("1", "AGND")])
    s = _sch([c1, c2], nets=[_net("AGND", "ground")])
    vs = SingleStarPoint().evaluate(s, None, {"max_merge_distance_mm": 5})
    assert vs == []
