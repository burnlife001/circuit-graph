"""Decoupling capacitor proximity assertion (§5 of the cg layout-check spec).

Spatial rule: requires component and pin coordinates. On ``.NET`` (Protel)
input the :attr:`LayoutComponent.position_mm` and :attr:`LayoutPin.x_mm` /
``y_mm`` fields are always ``None``, so the rule emits zero violations
(vacuous-pass). No positions are ever fabricated; candidates whose
geometry is missing are simply skipped.

Boundary semantics (uniform across all 12 assertions; see
:mod:`circuit_graph.layout_check.assertions.base`):

    distance <= max_distance_mm → PASS
    distance >  max_distance_mm → FAIL
"""
from __future__ import annotations

import re
from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.base import (
    Assertion,
    distance_mm,
    match_glob,
    passes_or_violates,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutComponent,
    LayoutPin,
    LayoutSchematic,
    Location,
    Violation,
)

# Capacitance parser. Recognises forms like "100n", "100nF", "0.1u",
# "0.1uF", "10p", "10pF", "1u", ".1u", case-insensitive. Bare numbers
# such as "100" or "1" are rejected — they are not unambiguous capacitor
# markings and would require guessing the unit.
_CAP_VALUE_RE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+)\s*([pPnNuUmMkK]?)\s*[fF]?\s*$"
)
_CAP_UNIT_FACTORS: dict[str, float] = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "": 1e0,   # bare number → reject in _parse_cap_value_farads
}


def _parse_cap_value_farads(s: str | None) -> float | None:
    """Best-effort capacitance parser.

    Returns the value in farads, or ``None`` when unparseable.

    The result is rounded to 12 decimal places to normalise IEEE-754
    noise (e.g. ``100 * 1e-9 == 1.0000000000000001e-7`` while
    ``100e-9 == 1e-7``). 12 dp preserves picofarad precision across the
    practical range (1 pF … 1 F) while collapsing trailing ULP jitter.
    """
    if s is None:
        return None
    m = _CAP_VALUE_RE.match(s)
    if not m:
        return None
    unit = m.group(2).lower()
    if unit not in _CAP_UNIT_FACTORS or unit == "":
        return None
    try:
        return round(float(m.group(1)) * _CAP_UNIT_FACTORS[unit], 12)
    except (ValueError, TypeError):
        return None


def _net_kind_map(schematic: LayoutSchematic) -> dict[str, str]:
    """Build a ``net_name -> kind`` lookup."""
    return {n.name: n.kind for n in schematic.nets}


def _pin_kind(pin: LayoutPin, kinds: dict[str, str]) -> str | None:
    """Net-kind of a pin's connected net, or ``None``."""
    if not pin.net:
        return None
    return kinds.get(pin.net)


def _component_matches(comp: LayoutComponent, applies: AppliesTo | None) -> bool:
    """Filter a component against ``applies_to``.

    Only ``refdes_prefix`` and ``type`` axes are honoured; ``kind`` and
    ``net`` are not meaningful for decoupling candidates — a cap can sit
    on any net, and the "kind" axis refers to net-kind which the rule
    already inspects itself via :func:`_pin_kind`.
    """
    if applies is None:
        return True
    if applies.refdes_prefix and not any(
        match_glob(p, comp.refdes_prefix) for p in applies.refdes_prefix
    ):
        return False
    if applies.type and not any(t == comp.type for t in applies.type):
        return False
    return True


def _loc(c: LayoutComponent) -> Location:
    """Location for a decoupling-cap violation (cap-anchored geometry)."""
    return Location(
        component=c.id,
        net=None,
        x_mm=c.position_mm[0] if c.position_mm else None,
        y_mm=c.position_mm[1] if c.position_mm else None,
        sheet=c.sheet,
        page=c.page,
    )


def _violation(
    cap: LayoutComponent,
    kind: str,
    message: str,
    context: dict[str, Any],
) -> Violation:
    return Violation(
        rule_id="",  # orchestrator (Batch 5) fills this in
        assert_kind=kind,
        severity="error",
        message=message,
        location=_loc(cap),
        context=context,
        doc=f"circuit-graph://rules#{kind}",
    )


