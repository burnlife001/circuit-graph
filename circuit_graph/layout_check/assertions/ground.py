"""§3 ground placement assertions.

Two spatial rules:

- ``gnd-at-bottom`` — every matched component whose pin-net set intersects
  the schematic's ground nets (``LayoutNet.kind == "ground"``) must sit
  within ``tolerance_mm`` of the **bottom of the page** (KiCad: ``y <=
  tolerance_mm``).
- ``single-star-point`` — analog (AGND) and digital (DGND) ground nets
  must converge to a single physical point; every component on a matched
  ground net must lie within ``max_merge_distance_mm`` of the centroid
  of all such components.

**Page-coordinate convention.** KiCad uses Y-up (origin at bottom-left).
Throughout this module "bottom of page" means small y (close to 0),
"top" means large y. The default page height is A4 landscape KiCad
(``210 mm``); override per rule with ``page_height_mm``.

**Geometry requirement.** Both rules depend on
:attr:`LayoutComponent.position_mm`. Components whose position is ``None``
are skipped without fabrication — on ``.NET`` (Protel) input this yields
zero violations (vacuous-pass), which is the documented behaviour per
design doc §1.1 / §7.

**Boundary semantics** (uniform across all 12 assertions; see
:mod:`circuit_graph.layout_check.assertions.base`):

    distance <= tolerance_mm → PASS
    distance >  tolerance_mm → FAIL

The helpers here mirror those in :mod:`power`; they are deliberately
duplicated rather than shared so the two parallel-task batches can land
without merge friction.
"""
from __future__ import annotations

from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.base import (
    Assertion,
    distance_mm,
    match_glob,
    passes_or_violates,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutSchematic,
    Location,
    Violation,
)

# Default page height for A4 landscape KiCad schematics. Override with the
# ``page_height_mm`` parameter per rule. ``single-star-point`` does NOT use
# this constant — it is a 2-D Euclidean rule in component (x, y) space.
_DEFAULT_PAGE_HEIGHT_MM: float = 210.0

# Default glob patterns for ``single-star-point``. AGND/DGND and their
# suffixes are the canonical analog/digital ground pairings. Users can
# override via the ``gnd_net_patterns`` param.
_DEFAULT_GND_NET_PATTERNS: tuple[str, ...] = ("AGND*", "DGND*", "*AGND", "*DGND")


# -- applies_to filter / location / violation helpers ------------------------

def _component_matches(comp, applies: AppliesTo | None) -> bool:
    """Filter a component against an :class:`AppliesTo` block.

    Mirrors the helper in :mod:`circuit_graph.layout_check.assertions.power`
    (kept local per the Batch-4 task scope: each batch is independent and
    may not import helpers from sibling batches).
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


# -- net helpers --------------------------------------------------------------

def _net_kind_map(schematic: LayoutSchematic) -> dict[str, str]:
    return {n.name: n.kind for n in schematic.nets}


def _comp_kinds(comp, kinds: dict[str, str]) -> set[str]:
    """Net kinds reachable through any of ``comp``'s pins."""
    return {kinds[p.net] for p in comp.pins if p.net in kinds}


def _matched_ground_nets(
    schematic: LayoutSchematic,
    patterns: tuple[str, ...],
) -> list[str]:
    """Return net names whose name matches any of ``patterns`` AND whose
    kind is ``ground``. Patterns are ``fnmatch`` globs.
    """
    out: list[str] = []
    for n in schematic.nets:
        if n.kind != "ground":
            continue
        if any(match_glob(p, n.name) for p in patterns):
            out.append(n.name)
    return out


def _components_on_nets(
    schematic: LayoutSchematic,
    net_names: set[str],
) -> list[tuple[Any, tuple[float, float]]]:
    """Return ``(component, (x, y))`` for every component with at least one
    pin on a name in ``net_names`` AND a known ``position_mm``.
    """
    out: list[tuple[Any, tuple[float, float]]] = []
    for c in schematic.components:
        if c.position_mm is None:
            continue
        if not any(p.net in net_names for p in c.pins):
            continue
        out.append((c, c.position_mm))
    return out


# -- concrete assertions ------------------------------------------------------

