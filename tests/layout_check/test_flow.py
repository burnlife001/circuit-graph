"""§1 signal-flow-axis assertion tests.

8 tests covering pass / fail / boundary / vacuous / position-skip /
y-axis / odd-count-median paths.
"""
from __future__ import annotations

from circuit_graph.layout_check.assertions.flow import SignalFlowAxis
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutSchematic,
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


def _sch(components, nets=(), wires=()):
    return LayoutSchematic(
        components=list(components),
        nets=list(nets),
        wires=list(wires),
    )


def _params(axis="x", tol=5.0, in_p=("R*",), out_p=("C*",)):
    return {
        "input_refdes_prefix": list(in_p),
        "output_refdes_prefix": list(out_p),
        "tolerance_mm": tol,
        "axis": axis,
    }


# -- pass / fail / boundary ---------------------------------------------------


def test_signal_flow_axis_passes_when_inputs_left_of_outputs():
    # axis=x, inputs median x=11, outputs median x=102.5, tolerance=5.
    # delta=91.5 > 5 → PASS.
    s = _sch([
        _comp("R1", pos=(10, 0)),
        _comp("R2", pos=(12, 0)),
        _comp("C1", pos=(100, 0)),
        _comp("C2", pos=(105, 0)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params()) == []


def test_signal_flow_axis_fails_when_inputs_past_outputs():
    # Inputs at high x (100, 110), outputs at low x (50, 55).
    # Input median = 105, output median = 52.5, delta = -52.5 < 0 → VIOLATION.
    s = _sch([
        _comp("R1", pos=(100, 0)),
        _comp("R2", pos=(110, 0)),
        _comp("C1", pos=(50, 0)),
        _comp("C2", pos=(55, 0)),
    ])
    vs = SignalFlowAxis().evaluate(s, None, _params())
    assert len(vs) == 1
    assert vs[0].assert_kind == "signal-flow-axis"
    ctx = vs[0].context
    assert ctx["axis"] == "x"
    assert ctx["input_median_axis_mm"] == 105.0
    assert ctx["output_median_axis_mm"] == 52.5
    assert ctx["delta_mm"] == -52.5
    assert ctx["tolerance_mm"] == 5.0
    assert ctx["input_count"] == 2
    assert ctx["output_count"] == 2


def test_signal_flow_axis_boundary_passes_when_delta_equals_tolerance():
    # input median 10, output median 12, tolerance 2.
    # delta = 2 → boundary (delta == tolerance) → PASS per uniform semantics.
    s = _sch([
        _comp("R1", pos=(10, 0)),
        _comp("C1", pos=(12, 0)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params(tol=2.0)) == []


# -- vacuous / skip paths -----------------------------------------------------


def test_signal_flow_axis_vacuous_when_no_inputs():
    # No R* components → inputs list empty → vacuous.
    s = _sch([
        _comp("C1", pos=(100, 0)),
        _comp("C2", pos=(105, 0)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params()) == []


def test_signal_flow_axis_vacuous_when_no_outputs():
    # No C* components → outputs list empty → vacuous.
    s = _sch([
        _comp("R1", pos=(10, 0)),
        _comp("R2", pos=(12, 0)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params()) == []


def test_signal_flow_axis_skips_components_without_position():
    # Inputs without position → inputs list empty → vacuous (no fabrication).
    s = _sch([
        _comp("R1", pos=None),
        _comp("R2", pos=None),
        _comp("C1", pos=(100, 0)),
        _comp("C2", pos=(105, 0)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params()) == []


# -- y-axis / odd-count-median ------------------------------------------------


def test_signal_flow_axis_uses_y_axis_when_specified():
    # On the x-axis these would fail (inputs past outputs).
    # On the y-axis inputs (y=10,12) are clearly below outputs (y=100,105)
    # → PASS.
    s = _sch([
        _comp("R1", pos=(100, 10)),
        _comp("R2", pos=(110, 12)),
        _comp("C1", pos=(50, 100)),
        _comp("C2", pos=(55, 105)),
    ])
    assert SignalFlowAxis().evaluate(s, None, _params(axis="y")) == []
    # Sanity check: the same schematic on the x-axis DOES fail.
    vs_x = SignalFlowAxis().evaluate(s, None, _params(axis="x"))
    assert len(vs_x) == 1


def test_signal_flow_axis_handles_median_with_odd_count():
    # 3 inputs (x=10,20,30 → median=20) and 3 outputs (x=100,200,300 →
    # median=200). delta=180 > 5 → PASS. statistics.median picks the
    # middle value for an odd N.
    s = _sch([
        _comp("R1", pos=(10, 0)),
        _comp("R2", pos=(20, 0)),
        _comp("R3", pos=(30, 0)),
        _comp("C1", pos=(100, 0)),
        _comp("C2", pos=(200, 0)),
        _comp("C3", pos=(300, 0)),
    ])
    vs = SignalFlowAxis().evaluate(s, None, _params())
    assert vs == []
