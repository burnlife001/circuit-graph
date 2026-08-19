"""§3 power-rail placement assertions.

Two spatial rules:

- ``power-at-top`` — every matched component whose pin-net set intersects the
  schematic's power nets (``LayoutNet.kind == "power"``) must sit within
  ``tolerance_mm`` of the **top of the page** (KiCad: ``y >= page_height_mm
  - tolerance_mm``).
- ``power-layer-hierarchy`` — for each unique voltage rail parsed from
  power-net names (e.g. ``5V``, ``3V3``, ``+12V``), the y-band of components
  on that rail must not overlap any other rail's y-band by more than
  ``voltage_tolerance_mm``.

**Page-coordinate convention.** KiCad uses Y-up (origin at bottom-left).
Throughout this module "top of page" means large y, "bottom" means small y.
The default page height is A4 landscape KiCad (``210 mm``); override per
rule with ``page_height_mm``.

**Geometry requirement.** Both rules depend on
:attr:`LayoutComponent.position_mm`. Components whose position is ``None``
are skipped without fabrication — on ``.NET`` (Protel) input this yields
zero violations (vacuous-pass), which is the documented behaviour per
design doc §1.1 / §7.

**Boundary semantics** (uniform across all 12 assertions; see
:mod:`circuit_graph.layout_check.assertions.base`):

    distance <= tolerance_mm → PASS
    distance >  tolerance_mm → FAIL

The helpers in this module (and in :mod:`ground`) are deliberately
duplicated rather than shared so that sibling batches remain independent
and can land in parallel without merge friction.
"""
from __future__ import annotations

import re
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

# Default page height for A4 landscape KiCad schematics. Override with the
# ``page_height_mm`` parameter per rule. ``power-layer-hierarchy`` does NOT
# use this constant — it only compares y-bands between voltage rails.
_DEFAULT_PAGE_HEIGHT_MM: float = 210.0

# Regex used by ``_voltage_of_net_name``. Two capture groups: ``(whole, frac)``
# where ``frac`` is the digit run after V (for compact notation like ``3V3``)
# or ``None`` for plain ``5V`` / ``12V`` / ``5.0V`` notation. Examples:
#   ``5V``      → group 1="5",   group 2=None  → 5.0
#   ``3V3``     → group 1="3",   group 2="3"   → 3.3
#   ``+12V``    → group 1="12",  group 2=None  → 12.0
#   ``VDD_5V0`` → group 1="5",   group 2="0"   → 5.0
#   ``GND``     → no match → None
#   ``VCC``     → no match → None (no digit before V)
_VOLTAGE_RE = re.compile(r"(?i)([0-9]+(?:\.[0-9]+)?)\s*v(?:([0-9]+)|\b)")

# Cap on the per-rail component sample recorded in violation context.
# Keeps the JSON report compact while still being debuggable.
_MAX_OVERLAP_SAMPLES: int = 5


# -- applies_to filter / location / violation helpers ------------------------

def _component_matches(comp, applies: AppliesTo | None) -> bool:
    """Filter a component against an :class:`AppliesTo` block.

    Mirrors the helper in :mod:`circuit_graph.layout_check.assertions.annotation`
    and :mod:`...alignment`; duplicated here so the two batches stay
    independent.
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

def _voltage_of_net_name(name: str) -> float | None:
    """Parse the voltage suffix from a net name. Returns ``None`` if no
    recognisable voltage is present.

    Examples
    --------
    >>> _voltage_of_net_name("5V")
    5.0
    >>> _voltage_of_net_name("3V3")
    3.3
    >>> _voltage_of_net_name("+12V")
    12.0
    >>> _voltage_of_net_name("VDD_5V0")
    5.0
    >>> _voltage_of_net_name("GND") is None
    True
    >>> _voltage_of_net_name("VCC") is None
    True
    """
    m = _VOLTAGE_RE.search(name or "")
    if not m:
        return None
    whole = float(m.group(1))
    frac = m.group(2)
    if not frac:
        return whole
    # Compact notation like "3V3" → whole="3", frac="3" → 3.3.
    # ``int(whole)`` drops any ".0" suffix that ``float`` may have appended.
    return float(f"{int(whole)}.{frac}")


def _net_kind_map(schematic: LayoutSchematic) -> dict[str, str]:
    """Build a ``net_name -> kind`` lookup from the schematic's nets."""
    return {n.name: n.kind for n in schematic.nets}


def _comp_kinds(comp, kinds: dict[str, str]) -> set[str]:
    """Net kinds reachable through any of ``comp``'s pins."""
    return {kinds[p.net] for p in comp.pins if p.net in kinds}


# -- concrete assertions ------------------------------------------------------

