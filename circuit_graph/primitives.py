"""Topology primitive library loader and bipartite graph encoding.

Primitives are human-editable JSON templates that describe a small circuit
structure (component signatures, net names, connections) that the matcher then
looks for inside a larger CircuitIR.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import networkx as nx

from circuit_graph.ir import CircuitIR, Component


# Primitive node kinds used in the bipartite encoding.
class _NodeKind:
    COMPONENT = "component"
    NET = "net"


# Canonical edge_semantic enums (used by signal_semantic_matches / schema validation).
_VALID_ROLES = frozenset({"power", "gnd", "analog", "digital", "clock"})
_VALID_DIRECTIONS = frozenset({"source", "load", "bidirectional"})


_LEVEL_FRAC_RE = re.compile(r"^\+?(\d+)[Vv](\d+)$")
_LEVEL_PLAIN_RE = re.compile(r"^\+?(\d+(?:\.\d+)?)[Vv]?$")


def extract_volts(s: str | None) -> Decimal | None:
    """Parse a voltage label to a Decimal value in volts.

    Rules (first match wins):
      1. ``"<int>V<frac>"`` → ``int.frac``   (e.g. "3V3" → 3.3, "1V25" → 1.25)
      2. ``"[+]<float>[V]"`` → ``float``     (e.g. "5V" → 5.0, "3.3V" → 3.3, "5" → 5.0)
      3. otherwise → ``None``
    """
    if not s:
        return None
    s = s.strip()
    m = _LEVEL_FRAC_RE.match(s)
    if m:
        return Decimal(f"{m.group(1)}.{m.group(2)}")
    m = _LEVEL_PLAIN_RE.match(s)
    if m:
        return Decimal(m.group(1))
    return None


def signal_semantic_matches(
    expected_role: str | tuple[str, ...] | None,
    expected_direction: str | None,
    expected_level: str | None,
    edge_semantic: dict[str, Any],
) -> bool:
    """Check one edge_semantic dict against the primitive's expected_* constraints.

    ``None`` expected fields are unconstrained and skipped. ``expected_role`` may
    be a single role string or a tuple of accepted roles; the circuit edge matches
    when its role is a member of the set (or equals the single string). Level
    comparison is decimal-based via :func:`extract_volts`; if either side fails
    to parse, the pair is treated as a mismatch (strict mode).
    """
    if expected_role is not None:
        accepted = (expected_role,) if isinstance(expected_role, str) else expected_role
        if edge_semantic.get("role") not in accepted:
            return False
    if expected_direction is not None and edge_semantic.get("direction") != expected_direction:
        return False
    if expected_level is not None:
        expected_v = extract_volts(expected_level)
        actual_v = extract_volts(edge_semantic.get("level"))
        if expected_v is None or actual_v is None:
            return False
        if expected_v != actual_v:
            return False
    return True


@dataclass(frozen=True)
class PrimitiveSignal:
    """A net that participates in the primitive boundary."""

    id: str
    type: str
    name_pattern: str
    required: bool = True
    expected_role: str | tuple[str, ...] | None = None
    expected_direction: str | None = None
    expected_level: str | None = None


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
    _signal_to_components: dict[str, list[str]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Populate ``_signal_to_components`` from components[*].pins[*].net.

        The dataclass is frozen, so we use ``object.__setattr__`` to set the
        derived index. The mapping is sorted to guarantee determinism across
        parser runs.
        """
        edges_by_net: dict[str, set[str]] = defaultdict(set)
        for comp in self.components:
            for pin in comp.pins:
                edges_by_net[pin.net].add(comp.id)
        derived = {
            sig.id: sorted(edges_by_net.get(sig.id, set()))
            for sig in self.signals
        }
        object.__setattr__(self, "_signal_to_components", derived)


class PrimitiveValidationError(Exception):
    """Raised when a primitive template is malformed."""


DEFAULT_PRIMITIVES_DIR = Path(__file__).resolve().parent.parent / "primitives"


