"""§3 power-rail placement assertion tests.

10 tests total: 5 for ``PowerAtTop`` (pass / fail / boundary / no-position /
no-power-pin) and 5 for ``PowerLayerHierarchy`` (separated / overlap /
single-rail vacuous / mixed-with-ground / multi-rail-clean).
"""
from __future__ import annotations

from circuit_graph.layout_check.assertions.power import (
    PowerAtTop,
    PowerLayerHierarchy,
)
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutNet,
    LayoutPin,
    LayoutSchematic,
)

# -- shared fixtures ----------------------------------------------------------


def _comp(id_, pos=None, pins=(), type_="ic", value=None):
    """Build a LayoutComponent with a derived refdes_prefix."""
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


def _net(name, kind="power"):
    return LayoutNet(name=name, kind=kind, members=[])


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components),
        nets=list(nets),
        wires=list(wires),
    )


def _pin(number, net, x=None, y=None):
    return LayoutPin(number=number, net=net, x_mm=x, y_mm=y)


def _pin_5v():
    return _pin("3", "5V")


def _pin_3v3():
    return _pin("3", "3V3")


# -- PowerAtTop ---------------------------------------------------------------


def test_power_at_top_passes_when_high_on_page():
    # y=208 mm: above the 205 mm tolerance boundary (page_height=210 - tol=5).
    c = _comp("U1", pos=(0.0, 208.0), pins=[_pin_5v()])
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerAtTop().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


def test_power_at_top_fails_low():
    c = _comp("U1", pos=(0.0, 50.0), pins=[_pin_5v()])
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerAtTop().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert len(vs) == 1
    assert vs[0].assert_kind == "power-at-top"
    assert vs[0].context["actual_y_mm"] == 50.0
    assert vs[0].context["required_y_min_mm"] == 205.0
    assert vs[0].context["page_height_mm"] == 210.0
    assert vs[0].context["tolerance_mm"] == 5


def test_power_at_top_boundary_at_205mm_passes():
    """Boundary semantics: y == page_height - tolerance passes (== 205 mm)."""
    c = _comp("U1", pos=(0.0, 205.0), pins=[_pin_5v()])
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerAtTop().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


def test_power_at_top_skips_components_without_position():
    # No fabrication: components with no geometry are skipped silently.
    c = _comp("U1", pos=None, pins=[_pin_5v()])
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerAtTop().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


def test_power_at_top_skips_components_without_power_pin():
    # Capacitor at y=50 with no pin to a power net — vacuous for this rule.
    c = _comp(
        "C1", pos=(0.0, 50.0), pins=[], type_="capacitor", value="100nF"
    )
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerAtTop().evaluate(s, None, {"tolerance_mm": 5, "page_height_mm": 210})
    assert vs == []


# -- PowerLayerHierarchy ------------------------------------------------------


def test_power_layer_hierarchy_passes_when_bands_separated():
    # 5V near the top (y=200), 3V3 near the bottom (y=50) → no overlap.
    c1 = _comp("U1", pos=(0.0, 200.0), pins=[_pin_5v()])
    c2 = _comp("U2", pos=(50.0, 50.0), pins=[_pin_3v3()])
    s = _sch([c1, c2], nets=[_net("5V", "power"), _net("3V3", "power")])
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 5})
    assert vs == []


