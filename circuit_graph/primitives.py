"""Topology primitive library loader and bipartite graph encoding.

Primitives are human-editable JSON templates that describe a small circuit
structure (component signatures, net names, connections) that the matcher then
looks for inside a larger CircuitIR.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from circuit_graph.ir import CircuitIR, Component


# Primitive node kinds used in the bipartite encoding.
class _NodeKind:
    COMPONENT = "component"
    NET = "net"


@dataclass(frozen=True)
class PrimitiveSignal:
    """A net that participates in the primitive boundary."""

    id: str
    type: str
    name_pattern: str
    required: bool = True


@dataclass(frozen=True)
class PrimitivePin:
    """A pin on a primitive component and the signal/net it must connect to."""

    number: str
    net: str


@dataclass(frozen=True)
class PrimitiveComponent:
    """A component signature inside a primitive template."""

    id: str
    type: str
    required: bool = True
    value_pattern: str | None = None
    pins: list[PrimitivePin] = field(default_factory=list)


@dataclass(frozen=True)
class PrimitiveConstraints:
    """Structural constraints for a primitive match."""

    min_components: int = 1
    exact_component_count: bool = False


@dataclass(frozen=True)
class PrimitiveScoring:
    """Scoring weights and confidence thresholds for a primitive."""

    weights: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PrimitiveTemplate:
    """In-memory representation of a primitive JSON template."""

    id: str
    name: str
    description: str
    role: str
    version: str
    signals: list[PrimitiveSignal]
    components: list[PrimitiveComponent]
    constraints: PrimitiveConstraints
    scoring: PrimitiveScoring
    raw: dict[str, Any]


class PrimitiveValidationError(Exception):
    """Raised when a primitive template is malformed."""


DEFAULT_PRIMITIVES_DIR = Path(__file__).resolve().parent.parent / "primitives"


def load_primitives(path: Path | str | None = None) -> list[PrimitiveTemplate]:
    """Load all ``*.json`` primitive templates from ``path``.

    The default path is ``primitives/`` next to the project root.  Templates
    are validated syntactically but not semantically against a real circuit.
    """
    base = Path(path) if path else DEFAULT_PRIMITIVES_DIR
    if not base.exists():
        raise FileNotFoundError(f"Primitive library directory not found: {base}")

    templates: list[PrimitiveTemplate] = []
    for file_path in sorted(base.glob("*.json")):
        if file_path.name == "schema.json":
            continue
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        templates.append(_parse_primitive(raw))
    return templates


def _parse_primitive(raw: dict[str, Any]) -> PrimitiveTemplate:
    """Parse a raw primitive dict into a :class:`PrimitiveTemplate`."""
    _require_keys(raw, {"id", "name", "components", "signals"})

    signals = [_parse_signal(s) for s in raw.get("signals", [])]
    components = [_parse_component(c, signals) for c in raw.get("components", [])]
    constraints = _parse_constraints(raw.get("constraints", {}))
    scoring = _parse_scoring(raw.get("scoring", {}))

    return PrimitiveTemplate(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        description=raw.get("description", ""),
        role=raw.get("role", "unknown"),
        version=raw.get("version", "0.1.0"),
        signals=signals,
        components=components,
        constraints=constraints,
        scoring=scoring,
        raw=raw,
    )


def _parse_signal(raw: dict[str, Any]) -> PrimitiveSignal:
    _require_keys(raw, {"id", "type", "name_pattern"})
    return PrimitiveSignal(
        id=raw["id"],
        type=raw["type"],
        name_pattern=raw["name_pattern"],
        required=raw.get("required", True),
    )


def _parse_component(raw: dict[str, Any], signals: list[PrimitiveSignal]) -> PrimitiveComponent:
    _require_keys(raw, {"id", "type", "pins"})
    valid_signals = {s.id for s in signals}
    pins: list[PrimitivePin] = []
    for p in raw.get("pins", []):
        if "net" not in p or "number" not in p:
            raise PrimitiveValidationError(f"Invalid pin definition: {p!r}")
        if p["net"] not in valid_signals:
            raise PrimitiveValidationError(f"Pin references undefined signal '{p['net']}'")
        pins.append(PrimitivePin(number=str(p["number"]), net=p["net"]))
    return PrimitiveComponent(
        id=raw["id"],
        type=raw["type"],
        required=raw.get("required", True),
        value_pattern=raw.get("value_pattern"),
        pins=pins,
    )


def _parse_constraints(raw: dict[str, Any]) -> PrimitiveConstraints:
    return PrimitiveConstraints(
        min_components=raw.get("min_components", 1),
        exact_component_count=raw.get("exact_component_count", False),
    )


def _parse_scoring(raw: dict[str, Any]) -> PrimitiveScoring:
    return PrimitiveScoring(
        weights=raw.get("weights", {}),
        thresholds=raw.get("thresholds", {}),
    )


def _require_keys(raw: dict[str, Any], keys: set[str]) -> None:
    missing = keys - set(raw)
    if missing:
        raise PrimitiveValidationError(f"Missing required keys: {sorted(missing)}")


# ---------------------------------------------------------------------------
# Bipartite encoding
# ---------------------------------------------------------------------------


def circuit_to_bipartite_graph(ir: CircuitIR) -> nx.Graph:
    """Encode ``ir`` as an undirected component-net bipartite graph.

    Nodes are labelled ``c:{component_id}`` and ``n:{net_id}``.  Node attributes
    ``kind`` and ``data`` hold the original objects for downstream matching.
    """
    g = nx.Graph()
    for comp in ir.components:
        g.add_node(f"c:{comp.id}", kind=_NodeKind.COMPONENT, data=comp)
    for net in ir.nets:
        g.add_node(f"n:{net.id}", kind=_NodeKind.NET, data=net)
    for conn in ir.connections:
        comp_id = conn.pin_id.rsplit(".", 1)[0]
        g.add_edge(f"c:{comp_id}", f"n:{conn.net_id}")
    return g


def primitive_to_bipartite_graph(template: PrimitiveTemplate) -> nx.Graph:
    """Encode ``template`` as a component-net bipartite graph.

    Nodes use the same ``c:`` / ``n:`` prefixes as the circuit graph so that
    the VF2 matcher can compare them directly.
    """
    g = nx.Graph()
    for comp in template.components:
        g.add_node(f"c:{comp.id}", kind=_NodeKind.COMPONENT, data=comp)
    for sig in template.signals:
        g.add_node(f"n:{sig.id}", kind=_NodeKind.NET, data=sig)
    for comp in template.components:
        for pin in comp.pins:
            g.add_edge(f"c:{comp.id}", f"n:{pin.net}")
    return g


def signal_name_matches(pattern: str, name: str) -> bool:
    """Check whether ``name`` matches ``pattern`` (case-insensitive regex)."""
    return bool(re.search(pattern, name, re.IGNORECASE))


def component_type_matches(expected: str, comp: Component) -> bool:
    """Check whether ``comp``'s device kind matches ``expected``."""
    return comp.device_kind.value.lower() == expected.lower()


def component_value_matches(pattern: str | None, comp: Component) -> bool:
    """Check whether ``comp``'s raw value matches ``pattern``."""
    if pattern is None:
        return True
    raw = comp.raw_value or ""
    return bool(re.search(pattern, raw, re.IGNORECASE))


__all__ = [
    "PrimitiveTemplate",
    "PrimitiveSignal",
    "PrimitiveComponent",
    "PrimitiveConstraints",
    "PrimitiveScoring",
    "PrimitiveValidationError",
    "load_primitives",
    "circuit_to_bipartite_graph",
    "primitive_to_bipartite_graph",
    "signal_name_matches",
    "component_type_matches",
    "component_value_matches",
]
