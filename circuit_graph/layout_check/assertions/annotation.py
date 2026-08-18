"""Annotation assertions (design doc §6 标注).

Three rules:

- ``designator-present`` — every matched component must carry a non-empty
  ``Designator`` field.
- ``value-present`` — every matched component must carry a ``Value`` field
  that is neither empty nor in a configurable forbidden list.
- ``signal-name-on-net`` — net names must not match a configurable list of
  forbidden auto-name patterns (e.g. KiCad's ``NetU1_2``).

Boundary semantics (uniform across all 12 assertions; see
:mod:`circuit_graph.layout_check.assertions.base`):

    distance <= tolerance_mm → PASS
    distance >  tolerance_mm → FAIL

The three annotation rules are non-spatial, so the boundary helper is not
called here. Each rule only reads fields off :class:`LayoutComponent` or
:class:`LayoutNet` and emits one :class:`Violation` per breach.
"""
from __future__ import annotations

from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.base import (
    Assertion,
    match_glob,
)
from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutSchematic,
    Location,
    Violation,
)

# Default forbidden values for ``value-present``. Matches the spec table in
# §6 of the design doc. Stored as a tuple so it's hashable / immutable.
_DEFAULT_FORBIDDEN_VALUE: tuple[str, ...] = ("0", "NC", "?", "")

# Default forbidden patterns for ``signal-name-on-net``. ``Net*`` is the
# KiCad 5+ prefix; ``NetU?_?`` / ``NetC?_?`` cover the older two-letter
# form. All are ``fnmatch`` globs.
_DEFAULT_FORBIDDEN_NET_NAMES: tuple[str, ...] = ("Net*", "NetU?_?", "NetC?_?")


# -- applies_to filter helpers -------------------------------------------------

def _component_matches(comp, applies: AppliesTo | None) -> bool:
    """Apply an :class:`AppliesTo` filter to a single component.

    Mirrors the helper in :mod:`circuit_graph.layout_check.assertions.alignment`
    (kept local per the Batch-3 task scope: each batch is independent and may
    not import helpers from sibling batches).

    A ``None`` applies matches everything. Each present axis is OR-ed within
    itself and AND-ed across the four axes. ``refdes_prefix`` globs are
    matched against the *derived* prefix (e.g. ``R96`` → ``R``), per the
    design doc.
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
        if not any(
            match_glob(p, n) for p in applies.net for n in comp_nets
        ):
            return False
    return True


def _net_matches(net, applies: AppliesTo | None) -> bool:
    """Apply an :class:`AppliesTo` filter to a single net.

    Only the ``net`` (name glob) and ``kind`` (literal) axes are relevant
    for net-targeted rules; ``refdes_prefix`` and ``type`` are component
    axes and are ignored.
    """
    if applies is None:
        return True
    if applies.net and not any(match_glob(p, net.name) for p in applies.net):
        return False
    if applies.kind and net.kind not in applies.kind:
        return False
    return True


# -- location / violation helpers --------------------------------------------

def _component_loc(c) -> Location:
    """Build a :class:`Location` for a component violation.

    Geometry fields fall back to ``None`` when the parser did not supply
    them (e.g. on ``.NET`` input). ``component`` is set to ``c.id`` or
    ``None`` when the id is missing/empty (so the violation remains
    identifiable by context, not by ``location.component``).
    """
    return Location(
        component=c.id or None,
        net=None,
        x_mm=c.position_mm[0] if c.position_mm else None,
        y_mm=c.position_mm[1] if c.position_mm else None,
        sheet=c.sheet,
        page=c.page,
    )


def _net_loc(net_name: str) -> Location:
    """Build a :class:`Location` for a net violation (no geometry)."""
    return Location(
        component=None,
        net=net_name,
        x_mm=None,
        y_mm=None,
        sheet=None,
        page=None,
    )


def _violation(
    loc: Location,
    kind: str,
    message: str,
    context: dict[str, Any],
) -> Violation:
    return Violation(
        rule_id="",  # orchestrator (Batch 5) fills this in when packaging
        assert_kind=kind,
        severity="error",
        message=message,
        location=loc,
        context=context,
        doc=f"circuit-graph://rules#{kind}",
    )


# -- concrete assertions -------------------------------------------------------

class DesignatorPresent(Assertion):
    """§6 ``designator-present`` — every matched component must have a non-empty id."""

    kind: ClassVar[str] = "designator-present"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            if not (c.id and c.id.strip()):
                violations.append(
                    _violation(
                        _component_loc(c),
                        self.kind,
                        f"component {c.id!r} has empty designator",
                        {"component_id": c.id},
                    )
                )
        return violations


class ValuePresent(Assertion):
    """§6 ``value-present`` — every matched component must have a value
    that is neither empty nor in the forbidden list."""

    kind: ClassVar[str] = "value-present"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        # Default applied in the assertion when the loader did not pass
        # the param (keeps behaviour deterministic when the rule omits
        # ``params`` entirely).
        forbidden_raw = params.get("forbidden_default")
        forbidden: tuple[str, ...] = (
            tuple(forbidden_raw) if forbidden_raw else _DEFAULT_FORBIDDEN_VALUE
        )
        violations: list[Violation] = []
        for c in schematic.components:
            if not _component_matches(c, applies):
                continue
            value = c.value
            bad = (
                value is None
                or value.strip() == ""
                or value in forbidden
            )
            if not bad:
                continue
            violations.append(
                _violation(
                    _component_loc(c),
                    self.kind,
                    f"component {c.id} has missing or forbidden value {value!r}",
                    {
                        "component_id": c.id,
                        "value": value,
                        "forbidden_default": list(forbidden),
                    },
                )
            )
        return violations


class SignalNameOnNet(Assertion):
    """§6 ``signal-name-on-net`` — net names must not match a forbidden
    auto-name pattern. This rule operates on **nets**, not components.

    Only the ``net`` (name glob) and ``kind`` axes of ``applies_to`` are
    honoured; ``refdes_prefix`` and ``type`` are component axes and are
    ignored. One violation per offending net (the first matching pattern
    is recorded in ``context.matched_pattern``).
    """

    kind: ClassVar[str] = "signal-name-on-net"

    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        forbidden_raw = params.get("forbidden_default_names")
        forbidden: tuple[str, ...] = (
            tuple(forbidden_raw)
            if forbidden_raw
            else _DEFAULT_FORBIDDEN_NET_NAMES
        )
        violations: list[Violation] = []
        for n in schematic.nets:
            if not _net_matches(n, applies):
                continue
            for pat in forbidden:
                if match_glob(pat, n.name):
                    violations.append(
                        _violation(
                            _net_loc(n.name),
                            self.kind,
                            f"net {n.name!r} matches forbidden pattern {pat!r}",
                            {
                                "net_name": n.name,
                                "matched_pattern": pat,
                                "forbidden_patterns": list(forbidden),
                            },
                        )
                    )
                    break  # one violation per net
        return violations