class DecoupNearIcPowerPin(Assertion):
    """§5 ``decoup-near-ic-power-pin`` — a decoupling cap must lie within
    ``max_distance_mm`` of any IC's power pin.

    Identification (per spec §5.1 "Type inference"):

    - ``refdes_prefix`` ∈ ``capacitor_refdes_prefix`` (default ``C*``)
    - ``value`` parses to ≤ ``capacitor_value_max_farads`` (default 100 nF)
    - at least one pin on a net with ``kind="power"`` and one on a net
      with ``kind="ground"``
    - ``position_mm`` is present (else skip — vacuous-pass)

    The IC side: any component with ``refdes_prefix`` ∈
    ``ic_refdes_prefix`` (default ``U*``) and at least one power-pin with
    ``x_mm``/``y_mm``. The nearest such pin wins; boundary
    ``distance <= max_distance_mm`` PASSes (uniform semantics).
    """

    kind: ClassVar[str] = "decoup-near-ic-power-pin"
    _DEFAULT_CAP_PREFIXES: tuple[str, ...] = ("C*",)
    _DEFAULT_IC_PREFIXES: tuple[str, ...] = ("U*",)
    _DEFAULT_VALUE_FARADS_MAX: float = 100e-9

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        # Required param. Loader validates params; if missing here we
        # fall back to 0 mm which makes every non-coincident pair a
        # violation (matches the task brief's "MVP" branch).
        max_dist = float(params.get("max_distance_mm", 0))
        cap_prefixes: tuple[str, ...] = tuple(
            params.get("capacitor_refdes_prefix") or self._DEFAULT_CAP_PREFIXES
        )
        ic_prefixes: tuple[str, ...] = tuple(
            params.get("ic_refdes_prefix") or self._DEFAULT_IC_PREFIXES
        )
        value_max = float(
            params.get("capacitor_value_max_farads")
            or self._DEFAULT_VALUE_FARADS_MAX
        )

        kinds = _net_kind_map(schematic)

        # 1. Identify decoupling capacitors.
        caps: list[LayoutComponent] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if not any(match_glob(p, c.refdes_prefix) for p in cap_prefixes):
                continue
            farads = _parse_cap_value_farads(c.value)
            if farads is None or farads > value_max:
                continue
            has_power = any(_pin_kind(p, kinds) == "power" for p in c.pins)
            has_ground = any(_pin_kind(p, kinds) == "ground" for p in c.pins)
            if not (has_power and has_ground):
                continue
            if c.position_mm is None:
                continue
            caps.append(c)

        # 2. Identify IC power-pin positions (across all IC components,
        # regardless of applies_to — the rule has its own IC prefix knob).
        ic_pins: list[tuple[LayoutComponent, LayoutPin]] = []
        for c in schematic.components:
            if not any(match_glob(p, c.refdes_prefix) for p in ic_prefixes):
                continue
            for p in c.pins:
                if (
                    _pin_kind(p, kinds) == "power"
                    and p.x_mm is not None
                    and p.y_mm is not None
                ):
                    ic_pins.append((c, p))

        # 3. Check each cap against nearest IC power pin.
        violations: list[Violation] = []
        for cap in caps:
            if not ic_pins:
                continue  # no IC power pin coords → vacuous-pass
            cap_pos = cap.position_mm
            nearest: tuple[LayoutComponent, LayoutPin] | None = None
            nearest_d: float | None = None
            for ic, ic_pin in ic_pins:
                d = distance_mm(cap_pos, (ic_pin.x_mm, ic_pin.y_mm))
                if nearest_d is None or d < nearest_d:
                    nearest = (ic, ic_pin)
                    nearest_d = d
            if nearest is None or nearest_d is None:
                continue
            if not passes_or_violates(nearest_d, max_dist):
                ic, ic_pin = nearest
                violations.append(
                    _violation(
                        cap,
                        self.kind,
                        (
                            f"decoupling capacitor {cap.id} at {cap_pos}mm is "
                            f"{nearest_d:.2f}mm from IC {ic.id} power pin "
                            f"(limit {max_dist}mm)"
                        ),
                        {
                            "max_distance_mm": max_dist,
                            "nearest_distance_mm": nearest_d,
                            "nearest_ic": ic.id,
                            "nearest_ic_pin_net": ic_pin.net,
                            "cap_count": len(caps),
                            "ic_power_pin_count": len(ic_pins),
                        },
                    )
                )
        return violations
