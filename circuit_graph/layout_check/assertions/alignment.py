"""Alignment assertions (design doc §5 — ``same-orientation`` and ``grid-alignment``).

Both rules require geometric data. On ``.NET`` (Protel) input the
:attr:`LayoutComponent.position_mm` and :attr:`LayoutComponent.rotation_deg`
fields are always ``None``, so both rules auto-pass via the "no matched
components" path — kicad-sch-api doesn't currently expose ``rotation_deg``
either, so ``same-orientation`` is forward-compatible until a real rotation
field is wired in (Batch 6 timeframe or later). We never fabricate
positions or rotations; components with ``None`` geometry are skipped.
"""
from __future__ import annotations

from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.base import (
    Assertion,
    match_glob,
    passes_or_violates,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutSchematic,
    Location,
    Violation,
)

# Tolerance for floating-point grid-snap math. 1 µm is well below any
# realistic PCB grid (0.1 mm and up) and well above IEEE-754 noise.
_GRID_EPSILON_MM = 1e-6


def _component_matches(comp, applies: AppliesTo | None) -> bool:
    """Filter a component against an :class:`AppliesTo` block.

    Mirrors the helper in :mod:`circuit_graph.layout_check.assertions.annotation`;
    duplicated here so the two batches stay independent.
    """
    if applies is None:
        return True
    if applies.refdes_prefix and not any(
        match_glob(p, comp.refdes_prefix) for p in applies.refdes_prefix
    ):
        return False
    if applies.type and not any(t == comp.type for t in applies.type):
        return False
    if applies.kind:
        comp_kinds = {pin.net for pin in comp.pins if pin.net}
        if not any(k == c for k in applies.kind for c in comp_kinds):
            return False
    if applies.net:
        comp_nets = {pin.net for pin in comp.pins if pin.net}
        if not any(match_glob(p, n) for p in applies.net for n in comp_nets):
            return False
    return True


def _loc(c) -> Location:
    return Location(
        component=c.id,
        net=None,
        x_mm=c.position_mm[0] if c.position_mm else None,
        y_mm=c.position_mm[1] if c.position_mm else None,
        sheet=c.sheet,
        page=c.page,
    )


def _violation(c, kind: str, message: str, context: dict[str, Any]) -> Violation:
    return Violation(
        rule_id="",
        assert_kind=kind,
        severity="error",
        message=message,
        location=_loc(c),
        context=context,
        doc=f"circuit-graph://rules#{kind}",
    )


class SameOrientation(Assertion):
    """§5 ``same-orientation``.

    All matched components must share rotation within ``tolerance_deg``.
    """

    kind: ClassVar[str] = "same-orientation"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        tol = float(params.get("tolerance_deg", 5))
        rots: list[tuple[Any, float]] = [
            (c, c.rotation_deg)  # type: ignore[arg-type]
            for c in schematic.components
            if _component_matches(c, applies) and c.rotation_deg is not None
        ]
        if len(rots) < 2:
            return []  # need ≥2 rotations to compare
        rotations = [r for _, r in rots]
        rng = max(rotations) - min(rotations)
        if passes_or_violates(rng, tol):  # <= tolerance passes (boundary semantics)
            return []
        # Boundary culprits: components pinned at min or max rotation.
        boundary = [
            c for c, r in rots if r == min(rotations) or r == max(rotations)
        ]
        return [
            _violation(
                c,
                self.kind,
                f"component {c.id} rotation {c.rotation_deg:.2f}° at boundary of spread "
                f"{rng:.2f}° (tolerance {tol}°)",
                {
                    "tolerance_deg": tol,
                    "range_deg": rng,
                    "applied_to_count": len(rots),
                    "rotations": {comp.id: r for comp, r in rots},
                },
            )
            for c in boundary
        ]


class GridAlignment(Assertion):
    """§5 ``grid-alignment``.

    All matched component positions must snap to ``grid_mm`` increments.
    """

    kind: ClassVar[str] = "grid-alignment"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        grid_mm = float(params["grid_mm"])  # required
        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if c.position_mm is None:
                continue
            x, y = c.position_mm
            x_off = x - round(x / grid_mm) * grid_mm
            y_off = y - round(y / grid_mm) * grid_mm
            if abs(x_off) > _GRID_EPSILON_MM or abs(y_off) > _GRID_EPSILON_MM:
                violations.append(
                    _violation(
                        c,
                        self.kind,
                        f"component {c.id} at ({x}, {y})mm not on {grid_mm}mm grid",
                        {
                            "component_id": c.id,
                            "position_mm": (x, y),
                            "grid_mm": grid_mm,
                            "x_offset_mm": x_off,
                            "y_offset_mm": y_off,
                        },
                    )
                )
        return violations