def load_primitives(path: Path | str | None = None) -> list[PrimitiveTemplate]:
    """Load all ``*.json`` primitive templates from ``path`` (recursive).

    The default path is ``primitives/`` next to the project root.  Templates
    are validated syntactically but not semantically against a real circuit.
    Sub-directories under ``primitives/`` are treated as organisational
    categories (amplifier / filter / power / ...); the loader walks them via
    ``rglob`` so new categories need no code change.
    """
    base = Path(path) if path else DEFAULT_PRIMITIVES_DIR
    if not base.exists():
        raise FileNotFoundError(f"Primitive library directory not found: {base}")

    templates: list[PrimitiveTemplate] = []
    for file_path in sorted(base.rglob("*.json")):
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
    expected_role = _parse_role(raw.get("expected_role"))
    expected_direction = raw.get("expected_direction")
    expected_level = raw.get("expected_level")
    if expected_direction is not None and expected_direction not in _VALID_DIRECTIONS:
        raise PrimitiveValidationError(
            f"Invalid expected_direction {expected_direction!r}; "
            f"must be one of {sorted(_VALID_DIRECTIONS)}"
        )
    return PrimitiveSignal(
        id=raw["id"],
        type=raw["type"],
        name_pattern=raw["name_pattern"],
        required=raw.get("required", True),
        expected_role=expected_role,
        expected_direction=expected_direction,
        expected_level=expected_level,
    )


