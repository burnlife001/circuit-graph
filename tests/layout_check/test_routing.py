"""§4 no-trace-through-component assertion tests.

7 tests covering pass / fail / boundary / position-skip / no-wires /
per-wire-per-component fan-out / wire-endpoint-on-disc-boundary paths.
"""
from __future__ import annotations

from circuit_graph.layout_check.assertions.routing import (
    NoTraceThroughComponent,
)
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutSchematic,
    LayoutWire,
)


def _comp(id_, pos=None, type_="ic", value=None):
    return LayoutComponent(
        id=id_,
        type=type_,
        value=value,
        position_mm=pos,
        rotation_deg=None,
        refdes_prefix="".join(c for c in id_ if c.isalpha()) or id_,
        sheet=None,
        page=None,
        pins=[],
    )


def _wire(start, end, net=None):
    return LayoutWire(start_mm=start, end_mm=end, net=net)


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components),
        nets=list(nets),
        wires=list(wires),
    )


def _params(tol=1.0, box=2.5):
    return {"tolerance_mm": tol, "component_box_half_mm": box}


# -- pass / fail / boundary ---------------------------------------------------


def test_no_trace_through_component_passes_when_wires_away():
    # Wire (0,0)→(50,0); component at (100,100) — far above the wire.
    # Min point-to-segment distance is ~111.8mm > limit 3.5mm → PASS.
    s = _sch(
        [_comp("R1", pos=(100, 100))],
        wires=[_wire((0, 0), (50, 0))],
    )
    assert NoTraceThroughComponent().evaluate(s, None, _params()) == []


def test_no_trace_through_component_fails_when_wire_near_component():
    # Wire (0,0)→(50,0); component at (25,0). Wire passes through the
    # component position → distance = 0 ≤ limit 3.5mm → VIOLATION.
    s = _sch(
        [_comp("R1", pos=(25, 0))],
        wires=[_wire((0, 0), (50, 0))],
    )
    vs = NoTraceThroughComponent().evaluate(s, None, _params())
    assert len(vs) == 1
    assert vs[0].assert_kind == "no-trace-through-component"
    assert vs[0].location.component == "R1"
    ctx = vs[0].context
    assert ctx["component_id"] == "R1"
    assert ctx["distance_mm"] == 0.0
    assert ctx["tolerance_mm"] == 1.0
    assert ctx["component_box_half_mm"] == 2.5
    assert ctx["tolerance_with_box_mm"] == 3.5


def test_no_trace_through_component_boundary_passes_at_exactly_tolerance():
    # Component at (0,5); wire (-10,0)→(10,0). The closest point on the
    # wire is (0,0); distance to (0,5) is exactly 5mm.
    # tolerance_mm=0 + box_half=5.0 → limit=5 → boundary PASS.
    s = _sch(
        [_comp("R1", pos=(0, 5))],
        wires=[_wire((-10, 0), (10, 0))],
    )
    vs = NoTraceThroughComponent().evaluate(s, None, _params(tol=0.0, box=5.0))
    assert vs == []


# -- skip / vacuous / fan-out -------------------------------------------------


def test_no_trace_through_component_skips_components_without_position():
    # Components with position_mm=None are skipped (no fabrication).
    s = _sch(
        [_comp("R1", pos=None)],
        wires=[_wire((0, 0), (50, 0))],
    )
    assert NoTraceThroughComponent().evaluate(s, None, _params()) == []


def test_no_trace_through_component_vacuous_when_no_wires():
    # Protel-style schematic: no wires → vacuous-pass even if components
    # have positions.
    s = _sch([_comp("R1", pos=(25, 0))], wires=[])
    assert NoTraceThroughComponent().evaluate(s, None, _params()) == []


def test_no_trace_through_component_per_wire_per_component_violations():
    # Two components sitting on a wire that runs from (0,0)→(50,0) — each
    # component is its own (wire, component) pair → two distinct violations.
    s = _sch(
        [_comp("R1", pos=(25, 0)), _comp("C1", pos=(35, 0))],
        wires=[_wire((0, 0), (50, 0))],
    )
    vs = NoTraceThroughComponent().evaluate(s, None, _params())
    assert len(vs) == 2
    components_hit = {v.location.component for v in vs}
    assert components_hit == {"R1", "C1"}
    for v in vs:
        assert v.context["distance_mm"] == 0.0
        assert v.context["wire_start_mm"] == (0, 0)
        assert v.context["wire_end_mm"] == (50, 0)


def test_no_trace_through_component_wire_endpoint_on_component_passes():
    # Wire (2.5,5)→(2.5,0); component at (0,0). Closest point on the
    # segment is endpoint (2.5,0); distance = 2.5mm = limit (0+2.5).
    # Boundary semantics: distance == limit → PASS.
    s = _sch(
        [_comp("R1", pos=(0, 0))],
        wires=[_wire((2.5, 5), (2.5, 0))],
    )
    vs = NoTraceThroughComponent().evaluate(s, None, _params(tol=0.0, box=2.5))
    assert vs == []
