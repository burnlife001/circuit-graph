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


_GND_FAMILY_RE = re.compile(
    r"^(GND|PGND|AGND|DGND|SGND|CGND)", re.IGNORECASE
)


def _classify_role(net_type: NetType | None, net_name: str | None = None) -> str:
    if net_type is NetType.POWER:
        return "power"
    if net_type is NetType.GND:
        return "gnd"
    if net_type is NetType.ANALOG:
        return "analog"
    if net_type is NetType.CLOCK:
        return "clock"
    # NetClassifier 不再把 CGND 并入 NetType.GND（todolist crash.md T1：避免
    # GND↔CGND 桥接电阻被规则引擎误判），但 CGND 语义上仍是地 —— 按名字回退，
    # 让 expected_role=gnd 的模板（rc-snubber 等）能继续匹配。
    if net_name is not None and _GND_FAMILY_RE.match(net_name):
        return "gnd"
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


def _supply_island_net_ids(ir: CircuitIR) -> set[str]:
    """找出经单电阻供电、带本地去耦的自动命名电源岛。

    仅处理 Protel 风格 ``Net...`` 且 NetClassifier 无法定性的网络：必须含
    引脚名缺失的 IC、电容到 GND、电阻到明确 POWER，且不能再有电阻下臂到 GND。
    最后一条把 NTC/ADC 分压输出滤波排除在外；命名信号（如 RST）和已知
    VIN 等明确引脚名的网络不走这条缺失证据启发式。
    """
    net_by_id = {n.id: n for n in ir.nets}
    comp_by_id = {c.id: c for c in ir.components}
    pin_by_id = {pin.id: pin for comp in ir.components for pin in comp.pins}
    component_nets: dict[str, set[str]] = {}
    net_components: dict[str, set[str]] = {}
    unnamed_ic_nets: set[str] = set()
    for conn in ir.connections:
        comp_id, _ = _split_pin(conn.pin_id)
        if comp_id is None:
            continue
        component_nets.setdefault(comp_id, set()).add(conn.net_id)
        net_components.setdefault(conn.net_id, set()).add(comp_id)
        comp = comp_by_id.get(comp_id)
        pin = pin_by_id.get(conn.pin_id)
        if (
            comp is not None
            and comp.device_kind is DeviceKind.IC
            and (pin is None or not pin.name)
        ):
            unnamed_ic_nets.add(conn.net_id)

    islands: set[str] = set()
    for net in ir.nets:
        if net.type is not NetType.UNKNOWN or not net.name.startswith("Net"):
            continue
        if net.id not in unnamed_ic_nets:
            continue
        members = net_components.get(net.id, set())
        if not any(
            comp_by_id[cid].device_kind is DeviceKind.IC
            for cid in members
            if cid in comp_by_id
        ):
            continue
        cap_to_gnd = False
        resistor_to_power = False
        resistor_to_gnd = False
        for comp_id in members:
            comp = comp_by_id.get(comp_id)
            if comp is None:
                continue
            other_ids = component_nets.get(comp_id, set()) - {net.id}
            if len(other_ids) != 1:
                continue
            other = net_by_id.get(next(iter(other_ids)))
            if other is None:
                continue
            if comp.device_kind is DeviceKind.CAPACITOR and other.type is NetType.GND:
                cap_to_gnd = True
            elif comp.device_kind is DeviceKind.RESISTOR:
                resistor_to_power |= other.type is NetType.POWER
                resistor_to_gnd |= other.type is NetType.GND
        if cap_to_gnd and resistor_to_power and not resistor_to_gnd:
            islands.add(net.id)
    return islands


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
        supply_islands = _supply_island_net_ids(ir)
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
            role = (
                "power"
                if conn.net_id in supply_islands
                else _classify_role(net_type, net_name)
            )
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
            metadata=ir.metadata,
        )


def _split_pin(pin_id: str) -> tuple[str | None, str | None]:
    if "." not in pin_id:
        return None, None
    comp_id, _, pin_num = pin_id.rpartition(".")
    if not comp_id or not pin_num:
        return None, None
    return comp_id, pin_num


__all__ = ["EdgeSemanticEnricher"]