def _parse_role(value: Any) -> str | tuple[str, ...] | None:
    """Normalize ``expected_role`` from JSON: str -> str, list -> tuple."""
    if value is None:
        return None
    if isinstance(value, str):
        if value not in _VALID_ROLES:
            raise PrimitiveValidationError(
                f"Invalid expected_role {value!r}; must be one of {sorted(_VALID_ROLES)}"
            )
        return value
    if isinstance(value, list):
        if not value:
            raise PrimitiveValidationError(
                "expected_role list must contain at least one role"
            )
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in _VALID_ROLES:
                raise PrimitiveValidationError(
                    f"Invalid expected_role entry {item!r}; "
                    f"must be one of {sorted(_VALID_ROLES)}"
                )
            normalized.append(item)
        return tuple(normalized)
    raise PrimitiveValidationError(
        f"expected_role must be a string or list of strings, got {type(value).__name__}"
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


_DEFAULT_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    # ----- Power rails -----
    "vcc": (
        r".*VDD.*", r".*VCC.*", r"^V[+A-Z0-9]+$", r".*V$",
        r"^[+]?\d+(\.\d+)?V(_.*)?$", r"^USB_5V$",
        r"^PVCC$", r"^AVCC$", r"^AVDD$",
    ),
    "gnd": (
        r"^GND\d*$", r"^PGND$", r"^AGND$", r"^DGND$",
        # NOTE: CGND is intentionally NOT in the 'gnd' fallback.
        # CGND is a signal/chassis ground, structurally distinct from
        # the circuit-reference ground. Letting CGND match 'gnd' caused
        # R96 (GND<->CGND bridge) to be falsely identified as the shunt
        # resistor of rc-high-pass.json. A primitive that genuinely needs
        # CGND-as-ground should declare a dedicated signal id (e.g. `cgnd`).
        # The broader `.*GND$` fallback was removed because it still
        # matched `CGND` (and any other name ending in 'GND'), re-introducing
        # the same false positive. If a future schematic uses a name like
        # `MYGND` that we need to recognise, add it explicitly here.
        r".*VSS$", r".*VEE$", r".*_GND$", r"^GND_[A-Z0-9]+$",
    ),
    "vcc_low":  (r"^Net.*$", r".*VDD.*LOW.*", r".*_LOW$"),
    "vcc_high": (r"^Net.*$", r".*VDD.*HIGH.*", r".*_HIGH$"),

    # ----- Inputs (semantic + Protel auto-named) -----
    "in_p":      (r"^Net.*_1$", r"^AINP.*$", r"^INP.*$", r"^V\+IN.*$", r"^VIP$", r"^POS$"),
    "in_n":      (r"^Net.*_1$", r"^AINN.*$", r"^INN.*$", r"^V\-IN.*$", r"^VIN$", r"^NEG$"),
    "vin":       (r"^Net.*_1$", r"^VIN.*$", r"^V[Ii]n.*$", r"^V[_]?[Ii]n.*$"),

    # ----- Outputs -----
    "out":       (r"^Net.*_1$", r"^V?OUT.*$", r"^VO$", r"^AMP_OUT.*$",
                  r"^OA_OUT.*$", r"^OP_OUT.*$", r"^VOUT.*$", r"^AOUT.*$"),
    "vout":      (r"^Net.*_1$", r"^V?OUT.*$", r"^VOUT.*$", r"^AOUT.*$"),

    # ----- Generic signals -----
    "signal":    (r"^Net.*$", r"^[A-Z]+_\d+V$", r"^SIG.*$", r"^A(IO|IN|OUT)\d+$"),
    "sig_in":    (r"^Net.*$", r"^SIG.*$", r"^VIN.*$", r"^AIN.*$"),
    "sig_p":     (r"^Net.*$", r"^SIG.*P$", r"^.+_P$"),
    "sig_n":     (r"^Net.*$", r"^SIG.*N$", r"^.+_N$"),
    "sig_low":   (r"^Net.*$", r".*_LOW$"),
    "sig_high":  (r"^Net.*$", r".*_HIGH$"),
    "load_net":  (r"^Net.*$", r"^LOAD.*$", r"^.+_LOAD$"),

    # ----- Differential / specific -----
    "ac1":       (r"^Net.*_1$", r"^AC\d?$", r"^A$"),
    "ac2":       (r"^Net.*_2$", r"^AC\d?$", r"^B$"),
    "dc_plus":   (r"^Net.*$", r"^D\+$", r"^DC\+$", r"^V\+IN$"),
    "dc_minus":  (r"^Net.*$", r"^D\-$", r"^DC\-$", r"^V\-IN$"),
    "xtal_p":    (r"^Net.*$", r"^XT.*P$", r"^XI$", r"^XIN$"),
    "xtal_n":    (r"^Net.*$", r"^XT.*N$", r"^XO$", r"^XOUT$"),

    # ----- Switches / drivers -----
    "ctrl":      (r"^Net.*$", r"^CTRL.*$", r".*_CTRL$", r"^GATE\d?$"),
    "high_side": (r"^Net.*$", r".*HS$", r".*_HIGH$"),
    "low_side":  (r"^Net.*$", r".*LS$", r".*_LOW$"),
    "pwr_in":    (r"^Net.*$", r"^PWR.*IN.*$", r".+_IN$"),
    "pwr_out":   (r"^Net.*$", r"^PWR.*OUT.*$", r".+_OUT$"),

    # ----- Pull-ups / CC / protection -----
    "pull_up":   (r"^Net.*$", r".*PULLUP.*", r".*PU$"),
    "cc":        (r"^CC[12]?$", r".*CC\d?$"),
    "protected": (r"^Net.*$", r"^PROT.*$"),
}


def signal_name_matches(
    pattern: str,
    name: str,
    *,
    signal_id: str | None = None,
) -> bool:
    """Check whether ``name`` matches ``pattern`` (case-insensitive regex).

    With the keyword-only ``signal_id``, a built-in default fallback table
    is consulted *after* the user pattern misses — this lets the matcher
    match Protel auto-named nets (``NetR97_1``, ``USB_5V`` ...) without
    editing any primitive template. The two-arg form is unchanged and
    triggers no fallback, so all pre-existing tests stay green.
    """
    if re.search(pattern, name, re.IGNORECASE):
        return True
    if signal_id is None:
        return False
    for fallback in _DEFAULT_SIGNAL_PATTERNS.get(signal_id, ()):
        if re.search(fallback, name, re.IGNORECASE):
            return True
    return False


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
    "extract_volts",
    "signal_semantic_matches",
]