def test_power_layer_hierarchy_fails_when_bands_overlap():
    # 5V band: y in {100, 95} → [95, 100]
    # 3V3 band: y in {96, 99} → [96, 99]
    # overlap = min(100, 99) - max(95, 96) = 99 - 96 = 3 mm > tolerance 1 mm.
    c1 = _comp("U1", pos=(0.0, 100.0), pins=[_pin_5v()])
    c2 = _comp("U2", pos=(50.0, 95.0), pins=[_pin("3", "5V")])
    c3 = _comp("U3", pos=(60.0, 96.0), pins=[_pin("1", "3V3")])
    c4 = _comp("U4", pos=(70.0, 99.0), pins=[_pin("2", "3V3")])
    s = _sch(
        [c1, c2, c3, c4],
        nets=[_net("5V", "power"), _net("3V3", "power")],
    )
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 1})
    assert len(vs) == 1
    ctx = vs[0].context
    assert vs[0].assert_kind == "power-layer-hierarchy"
    assert ctx["voltage_low_v"] == 3.3
    assert ctx["voltage_high_v"] == 5.0
    assert ctx["voltage_tolerance_mm"] == 1
    assert abs(ctx["overlap_mm"] - 3.0) < 1e-9
    assert ctx["band_low_v"] == [96.0, 99.0]
    assert ctx["band_high_v"] == [95.0, 100.0]


def test_power_layer_hierarchy_boundary_overlap_passes():
    # Boundary semantics: overlap == tolerance → passes.
    # 5V band: y in {90, 100} → [90, 100]
    # 3V3 band: y in {94, 96} → [94, 96]
    # overlap = min(100, 96) - max(90, 94) = 96 - 94 = 2 mm.
    # Set tolerance == 2 → boundary pass.
    c1 = _comp("U1", pos=(0.0, 100.0), pins=[_pin_5v()])
    c2 = _comp("U2", pos=(50.0, 90.0), pins=[_pin("3", "5V")])
    c3 = _comp("U3", pos=(60.0, 94.0), pins=[_pin("1", "3V3")])
    c4 = _comp("U4", pos=(70.0, 96.0), pins=[_pin("2", "3V3")])
    s = _sch(
        [c1, c2, c3, c4],
        nets=[_net("5V", "power"), _net("3V3", "power")],
    )
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 2})
    assert vs == []


def test_power_layer_hierarchy_vacuous_single_voltage():
    # Only one power rail → cannot compare → vacuous pass.
    c = _comp("U1", pos=(0.0, 100.0), pins=[_pin_5v()])
    s = _sch([c], nets=[_net("5V", "power")])
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 5})
    assert vs == []


def test_power_layer_hierarchy_ignores_ground_and_signal():
    # 5V + GND + signal nets at the same y → only one power rail → vacuous.
    c1 = _comp("U1", pos=(0.0, 100.0), pins=[_pin_5v()])
    c2 = _comp("U2", pos=(50.0, 100.0), pins=[_pin("3", "GND")])
    c3 = _comp("U3", pos=(100.0, 100.0), pins=[_pin("3", "TX_OUT")])
    s = _sch(
        [c1, c2, c3],
        nets=[
            _net("5V", "power"),
            _net("GND", "ground"),
            _net("TX_OUT", "signal"),
        ],
    )
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 1})
    assert vs == []


def test_power_layer_hierarchy_handles_3v3_notation():
    # "3V3" should parse to voltage 3.3, "5V" to 5.0 — both distinct.
    # 5V band: y in {100, 105} → [100, 105]
    # 3V3 band: y in {102, 104} → [102, 104]
    # overlap = min(105, 104) - max(100, 102) = 104 - 102 = 2 mm > tol 1 mm.
    c1 = _comp("U1", pos=(0.0, 100.0), pins=[_pin_5v()])
    c2 = _comp("U2", pos=(50.0, 105.0), pins=[_pin_5v()])
    c3 = _comp("U3", pos=(60.0, 102.0), pins=[_pin_3v3()])
    c4 = _comp("U4", pos=(70.0, 104.0), pins=[_pin("2", "3V3")])
    s = _sch(
        [c1, c2, c3, c4],
        nets=[_net("5V", "power"), _net("3V3", "power")],
    )
    vs = PowerLayerHierarchy().evaluate(s, None, {"voltage_tolerance_mm": 1})
    assert len(vs) == 1
    assert vs[0].context["voltage_low_v"] == 3.3
    assert vs[0].context["voltage_high_v"] == 5.0
    assert abs(vs[0].context["overlap_mm"] - 2.0) < 1e-9
