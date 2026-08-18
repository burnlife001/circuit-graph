"""§4 no-trace-through-component assertion.

Best-effort, KiCad-only rule. On ``.NET`` (Protel) input the schematic
carries no wires (``LayoutSchematic.wires`` is empty), so the rule
emits zero violations (vacuous-pass). Components without
``position_mm`` are skipped without fabrication.

Boundary semantics: for ``no-trace-through-component`` the rule's
"distance" is the point-to-segment distance from the component
position to each wire segment. We treat the keep-out boundary as
**inclusive on the pass side**:

    distance >= limit (tolerance_mm + component_box_half_mm) → PASS
    distance <  limit                                          → VIOLATION

This is the inverse of the uniform "distance <= tolerance → PASS"
convention in :mod:`circuit_graph.layout_check.assertions.base`,
because for routing the violation means the wire has *entered* the
keep-out zone — the boundary itself is acceptable.

We treat each matched component position as a point surrounded by a
disc of radius ``tolerance_mm + component_box_half_mm`` (the
"effective keep-out zone"). Each :class:`LayoutWire` is treated as a
single segment from ``start_mm`` to ``end_mm``. If the component's
disc is penetrated by a wire segment — i.e. the point-to-segment
distance is strictly less than ``tolerance_mm + component_box_half_mm``
— a violation is emitted for that ``(wire, component)`` pair.

Note: :class:`LayoutWire` is a single segment (not a polyline), so
each ``wires`` record counts as exactly one line segment here. Each
wire's ``net`` field is not consulted (the rule is purely geometric).
"""
from __future__ import annotations

import math
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


def _point_to_segment_distance(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Euclidean distance from point ``p`` to the segment ``a → b``.

    Uses the standard "project ``p`` onto the line, clamp t to [0, 1]"
    approach. When ``a == b`` the segment degenerates to a point and
    we fall back to ``|p - a|``.
    """
    px, py = p
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def _component_matches(comp: LayoutComponent, applies: AppliesTo | None) -> bool:
    """Filter a component against an :class:`AppliesTo` block.

    Mirrors the helper in :mod:`circuit_graph.layout_check.assertions.flow`
    (kept local per the Batch-5 task scope: each batch is independent
    and may not import helpers from sibling batches).
    """
    if applies is None:
        return True
    if applies.refdes_prefix and not any(
        match_glob(p, comp.refdes_prefix)
        for p in applies.refdes_prefix
    ):
        return False
    if applies.type and not any(t == comp.type for t in applies.type):
        return False
    return True


class NoTraceThroughComponent(Assertion):
    """§4 ``no-trace-through-component``.

    No wire segment may pass within ``tolerance_mm +
    component_box_half_mm`` of the position of any matched component.
    Each matched component is treated as a point surrounded by a disc
    of radius ``tolerance_mm + component_box_half_mm``. Each wire is
    treated as a single segment from ``start_mm`` to ``end_mm``. One
    violation is emitted per ``(wire, component)`` pair where the disc
    overlaps the segment.

    Vacuous on ``.NET`` input (``schematic.wires == []``). Components
    without ``position_mm`` are skipped.

    Parameters
    ----------
    tolerance_mm : float, required
        Per-component keep-out distance in mm.
    component_box_half_mm : float, optional
        Half-side of the component bbox used as a proxy for body size.
        Default ``2.5`` (5 mm bbox).
    apply_to_applies_components_only : bool, optional
        Reserved; today only "matched components" are evaluated
        regardless of this flag's value. Recorded in the violation
        context for forward compatibility. Default ``True``.
    """

    kind: ClassVar[str] = "no-trace-through-component"
    _DEFAULT_BOX_HALF_MM: float = 2.5

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        tolerance_mm = float(params["tolerance_mm"])  # required
        box_half = float(
            params.get("component_box_half_mm", self._DEFAULT_BOX_HALF_MM)
        )
        limit = tolerance_mm + box_half
        applies_only = bool(
            params.get("apply_to_applies_components_only", True)
        )

        # Vacuous-pass on Protel: ``schematic.wires`` is empty. This is
        # the documented behaviour per spec §1.1 and §7 — the loader
        # counts the rule in ``rules_skipped_unsupported_input`` and the
        # checker never invokes us for unsupported input, but we still
        # short-circuit defensively in case of direct invocation.
        if not schematic.wires:
            return []

        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if c.position_mm is None:
                continue  # no fabrication
            for wire in schematic.wires:
                d = _point_to_segment_distance(
                    c.position_mm, wire.start_mm, wire.end_mm
                )
                # Boundary is inclusive on the PASS side for this rule.
                # ``d >= limit`` → wire outside the disc or exactly on
                # its boundary → no violation. ``d < limit`` → wire has
                # entered the keep-out disc → violation.
                if d >= limit:
                    continue
                violations.append(
                    Violation(
                        rule_id="",
                        assert_kind=self.kind,
                        severity="error",
                        message=(
                            f"wire {wire.start_mm}→{wire.end_mm}mm passes through "
                            f"component {c.id} at {c.position_mm}mm "
                            f"(distance {d:.2f}mm, limit {limit:.2f}mm)"
                        ),
                        location=Location(
                            component=c.id,
                            net=None,
                            x_mm=c.position_mm[0],
                            y_mm=c.position_mm[1],
                            sheet=c.sheet,
                            page=c.page,
                        ),
                        context={
                            "wire_uuid": getattr(wire, "uuid", None),
                            "wire_start_mm": wire.start_mm,
                            "wire_end_mm": wire.end_mm,
                            "wire_net": wire.net,
                            "component_id": c.id,
                            "distance_mm": d,
                            "tolerance_mm": tolerance_mm,
                            "component_box_half_mm": box_half,
                            "tolerance_with_box_mm": limit,
                            "apply_to_applies_components_only": applies_only,
                        },
                        doc=f"circuit-graph://rules#{self.kind}",
                    )
                )
        return violations


__all__ = ["NoTraceThroughComponent"]
