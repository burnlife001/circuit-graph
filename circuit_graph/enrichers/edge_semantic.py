"""Edge-semantic enricher.

For every pin–net connection in the IR, derives a ``direction`` (source /
load / bidirectional) and a ``role`` (power / gnd / analog / digital /
clock), plus an optional ``level`` for power rails. The result is written
onto ``Connection.metadata["edge_semantic"]`` so it can flow into the
corresponding :class:`~circuit_graph.ir.GraphEdge` when the graph is built.

Direction rules
---------------
* Active output pin (OUT, VOUT, VO, TX, …) on a non-power net → ``source``.
* Active input pin (IN, IN+, RX, …) on a non-power net → ``load``.
* Active bidirectional pin (IO, GPIO, SDA, …) → ``bidirectional``.
* Pin on a power/gnd net → ``bidirectional`` (current flows either way).
* Passive components (R/C/L) → ``bidirectional`` regardless of pin name.
* Unknown (no resolved pin name and not passive) → ``bidirectional`` to stay
  conservative; predicates downstream can still filter by role.

Role rules
----------
* ``net.type == POWER`` → ``power``.
* ``net.type == GND`` → ``gnd``.
* ``net.type == ANALOG`` → ``analog``.
* ``net.type == CLOCK`` → ``clock``.
* Everything else (SIGNAL / UNKNOWN) → ``digital``.

Level rules
-----------
Power/gnd nets carry a nominal voltage derived from the net name via a small
regex (5V / 3V3 / 1V8 / USB_5V → "5V", "3V3", "1V8", "5V"). Non-power roles
leave level as ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from circuit_graph.ir import (
    CircuitIR,
    Connection,
    DeviceKind,
    NetType,
)

# Pin-name patterns. Universal across vendors; "OBID" deliberately omitted
# to keep the list short and obviously right when reading the file.
_OUTPUT_PIN_RE = re.compile(r"^(OUT|VOUT|VO|TX|SDO|MOSI|REG)$", re.IGNORECASE)
_INPUT_PIN_RE = re.compile(
    r"^[+\-]?(IN|RX|SDI|MISO|FB|ADJ|EN|ENB|COMP)[+\-]?$",
    re.IGNORECASE,
)
_BIDIR_PIN_RE = re.compile(
    r"^(IO|GPIO|SDA|SCL|SDL|SDIO|XTAL|CLK|OSC)$",
    re.IGNORECASE,
)
_POWER_PIN_RE = re.compile(r"^(VDD|VCC|VIN|VI|PVIN|VBAT|VBUS|V\+)$", re.IGNORECASE)
_GND_PIN_RE = re.compile(r"^(GND|PGND|AGND|DGND|VSS|VEE)$", re.IGNORECASE)

# Voltage extraction: 5V, 3V3, 1V8, 3.3V, 12V — first match wins.
_VOLTAGE_RE = re.compile(r"(\d+)[Vv](\d+)?")
_USB_VOLTAGE_RE = re.compile(r"USB[_]?5V|USB_?VCC|^VBUS$", re.IGNORECASE)


def _classify_direction(
    pin_name: str | None,
    device_kind: DeviceKind,
    net_type: NetType | None,
) -> str:
    """Return ``"source"``, ``"load"``, or ``"bidirectional"``."""
    # Power/gnd nets are always bidirectional regardless of pin.
    if net_type in {NetType.POWER, NetType.GND}:
        return "bidirectional"

    # Passives: current flows through either way.
    if device_kind in {
        DeviceKind.RESISTOR,
        DeviceKind.CAPACITOR,
        DeviceKind.INDUCTOR,
    }:
        return "bidirectional"

    # Active pins: classify by name when available.
    if pin_name is not None:
        name = pin_name.strip()
        if _OUTPUT_PIN_RE.match(name):
            return "source"
        if _INPUT_PIN_RE.match(name):
            return "load"
        if _BIDIR_PIN_RE.match(name):
            return "bidirectional"
        if _POWER_PIN_RE.match(name) or _GND_PIN_RE.match(name):
            return "bidirectional"

    # Unknown active pin: stay conservative.
    return "bidirectional"


def _classify_role(net_type: NetType | None) -> str:
    if net_type is NetType.POWER:
        return "power"
    if net_type is NetType.GND:
        return "gnd"
    if net_type is NetType.ANALOG:
        return "analog"
    if net_type is NetType.CLOCK:
        return "clock"
    # SIGNAL / UNKNOWN — most consumer signals are digital.
    return "digital"


def _extract_level(net_name: str) -> str | None:
    """Pull a nominal voltage label out of a power-rail name.

    Returns a canonical form ("5V", "3V3", "1V8", "12V"); ``None`` if the
    name does not look like a power rail.
    """
    if _USB_VOLTAGE_RE.match(net_name):
        return "5V"
    m = _VOLTAGE_RE.search(net_name.upper())
    if not m:
        return None
    whole = m.group(1)
    frac = m.group(2)
    if frac:
        # e.g. "3V3" / "1V8" / "1V25" — keep the digit form.
        return f"{whole}V{frac}"
    return f"{whole}V"


@dataclass
class EdgeSemanticEnricher:
    """Annotate every ``Connection`` with direction / role / level."""

    name: str = "EdgeSemanticEnricher"
    # Net type is the only hard requirement — without it the role classification
    # collapses to "digital". PinNameResolver is a soft dependency: without it,
    # every active pin falls back to "bidirectional", which is still safe for
    # downstream predicates.
    priority: int = 25
    requires: list[str] | None = field(
        default_factory=lambda: ["NetClassifier"]
    )

    def can_enrich(self, ir: CircuitIR) -> bool:
        return any(
            "edge_semantic" not in (c.metadata or {}) for c in ir.connections
        )

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        comp_by_id = {c.id: c for c in ir.components}
        net_by_id = {n.id: n for n in ir.nets}
        pin_by_id: dict[str, Any] = {}
        for comp in ir.components:
            for pin in comp.pins:
                pin_by_id[pin.id] = pin

        new_connections: list[Connection] = []
        for conn in ir.connections:
            if "edge_semantic" in (conn.metadata or {}):
                new_connections.append(conn)
                continue
            comp_id, _ = _split_pin(conn.pin_id)
            comp = comp_by_id.get(comp_id) if comp_id else None
            pin = pin_by_id.get(conn.pin_id)
            net = net_by_id.get(conn.net_id)

            device_kind = comp.device_kind if comp is not None else DeviceKind.UNKNOWN
            net_type = net.type if net is not None else None
            pin_name = pin.name if pin is not None else None
            net_name = net.name if net is not None else conn.net_id

            direction = _classify_direction(pin_name, device_kind, net_type)
            role = _classify_role(net_type)
            level = _extract_level(net_name) if role in {"power", "gnd"} else None

            new_meta = dict(conn.metadata or {})
            new_meta["edge_semantic"] = {
                "direction": direction,
                "role": role,
                "level": level,
            }
            new_connections.append(
                Connection(
                    pin_id=conn.pin_id,
                    net_id=conn.net_id,
                    provenance=conn.provenance,
                    metadata=new_meta,
                )
            )

        return CircuitIR(
            meta=ir.meta,
            components=list(ir.components),
            nets=list(ir.nets),
            connections=new_connections,
            blocks=list(ir.blocks),
            semantic_edges=list(ir.semantic_edges),
            version=ir.version,
        )


def _split_pin(pin_id: str) -> tuple[str | None, str | None]:
    if "." not in pin_id:
        return None, None
    comp_id, _, pin_num = pin_id.rpartition(".")
    if not comp_id or not pin_num:
        return None, None
    return comp_id, pin_num


__all__ = ["EdgeSemanticEnricher"]