class GndAtBottom(Assertion):
    """§3 ``gnd-at-bottom``.

    All matched components whose connected net has ``kind == "ground"``
    must sit within ``tolerance_mm`` of the bottom of the page (small y).

    Parameters
    ----------
    tolerance_mm : float, required
        Vertical slack measured upward from the bottom edge.
    page_height_mm : float, optional
        Override for the page height. Default ``210.0`` (A4 landscape KiCad).
    """

    kind: ClassVar[str] = "gnd-at-bottom"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        tolerance_mm = float(params["tolerance_mm"])  # required
        page_height_mm = float(params.get("page_height_mm", _DEFAULT_PAGE_HEIGHT_MM))

        kinds = _net_kind_map(schematic)
        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if c.position_mm is None:
                continue
            if "ground" not in _comp_kinds(c, kinds):
                continue
            _x, y = c.position_mm
            # Boundary semantics: y <= tolerance_mm ↔ passes_or_violates(y, tolerance_mm).
            if passes_or_violates(y, tolerance_mm):
                continue
            violations.append(
                _violation(
                    c,
                    self.kind,
                    f"ground component {c.id} at y={y:.2f}mm is above bottom tolerance "
                    f"{tolerance_mm}mm from page bottom",
                    {
                        "required_y_max_mm": tolerance_mm,
                        "actual_y_mm": y,
                        "page_height_mm": page_height_mm,
                        "tolerance_mm": tolerance_mm,
                    },
                )
            )
        return violations


class SingleStarPoint(Assertion):
    """§3 ``single-star-point``.

    All matched analog/digital ground nets must converge to one physical
    point within ``max_merge_distance_mm``. The "star point" is the
    centroid (mean x, mean y) of every component on a matched ground net
    that has a known ``position_mm``; every participating component must
    lie within ``max_merge_distance_mm`` of the centroid.

    Boundary semantics: ``distance == max_merge_distance_mm`` passes.

    Vacuous when zero matched AGND/DGND nets are present, or when no
    component on a matched net has a known position.

    Parameters
    ----------
    max_merge_distance_mm : float, required
        Maximum permitted distance from the centroid.
    gnd_net_patterns : list[str], optional
        Override for the AGND/DGND name globs. Default
        ``["AGND*", "DGND*", "*AGND", "*DGND"]``.
    """

    kind: ClassVar[str] = "single-star-point"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        max_merge_distance_mm = float(params["max_merge_distance_mm"])  # required
        patterns_raw = params.get("gnd_net_patterns")
        patterns: tuple[str, ...] = (
            tuple(patterns_raw) if patterns_raw else _DEFAULT_GND_NET_PATTERNS
        )

        matched_nets = _matched_ground_nets(schematic, patterns)
        if not matched_nets:
            return []  # vacuous: no AGND/DGND nets
        participating = _components_on_nets(schematic, set(matched_nets))
        # Apply the applies filter (refdes_prefix/type/kind/net).
        participating = [
            (c, pos) for c, pos in participating if _component_matches(c, applies)
        ]
        if len(participating) < 2:
            return []  # vacuous: <2 components can't form a non-trivial centroid

        cx = sum(pos[0] for _, pos in participating) / len(participating)
        cy = sum(pos[1] for _, pos in participating) / len(participating)
        centroid = (cx, cy)

        far: list[dict[str, Any]] = []
        for c, pos in participating:
            d = distance_mm(pos, centroid)
            if d > max_merge_distance_mm:
                far.append(
                    {
                        "component": c.id,
                        "position": [pos[0], pos[1]],
                        "distance_mm": d,
                    }
                )
        if not far:
            return []

        # Anchor the violation to the worst-offender component so the
        # JSON ``location`` is meaningful.
        worst = max(far, key=lambda v: v["distance_mm"])
        # Recover the offending component for the Location.
        worst_comp = next(c for c, _pos in participating if c.id == worst["component"])
        return [
            _violation(
                worst_comp,
                self.kind,
                f"star-point violation: {len(far)} component(s) more than "
                f"{max_merge_distance_mm}mm from centroid ({cx:.2f}, {cy:.2f})mm; "
                f"worst: {worst['component']} at {worst['distance_mm']:.2f}mm",
                {
                    "centroid_mm": [cx, cy],
                    "max_merge_distance_mm": max_merge_distance_mm,
                    "gnd_net_patterns": list(patterns),
                    "matched_ground_nets": matched_nets,
                    "participating_count": len(participating),
                    "violations": far,
                },
            )
        ]


__all__ = ["GndAtBottom", "SingleStarPoint"]
