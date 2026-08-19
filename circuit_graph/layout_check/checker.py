"""LayoutChecker orchestrator — wires loader + parsers + 12 assertions.

This module is the **public surface** that the ``cg layout-check`` CLI (Batch 6)
invokes. It owns three responsibilities and nothing else:

1. Load a ``.schlayout`` rule file via :func:`circuit_graph.layout_check.loader.load_rules`.
2. Parse a schematic (``.NET`` or ``.kicad_sch``) via
   :func:`circuit_graph.layout_check.parsers.parse_schematic`.
3. Run each enabled + applicable rule through the matching
   :class:`~circuit_graph.layout_check.assertions.base.Assertion` and emit a
   JSON-serializable report per design-doc §6.1.

Design-doc contract (2026-08-18-cg-layout-check-rule-engine-design.md):

- §2  Architecture — this file is the central node between CLI / loader /
       parsers / assertions / report.
- §6.1 Report JSON schema — :meth:`LayoutChecker.report` returns the dict
       structure the CLI writes verbatim to stdout.
- §7  Error handling — :class:`~circuit_graph.layout_check.loader.LayoutCheckError`
       bubbles through unchanged; the CLI decides how to package it as an
       exit-2 JSON envelope.
- §8.6 Snapshot for ``--baseline`` / ``--diff-baseline`` —
       :meth:`LayoutChecker.baseline_signature`, :meth:`write_baseline`, and
       :meth:`diff_against_baseline` implement this surface.

Defensive imports for parallel work
-----------------------------------
``flow.py`` (Task 9) and ``routing.py`` (Task 9) are being created by a
parallel subagent in the same Batch 5 window. To keep this orchestrator
testable before that lands, the two modules are loaded with
``try/except ImportError`` and the registry is built from whatever is
importable at import time. Once both files ship, ``ASSERTION_REGISTRY``
contains all 12 built-in kinds; before that, it contains the 10 already-
shipped kinds and the orchestrator degrades gracefully (no-trace-through
auto-skips are simply never counted, the rest of the pipeline is unaffected).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, ClassVar

from circuit_graph.layout_check.assertions.alignment import (
    GridAlignment,
    SameOrientation,
)
from circuit_graph.layout_check.assertions.annotation import (
    DesignatorPresent,
    SignalNameOnNet,
    ValuePresent,
)
from circuit_graph.layout_check.assertions.decoupling import DecoupNearIcPowerPin
from circuit_graph.layout_check.assertions.ground import (
    GndAtBottom,
    SingleStarPoint,
)
from circuit_graph.layout_check.assertions.power import (
    PowerAtTop,
    PowerLayerHierarchy,
)
from circuit_graph.layout_check.loader import load_rules
from circuit_graph.layout_check.model import (
    LayoutSchematic,
    Location,
    RuleSpec,
    Violation,
)
from circuit_graph.layout_check.parsers import parse_schematic

# -- parallel-subagent assertion imports (defensive) ---------------------------
#
# ``flow.py`` and ``routing.py`` are being created in parallel by another
# subagent within the same Batch 5 window. They may not be on disk when this
# module is imported (e.g. when running tests in isolation). The try/except
# blocks below let the orchestrator run with the 10 already-shipped kinds
# without crashing; once both files ship, both branches succeed and the
# registry contains all 12 kinds.

try:
    from circuit_graph.layout_check.assertions.flow import (  # type: ignore[import-not-found]
        SignalFlowAxis,
    )
except ImportError:  # parallel subagent has not yet shipped flow.py
    SignalFlowAxis = None  # type: ignore[assignment,misc]

try:
    from circuit_graph.layout_check.assertions.routing import (  # type: ignore[import-not-found]
        NoTraceThroughComponent,
    )
except ImportError:  # parallel subagent has not yet shipped routing.py
    NoTraceThroughComponent = None  # type: ignore[assignment,misc]


# -- assertion registry -------------------------------------------------------

# The ten already-shipped assertions are imported unconditionally above. The
# two parallel-subagent assertions are appended only if their classes actually
# exist. The registry maps each ``assert.kind`` string to a singleton instance
# (the assertions hold no per-call state; one instance is reused for every
# rule invocation).
_ASSERTION_CLASSES_SHIPPED: tuple[type, ...] = (
    DesignatorPresent,
    ValuePresent,
    SignalNameOnNet,
    SameOrientation,
    GridAlignment,
    DecoupNearIcPowerPin,
    PowerAtTop,
    PowerLayerHierarchy,
    GndAtBottom,
    SingleStarPoint,
)
_ASSERTION_CLASSES_PARALLEL: tuple[type | None, ...] = (
    SignalFlowAxis,
    NoTraceThroughComponent,
)


def _build_registry() -> dict[str, Any]:
    """Construct the assertion registry from whatever classes are importable."""
    registry: dict[str, Any] = {cls.kind: cls() for cls in _ASSERTION_CLASSES_SHIPPED}
    for cls in _ASSERTION_CLASSES_PARALLEL:
        if cls is not None:
            registry[cls.kind] = cls()
    return registry


ASSERTION_REGISTRY: dict[str, Any] = _build_registry()
"""Mapping from assertion kind name to its singleton instance."""


# Rules that auto-skip on .NET (Protel) input — they need geometry or wires.
# ``signal-flow-axis`` is geometry-tolerant (it uses positions to compare
# medians) so it would silently vacuous-pass on .NET; we still count it as
# skipped so the user sees why no signal-flow violations appeared.
_PROTEL_INCOMPATIBLE_KINDS: frozenset[str] = frozenset({
    "no-trace-through-component",
    "signal-flow-axis",
})


# -- orchestrator -------------------------------------------------------------


class LayoutChecker:
    """End-to-end layout-rule orchestrator.

    Parameters
    ----------
    rules_path : Path
        Path to a ``.schlayout`` YAML file (loaded by :func:`load_rules`).
    schematic_path : Path
        Path to a schematic file (``.NET`` or ``.kicad_sch``; dispatched by
        :func:`parse_schematic`).

    Usage
    -----
    End-to-end::

            c = LayoutChecker(rules_path=Path("rules.schlayout"),
                             schematic_path=Path("foo.kicad_sch"))
            violations = c.evaluate()
            report = c.report()   # JSON-serializable dict

    Step-by-step (handy for tests and for caching parses across multiple
    rule files against the same schematic)::

            c = LayoutChecker(rules_path=Path("..."), schematic_path=Path("..."))
            c.load_rules()
            c.parse_schematic()
            violations = c.evaluate()
            json.dumps(c.report())   # write to stdout / file / etc.

    Errors propagate :class:`LayoutCheckError` from the loader and parser
    layers unchanged — the CLI (Batch 6) is responsible for packaging them
    into the §6.2 JSON error envelope.
    """

    SCHEMA_VERSION: ClassVar[str] = "cg-layout-check-v1"
    TOOL_NAME: ClassVar[str] = "cg-layout-check"
    TOOL_VERSION: ClassVar[str] = "0.1.0"

    def __init__(self, rules_path: Path, schematic_path: Path) -> None:
        self.rules_path = Path(rules_path)
        self.schematic_path = Path(schematic_path)
        self.rules: list[RuleSpec] | None = None
        self.schematic: LayoutSchematic | None = None
        self._violations: list[Violation] = []
        self._summary: dict[str, Any] = {}

    # -- step API -----------------------------------------------------------

    def load_rules(self, path: Path | None = None) -> list[RuleSpec]:
        """Load and validate the rules file. Returns the loaded ``RuleSpec[]``.

        Replaces any previously-loaded rules and resets the violation /
        summary state. Raises :class:`LayoutCheckError` on any schema error.
        """
        p = Path(path) if path is not None else self.rules_path
        self.rules = load_rules(p)
        self._reset_run_state()
        return self.rules

    def parse_schematic(self, path: Path | None = None) -> LayoutSchematic:
        """Parse the schematic (KiCad or Protel). Returns the layout model.

        Replaces any previously-parsed schematic and resets the violation /
        summary state. Raises :class:`LayoutCheckError` on any parse error.
        """
        p = Path(path) if path is not None else self.schematic_path
        self.schematic = parse_schematic(p)
        self._reset_run_state()
        return self.schematic

    def _reset_run_state(self) -> None:
        """Wipe ``_violations`` and ``_summary`` between phases / re-runs."""
        self._violations = []
        self._summary = {}

    # -- evaluate ---------------------------------------------------------

    def evaluate(self) -> list[Violation]:
        """Run all enabled, applicable rules. Returns the merged violation list.

        Side effects: populates :attr:`summary` and :attr:`_violations`.

        Implicit auto-load: if either :meth:`load_rules` or
        :meth:`parse_schematic` was not called yet, the missing phase runs
        against the path that was passed to ``__init__``. This is purely
        a convenience for the common "construct + evaluate" path; the
        step-by-step form remains available for tests that want to inspect
        intermediate state.
        """
        if self.rules is None:
            self.load_rules()
        if self.schematic is None:
            self.parse_schematic()
        assert self.rules is not None  # for type-checkers
        assert self.schematic is not None

        # Always reset on every (re-)evaluate — callers may re-invoke after
        # adjusting rules or schematics.
        self._violations = []
        self._summary = {
            "rules_total": len(self.rules),
            "rules_evaluated": 0,
            "rules_skipped_disabled": 0,
            "rules_skipped_unsupported_input": 0,
        }

        start = time.monotonic()
        is_protel = self._is_protel()
        for rule in self.rules:
            if not rule.enabled:
                self._summary["rules_skipped_disabled"] += 1
                continue
            if is_protel and rule.assertion.kind in _PROTEL_INCOMPATIBLE_KINDS:
                self._summary["rules_skipped_unsupported_input"] += 1
                continue
            assertion = ASSERTION_REGISTRY.get(rule.assertion.kind)
            if assertion is None:
                # Should never happen because the loader validates kinds,
                # but defend against registry drift (e.g. parallel-subagent
                # assertion not yet on disk): silently skip rather than
                # crash mid-run. The loader would have raised
                # ``unknown_assert_kind`` for kinds outside
                # BUILT_IN_ASSERTION_KINDS, so reaching this branch
                # implies the kind IS in the canon but the class is not
                # yet importable.
                self._summary["rules_skipped_unsupported_input"] += 1
                continue
            rule_violations = assertion.evaluate(
                self.schematic,
                rule.applies_to,
                rule.assertion.params,
            )
            # Stamp each violation with the producing rule's id.
            for v in rule_violations:
                v.rule_id = rule.id
            self._violations.extend(rule_violations)
            self._summary["rules_evaluated"] += 1

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._summary["violations"] = len(self._violations)
        self._summary["checked_components"] = len(self.schematic.components)
        self._summary["checked_nets"] = len(self.schematic.nets)
        self._summary["duration_ms"] = elapsed_ms
        return list(self._violations)

    @property
    def summary(self) -> dict[str, Any]:
        """Read-only copy of the current run's summary dict.

        Returns a fresh dict each access so callers cannot mutate the
        orchestrator's internal state. Empty until :meth:`evaluate` runs.
        """
        return dict(self._summary)

    @property
    def violations(self) -> list[Violation]:
        """Read-only view of the current violation list."""
        return list(self._violations)

    # -- report -----------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Return a JSON-serializable report dict per spec §6.1.

        Always returns a dict with the four top-level keys ``$schema``,
        ``tool``, ``input``, ``summary``, ``violations`` so the CLI can
        rely on the shape even when :meth:`evaluate` has not been called
        (in which case ``summary`` is empty and ``violations`` is ``[]``).
        """
        rules_sha = ""
        try:
            rules_sha = hashlib.sha256(
                self.rules_path.read_bytes()
            ).hexdigest()
        except OSError:
            # If the rules file vanished between phases we still produce a
            # well-formed report; just omit the sha.
            pass

        return {
            "$schema": self.SCHEMA_VERSION,
            "tool": {"name": self.TOOL_NAME, "version": self.TOOL_VERSION},
            "input": {
                "schematic": str(self.schematic_path),
                "rules_file": str(self.rules_path),
                "rules_file_sha256": rules_sha,
                "schematic_globs": None,
            },
            "summary": self.summary,
            "violations": [_violation_to_dict(v) for v in self._violations],
        }

    # -- baseline support --------------------------------------------------

    def baseline_signature(self, violation: Violation) -> tuple:
        """Stable signature for ``--baseline`` / ``--diff-baseline``.

        Tuple shape: ``(rule_id, component, net, x_mm_round, y_mm_round)``.

        Coordinates are rounded to 1e-3 mm (1 µm) to absorb IEEE-754 noise
        and minor re-export rounding without producing spurious diffs. Any
        coordinate that is ``None`` is preserved as ``None`` so net-only
        violations signature on (component=None, net=...) alone.
        """
        loc = violation.location
        return (
            violation.rule_id,
            loc.component,
            loc.net,
            round(loc.x_mm, 3) if loc.x_mm is not None else None,
            round(loc.y_mm, 3) if loc.y_mm is not None else None,
        )

    def write_baseline(self, path: Path) -> None:
        """Write the sorted list of current violation signatures to ``path``.

        Format: JSON array of tuples. Sorted so byte-equal baselines are
        produced across runs with the same violations in different
        evaluation orders.
        """
        sigs = sorted(self.baseline_signature(v) for v in self._violations)
        path.write_text(
            json.dumps(sigs, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def diff_against_baseline(self, baseline_path: Path) -> list[Violation]:
        """Return violations whose signature is **not** in the baseline.

        Returned in the order they were emitted by :meth:`evaluate` (i.e.
        rule-by-rule, violation-by-violation). Sorted-by-signature output
        is left to the caller.
        """
        baseline_sigs: set[tuple] = {
            tuple(s)
            for s in json.loads(baseline_path.read_text(encoding="utf-8"))
        }
        return [
            v
            for v in self._violations
            if self.baseline_signature(v) not in baseline_sigs
        ]

    # -- internals ---------------------------------------------------------

    def _is_protel(self) -> bool:
        """True iff the schematic is a Protel ``.NET`` (no geometry)."""
        return self.schematic_path.suffix.lower() == ".net"


# -- JSON serialization helpers ----------------------------------------------


def _violation_to_dict(v: Violation) -> dict[str, Any]:
    """JSON-serialize one :class:`Violation` per spec §6.1.

    :class:`Violation` and its nested :class:`Location` are dataclasses, so
    :func:`dataclasses.asdict` produces the right shape. We use it directly
    and fall back to manual construction only if the input somehow stops
    being a dataclass (defensive).
    """
    if is_dataclass(v):
        d = asdict(v)
        # ``asdict`` deep-copies, which guarantees a JSON-clean payload
        # even if the source dataclass contains mutable defaults. No
        # post-processing is required.
        return d
    # Fallback path: should be unreachable given the dataclass definitions
    # in :mod:`circuit_graph.layout_check.model`, but kept for forward
    # compatibility.
    return {
        "rule_id": v.rule_id,
        "assert_kind": v.assert_kind,
        "severity": v.severity,
        "message": v.message,
        "location": _location_to_dict(v.location),
        "context": dict(v.context),
        "doc": v.doc,
    }


def _location_to_dict(loc: Location) -> dict[str, Any]:
    return {
        "component": loc.component,
        "net": loc.net,
        "x_mm": loc.x_mm,
        "y_mm": loc.y_mm,
        "sheet": loc.sheet,
        "page": loc.page,
    }


__all__ = [
    "LayoutChecker",
    "ASSERTION_REGISTRY",
    "_PROTEL_INCOMPATIBLE_KINDS",
    "_violation_to_dict",
    "_location_to_dict",
]
