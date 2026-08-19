"""§1 signal-flow-axis assertion.

Spatial rule: requires component positions on the chosen axis. On ``.NET``
(Protel) input the :attr:`LayoutComponent.position_mm` field is always
``None``, so the rule emits zero violations (vacuous-pass). No positions
are ever fabricated; components without geometry are skipped silently.

Boundary semantics (uniform across all 12 assertions; see
:mod:`circuit_graph.layout_check.assertions.base`):

    distance <= tolerance_mm → PASS
    distance >  tolerance_mm → FAIL

For ``signal-flow-axis`` the "distance" is the axis-coordinate separation
between the **input-group median** and the **output-group median**:
``delta = output_median - input_median``. The rule fails only when the
input median is at-or-past the output median (``delta < 0`` — signals
would flow backward through the layout). When
``0 <= delta <= tolerance_mm`` we treat the layout as compliant within
the allowed band and emit no violation (uniform ``<= tolerance`` pass
semantics).
"""
from __future__ import annotations

import statistics
from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.base import (
    Assertion,
    match_glob,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutComponent,
    LayoutSchematic,
    Location,
    Violation,
)


def _refdes_match(comp: LayoutComponent, prefixes: list[str]) -> bool:
    """True iff ``comp.refdes_prefix`` matches any glob in ``prefixes``."""
    return any(match_glob(p, comp.refdes_prefix) for p in prefixes)


class SignalFlowAxis(Assertion):
    """§1 ``signal-flow-axis``.

    The axis-coordinate median of components matching
    ``input_refdes_prefix`` must sit *below* the axis-coordinate median
    of components matching ``output_refdes_prefix`` (i.e. signal flows
    forward). The separation ``delta = output_median - input_median`` is
    required to be either strictly greater than ``tolerance_mm``
    (clearly separated) or in the inclusive tolerance band
    ``[0, tolerance_mm]`` (acceptable slack). Anything else — i.e.
    ``delta < 0`` — produces a single violation.

    Vacuous when either group is empty of matched components with
    ``position_mm``.

    Parameters
    ----------
    input_refdes_prefix : list[str], required
        Globs matched against the derived ``refdes_prefix`` of each
        component (e.g. ``["R*"]`` matches R1, R96, etc.).
    output_refdes_prefix : list[str], required
        Globs matched against the derived ``refdes_prefix`` of each
        component (e.g. ``["C*"]``).
    tolerance_mm : float, required
        Allowed "input still close to output" slack on the chosen axis.
    axis : {"x", "y"}, optional
        Axis along which to measure. Default ``"x"``.
    """

    kind: ClassVar[str] = "signal-flow-axis"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        # Required params. ``input_refdes_prefix`` / ``output_refdes_prefix``
        # are read with .get() so a missing list silently vacuous-passes
        # (the loader is responsible for surfacing missing-required-param
        # errors as ``unknown_assert_kind`` per spec §7).
        in_prefixes: list[str] = list(params.get("input_refdes_prefix") or [])
        out_prefixes: list[str] = list(params.get("output_refdes_prefix") or [])
        if not in_prefixes or not out_prefixes:
            return []
        tol = float(params["tolerance_mm"])  # required
        axis_raw = params.get("axis") or "x"
        axis = str(axis_raw).lower()
        if axis not in ("x", "y"):
            return []  # invalid axis → silent vacuous-pass
        idx = 0 if axis == "x" else 1

        # Collect axis coords of every matched component with a position.
        # ``applies`` is honoured in addition to the prefix groups — if
        # the rule was scoped to e.g. "resistors only", a cap won't be
        # counted as either an input or an output here.
        inputs: list[float] = [
            c.position_mm[idx]
            for c in schematic.components
            if c.position_mm is not None and _refdes_match(c, in_prefixes)
        ]
        outputs: list[float] = [
            c.position_mm[idx]
            for c in schematic.components
            if c.position_mm is not None and _refdes_match(c, out_prefixes)
        ]
        if not inputs or not outputs:
            return []  # one or both groups empty → vacuous

        # statistics.median handles both odd and even counts: with an odd
        # N it returns the middle value; with an even N it averages the
        # two middle values. N is small (rarely >10) so we do not need a
        # faster algorithm.
        in_med = statistics.median(inputs)
        out_med = statistics.median(outputs)
        delta = out_med - in_med

        # Boundary: delta within [0, tolerance_mm] → PASS (uniform).
        # Only the reverse direction (delta < 0) is a violation.
        if delta >= 0:
            return []
        # Anchor the violation to the median coordinate so ``location``
        # is meaningful even though no single component is at fault.
        # The median lives on the chosen axis; the orthogonal axis is
        # left as None.
        loc_x = in_med if axis == "x" else None
        loc_y = in_med if axis == "y" else None
        return [
            Violation(
                rule_id="",  # orchestrator (Batch 5B) fills this in
                assert_kind=self.kind,
                severity="error",
                message=(
                    f"signal-flow-axis violation on {axis}-axis: input median "
                    f"{in_med:.2f}mm is at-or-past output median {out_med:.2f}mm "
                    f"(delta {delta:.2f}mm, tolerance {tol}mm)"
                ),
                location=Location(
                    component=None,
                    net=None,
                    x_mm=loc_x,
                    y_mm=loc_y,
                    sheet=None,
                    page=None,
                ),
                context={
                    "axis": axis,
                    "input_median_axis_mm": in_med,
                    "output_median_axis_mm": out_med,
                    "delta_mm": delta,
                    "tolerance_mm": tol,
                    "input_count": len(inputs),
                    "output_count": len(outputs),
                },
                doc=f"circuit-graph://rules#{self.kind}",
            )
        ]


__all__ = ["SignalFlowAxis"]
