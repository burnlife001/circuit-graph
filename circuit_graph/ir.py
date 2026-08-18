"""Core IR data model for circuit-graph.

This module mirrors the TypeScript IR types while using Python dataclasses
and enums.  The IR is the single source of truth for netlist-derived circuit
structure; higher-level views (NetworkX graph, CircuitOps LPG) are derived
from it without mutation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, EnumMeta
from typing import Any, Literal


class StrEnumMeta(EnumMeta):
    """Metaclass that lets json.JSONEncoder treat enum members as strings."""

    def __str__(cls) -> str:  # noqa: N805
        return cls.value


class StrEnum(str, Enum, metaclass=StrEnumMeta):
    """Enum whose members compare/serialize as strings."""

    def __str__(self) -> str:
        return self.value


class BlockRole(StrEnum):
    MCU = "mcu"
    POWER = "power"
    PD_CONTROLLER = "pd_controller"
    CHARGER = "charger"
    REGULATOR = "regulator"
    CONNECTOR = "connector"
    MEMORY = "memory"
    SENSOR = "sensor"
    UNKNOWN = "unknown"


class NodeKind(StrEnum):
    COMPONENT = "component"
    NET = "net"
    PIN = "pin"
    FUNCTIONAL_BLOCK = "functional_block"
    SHEET = "sheet"


class EdgeKind(StrEnum):
    HAS_PIN = "has_pin"
    CONNECTS_TO = "connects_to"
    BELONGS_TO = "belongs_to"
    POWERED_BY = "powered_by"
    COMMUNICATES_WITH = "communicates_with"


class DeviceKind(StrEnum):
    RESISTOR = "Resistor"
    CAPACITOR = "Capacitor"
    INDUCTOR = "Inductor"
    IC = "IC"
    MOSFET = "Mosfet"
    DIODE = "Diode"
    BRIDGE_RECTIFIER = "BridgeRectifier"
    CONNECTOR = "Connector"
    UNKNOWN = "Unknown"


class NetType(StrEnum):
    SIGNAL = "signal"
    GND = "gnd"
    POWER = "power"
    CLOCK = "clock"
    ANALOG = "analog"
    UNKNOWN = "unknown"


class Provenance(StrEnum):
    NETLIST = "netlist"
    HEURISTIC = "heuristic"
    MANUAL = "manual"
    ENRICHED = "enriched"


@dataclass(frozen=True)
class ResistanceValue:
    kind: Literal["resistance"] = "resistance"
    magnitude: float = 0.0
    unit: Literal["Ω", "kΩ", "MΩ"] = "Ω"
    tolerance: float | None = None


@dataclass(frozen=True)
class CapacitanceValue:
    kind: Literal["capacitance"] = "capacitance"
    magnitude: float = 0.0
    unit: Literal["pF", "nF", "µF"] = "µF"
    tolerance: float | None = None
    dielectric: str | None = None
    voltage_rating: float | None = None


@dataclass(frozen=True)
class InductanceValue:
    kind: Literal["inductance"] = "inductance"
    magnitude: float = 0.0
    unit: Literal["nH", "µH", "mH"] = "µH"
    tolerance: float | None = None


@dataclass(frozen=True)
class VoltageValue:
    kind: Literal["voltage"] = "voltage"
    magnitude: float = 0.0
    unit: Literal["V", "mV"] = "V"


@dataclass(frozen=True)
class CurrentValue:
    kind: Literal["current"] = "current"
    magnitude: float = 0.0
    unit: Literal["A", "mA"] = "A"


@dataclass(frozen=True)
class FrequencyValue:
    kind: Literal["frequency"] = "frequency"
    magnitude: float = 0.0
    unit: Literal["Hz", "kHz", "MHz"] = "Hz"


@dataclass(frozen=True)
class UnknownValue:
    kind: Literal["unknown"] = "unknown"
    raw: str = ""


NormalizedValue = (
    ResistanceValue
    | CapacitanceValue
    | InductanceValue
    | VoltageValue
    | CurrentValue
    | FrequencyValue
    | UnknownValue
)


@dataclass(frozen=True)
class Pin:
    id: str
    number: str
    name: str | None = None
    type: Literal["input", "output", "power", "gnd", "passive", "unknown"] | None = None
    net_id: str | None = None

    def __post_init__(self) -> None:
        comp_id, pin_num = parse_pin_id(self.id)
        if comp_id is None or pin_num is None:
            raise ValueError(f"Invalid pin id: {self.id!r}")


@dataclass(frozen=True)
class Component:
    id: str
    device_kind: DeviceKind
    footprint: str | None = None
    value: NormalizedValue | None = None
    raw_value: str | None = None
    manufacturer: str | None = None
    part_number: str | None = None
    pins: list[Pin] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Net:
    id: str
    name: str
    type: NetType | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Connection:
    pin_id: str
    net_id: str
    provenance: Provenance = Provenance.NETLIST
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FunctionalBlock:
    id: str
    name: str
    role: BlockRole
    component_ids: list[str] = field(default_factory=list)
    description: str | None = None
    source: Literal["manual", "rule", "imported"] = "rule"


@dataclass(frozen=True)
class SemanticEdge:
    source: str
    target: str
    kind: Literal["powered_by", "communicates_with"]
    provenance: Provenance = Provenance.ENRICHED
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeSemantic:
    """Per-pin-to-net edge semantics attached to GraphEdge.metadata['edge_semantic'].

    Populated by the EdgeSemanticEnricher; lets traversal predicates filter
    by direction (source/load/bidirectional) and role (power/gnd/analog/digital/clock)
    without re-deriving them on every query.

    direction: signal flow from the component's perspective
      - source: pin drives the net (op-amp OUT, regulator VOUT)
      - load: pin sinks from the net (op-amp IN+, MCU GPIO input)
      - bidirectional: current flows either way (R/C/L passives, power rails)

    role: functional role of the net at this edge
      - power / gnd / analog / digital / clock

    level: nominal voltage for power/gnd rails (e.g. "5V", "3V3", "1V8");
           None for signal/clock/role-less edges.
    """

    direction: Literal["source", "load", "bidirectional"]
    role: Literal["power", "gnd", "analog", "digital", "clock"]
    level: str | None = None


@dataclass(frozen=True)
class CircuitMeta:
    source: str
    format: str
    parsed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sheet_count: int | None = None
    generator: str | None = None


@dataclass(frozen=True)
class CircuitIR:
    meta: CircuitMeta
    components: list[Component] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    blocks: list[FunctionalBlock] = field(default_factory=list)
    semantic_edges: list[SemanticEdge] = field(default_factory=list)
    version: Literal["0.1.0"] = "0.1.0"


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: NodeKind
    data: Any


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source: str
    target: str
    kind: EdgeKind
    provenance: Provenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraversalOptions:
    direction: Literal["outgoing", "incoming", "both"] = "both"
    edge_kinds: list[EdgeKind] | None = None
    node_kinds: list[NodeKind] | None = None
    net_types: list[NetType] | None = None
    device_kinds: list[DeviceKind] | None = None
    max_depth: int | None = None
    limit: int | None = None
    include_start: bool = True


@dataclass
class Subgraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)


# Pin ID helpers


def make_pin_id(component_id: str, pin_number: str | int) -> str:
    """Return the canonical pin id: ``{componentId}.{pinNumber}``."""
    return f"{component_id}.{pin_number}"


def parse_pin_id(pin_id: str) -> tuple[str, str] | tuple[None, None]:
    """Split a canonical pin id into (component_id, pin_number).

    Returns ``(None, None)`` when the id does not follow the convention.
    """
    if "." not in pin_id:
        return None, None
    component_id, _, pin_number = pin_id.rpartition(".")
    if not component_id or not pin_number:
        return None, None
    return component_id, pin_number


# JSON serialization helpers


class _IREncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def ir_to_dict(ir: CircuitIR) -> dict[str, Any]:
    """Serialize a CircuitIR to a plain dictionary compatible with JSON."""
    return asdict(ir)


def ir_to_json(ir: CircuitIR, indent: int | None = 2) -> str:
    """Serialize a CircuitIR to a JSON string."""
    return json.dumps(ir_to_dict(ir), cls=_IREncoder, indent=indent, ensure_ascii=False)


def _reconstruct_normalized(value: dict[str, Any]) -> NormalizedValue | None:
    kind = value.get("kind")
    if kind == "resistance":
        return ResistanceValue(**value)
    if kind == "capacitance":
        return CapacitanceValue(**value)
    if kind == "inductance":
        return InductanceValue(**value)
    if kind == "voltage":
        return VoltageValue(**value)
    if kind == "current":
        return CurrentValue(**value)
    if kind == "frequency":
        return FrequencyValue(**value)
    if kind == "unknown":
        return UnknownValue(**value)
    return None


def _reconstruct_component(data: dict[str, Any]) -> Component:
    raw_value = data.get("value")
    value = _reconstruct_normalized(raw_value) if raw_value else None
    return Component(
        id=data["id"],
        device_kind=DeviceKind(data["device_kind"]),
        footprint=data.get("footprint"),
        value=value,
        raw_value=data.get("raw_value"),
        manufacturer=data.get("manufacturer"),
        part_number=data.get("part_number"),
        pins=[Pin(**p) for p in data.get("pins", [])],
        metadata=data.get("metadata", {}),
    )


def ir_from_dict(data: dict[str, Any]) -> CircuitIR:
    """Deserialize a plain dictionary into a CircuitIR instance."""
    meta = CircuitMeta(**data["meta"])
    components = [_reconstruct_component(c) for c in data.get("components", [])]
    nets = [
        Net(
            id=n["id"],
            name=n["name"],
            type=NetType(n["type"]) if n.get("type") else None,
            metadata=n.get("metadata", {}),
        )
        for n in data.get("nets", [])
    ]
    connections = [
        Connection(
            pin_id=c["pin_id"],
            net_id=c["net_id"],
            provenance=Provenance(c.get("provenance", "netlist")),
            metadata=c.get("metadata", {}),
        )
        for c in data.get("connections", [])
    ]
    blocks = [
        FunctionalBlock(
            id=b["id"],
            name=b["name"],
            role=BlockRole(b["role"]),
            component_ids=b.get("component_ids", []),
            description=b.get("description"),
            source=b.get("source", "rule"),
        )
        for b in data.get("blocks", [])
    ]
    semantic_edges = [
        SemanticEdge(
            source=se["source"],
            target=se["target"],
            kind=se["kind"],
            provenance=Provenance(se.get("provenance", "enriched")),
            metadata=se.get("metadata", {}),
        )
        for se in data.get("semantic_edges", [])
    ]
    return CircuitIR(
        meta=meta,
        components=components,
        nets=nets,
        connections=connections,
        blocks=blocks,
        semantic_edges=semantic_edges,
        version=data.get("version", "0.1.0"),
    )


def ir_from_json(raw: str) -> CircuitIR:
    """Deserialize a JSON string into a CircuitIR instance."""
    return ir_from_dict(json.loads(raw))
