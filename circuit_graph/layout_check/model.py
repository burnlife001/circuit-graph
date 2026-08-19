"""Layout-check data model.

This module defines the dataclasses shared by every component of the
``cg layout-check`` rule engine:

- **Side-car schematic model** (``LayoutSchematic``, ``LayoutComponent``,
  ``LayoutPin``, ``LayoutNet``, ``LayoutWire``) — geometry-aware IR used by
  parsers; lives alongside :class:`circuit_graph.ir.CircuitIR` without
  contaminating it.
- **Rule model** (``RuleSpec``, ``AppliesTo``, ``AssertSpec``) — the in-memory
  representation of one entry in a ``.schlayout`` YAML file.
- **Report model** (``Violation``, ``Location``, ``Region``) — emitted by the
  checker orchestrator and serialized into the JSON report.

Note on field naming: the YAML key ``assert`` is mapped to the Python field
``assertion`` because ``assert`` is a reserved keyword and is rejected as a
``@dataclass`` field name. The loader (created in a parallel task) is
responsible for that key translation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class LayoutSchematic:
    components: list[LayoutComponent]
    nets: list[LayoutNet]
    wires: list[LayoutWire]


@dataclass
class LayoutComponent:
    id: str
    type: str                                 # device kind, may be 'unknown'
    value: str | None
    position_mm: tuple[float, float] | None   # KiCad only
    rotation_deg: float | None                # KiCad only
    refdes_prefix: str                        # DERIVED: leading alpha of id
    sheet: str | None
    page: int | None
    pins: list[LayoutPin]


@dataclass
class LayoutPin:
    number: str
    net: str
    x_mm: float | None                        # KiCad only
    y_mm: float | None


@dataclass
class LayoutNet:
    name: str
    kind: Literal["power", "ground", "signal"]
    members: list[str]                        # pin ids like "R96.1"


@dataclass
class LayoutWire:
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]
    net: str | None                           # inferred by traversal or None


@dataclass
class RuleSpec:
    """One rule from .schlayout. YAML key ``assert`` is mapped to ``assertion``
    (Python can't use ``assert`` as a field/kwarg name)."""
    id: str
    enabled: bool
    applies_to: AppliesTo | None              # None = matches everything
    assertion: AssertSpec                     # ← note: 'assertion' not 'assert'


@dataclass
class AppliesTo:
    refdes_prefix: list[str]
    type: list[str]
    net: list[str]
    kind: list[str]


@dataclass
class AssertSpec:
    kind: str
    params: dict[str, Any]


@dataclass
class Violation:
    rule_id: str
    assert_kind: str
    severity: Literal["error"]               # MVP: error only
    message: str
    location: Location
    context: dict[str, Any]
    doc: str                                  # e.g. "circuit-graph://rules#power-at-top"


@dataclass
class Location:
    component: str | None
    net: str | None
    x_mm: float | None
    y_mm: float | None
    sheet: str | None
    page: int | None


@dataclass
class Region:
    """Helper: bounding box used by spatial assertions."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float


# Canonical 12 built-in assertion kinds, in the order they appear in
# docs/superpowers/specs/2026-08-18-cg-layout-check-rule-engine-design.md §5
# (grouped by section). Used by the loader for validation, by every concrete
# assertion's `kind` ClassVar, and by the orchestrator for unknown-kind errors.
#
# DO NOT change the order or names without updating the spec.
BUILT_IN_ASSERTION_KINDS: tuple[str, ...] = (
    # §1 signal flow
    "signal-flow-axis",
    # §3 power/ground
    "power-at-top",
    "gnd-at-bottom",
    "power-layer-hierarchy",
    "single-star-point",
    # §5 alignment + decoupling
    "same-orientation",
    "grid-alignment",
    "decoup-near-ic-power-pin",
    # §6 annotation
    "designator-present",
    "value-present",
    "signal-name-on-net",
    # §4 routing
    "no-trace-through-component",
)