class PowerAtTop(Assertion):
    """§3 ``power-at-top``.

    All matched components whose connected net has ``kind == "power"`` must
    sit within ``tolerance_mm`` of the top of the page (large y).

    Parameters
    ----------
    tolerance_mm : float, required
        Vertical slack measured downward from the top edge.
    page_height_mm : float, optional
        Override for the page height. Default ``210.0`` (A4 landscape KiCad).
    """

    kind: ClassVar[str] = "power-at-top"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        tolerance_mm = float(params["tolerance_mm"])  # required
        page_height_mm = float(params.get("page_height_mm", _DEFAULT_PAGE_HEIGHT_MM))
        required_y_min = page_height_mm - tolerance_mm

        kinds = _net_kind_map(schematic)
        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if c.position_mm is None:
                continue  # no fabrication; skip silently
            if "power" not in _comp_kinds(c, kinds):
                continue  # component is irrelevant to this rule
            _x, y = c.position_mm
            # Boundary semantics: y >= required_y_min ↔ (page_height_mm - y) <= tolerance_mm.
            if passes_or_violates(page_height_mm - y, tolerance_mm):
                continue
            violations.append(
                _violation(
                    c,
                    self.kind,
                    f"power component {c.id} at y={y:.2f}mm is below top tolerance "
                    f"{tolerance_mm}mm from page top ({page_height_mm}mm)",
                    {
                        "required_y_min_mm": required_y_min,
                        "actual_y_mm": y,
                        "page_height_mm": page_height_mm,
                        "tolerance_mm": tolerance_mm,
                    },
                )
            )
        return violations


class PowerLayerHierarchy(Assertion):
    """§3 ``power-layer-hierarchy``.

    For each unique voltage rail parsed from a power-net name, the y-band
    of components on that rail must not overlap any other voltage rail's
    y-band by more than ``voltage_tolerance_mm``. ``forbidden_mixing``
    (``True`` by default) controls whether non-overlapping mixing is also
    flagged; MVP only checks overlap, so the param is recorded in the
    context for forward-compatibility and otherwise unused.

    Vacuous when zero or one distinct voltage rail is present.

    Parameters
    ----------
    voltage_tolerance_mm : float, required
        Maximum permitted vertical overlap between two distinct rails'
    forbidden_mixing : bool, optional
        Reserved; recorded in context. Default ``True``.
    y-bands in mm.
    """

    kind: ClassVar[str] = "power-layer-hierarchy"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        voltage_tolerance_mm = float(params["voltage_tolerance_mm"])  # required
        forbidden_mixing = bool(params.get("forbidden_mixing", True))

        kinds = _net_kind_map(schematic)
        # voltage -> list of (component, y) on that rail.
        bands: dict[float, list[tuple[Any, float]]] = {}
        for c in schematic.components:
            if c.position_mm is None:
                continue
            if not _component_matches(c, applies):
                continue
            # Only consider power nets with a parseable voltage.
            for pin in c.pins:
                if pin.net not in kinds:
                    continue
                if kinds[pin.net] != "power":
                    continue
                v = _voltage_of_net_name(pin.net)
                if v is None:
                    continue
                _x, y = c.position_mm
                bands.setdefault(v, []).append((c, y))

        if len(bands) < 2:
            return []  # vacuous: zero or one rail

        violations: list[Violation] = []
        # Sort rails so iteration is deterministic and (i<j) is stable.
        sorted_voltages = sorted(bands.keys())
        for i in range(len(sorted_voltages)):
            for j in range(i + 1, len(sorted_voltages)):
                v_i, v_j = sorted_voltages[i], sorted_voltages[j]
                ys_i = [y for _, y in bands[v_i]]
                ys_j = [y for _, y in bands[v_j]]
                min_i, max_i = min(ys_i), max(ys_i)
                min_j, max_j = min(ys_j), max(ys_j)
                overlap = min(max_i, max_j) - max(min_i, min_j)
                if overlap <= voltage_tolerance_mm:
                    continue  # boundary: overlap == tolerance passes
                # Sample up to _MAX_OVERLAP_SAMPLES components from each rail.
                sample_i = [
                    {"component": c.id, "y_mm": y}
                    for c, y in bands[v_i][:_MAX_OVERLAP_SAMPLES]
                ]
                sample_j = [
                    {"component": c.id, "y_mm": y}
                    for c, y in bands[v_j][:_MAX_OVERLAP_SAMPLES]
                ]
                # Anchor the violation to the first component of the LOWER rail
                # so the JSON ``location`` is meaningful. The component with the
                # highest y in the lower rail (closest to the overlap) is the
                # boundary culprit.
                lower = v_i if v_i < v_j else v_j
                upper = v_j if v_i < v_j else v_i
                # Pick the component at the top of the lower band.
                culprit = max(bands[lower], key=lambda t: t[1])[0]
                violations.append(
                    _violation(
                        culprit,
                        self.kind,
                        f"voltage rails {v_i}V (y∈[{min_i:.2f}, {max_i:.2f}]) "
                        f"and {v_j}V (y∈[{min_j:.2f}, {max_j:.2f}]) overlap by "
                        f"{overlap:.2f}mm (tolerance {voltage_tolerance_mm}mm)",
                        {
                            "voltage_low_v": lower,
                            "voltage_high_v": upper,
                            "band_low_v": [min_i, max_i],
                            "band_high_v": [min_j, max_j],
                            "overlap_mm": overlap,
                            "voltage_tolerance_mm": voltage_tolerance_mm,
                            "forbidden_mixing": forbidden_mixing,
                            "components_low_v": sample_i
                            if v_i == lower
                            else sample_j,
                            "components_high_v": sample_j
                            if v_i == lower
                            else sample_i,
                        },
                    )
                )
        return violations


__all__ = ["PowerAtTop", "PowerLayerHierarchy"]
