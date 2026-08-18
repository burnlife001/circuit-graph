"""Protel .NET -> LayoutSchematic adapter.

Protel netlists carry NO geometric data -- no component coordinates, no
rotation, no wire segments. The adapter produces a :class:`LayoutSchematic`
with all geometry fields set to ``None`` and ``wires=[]``. Spatial assertions
auto-skip on .NET input (counted by the orchestrator under
``summary.rules_skipped_unsupported_input``; the adapter itself does not
flag this).

This module consumes :class:`circuit_graph.parsers.protel.ProtelNetParser`
as a read-only dependency. The existing parser is NOT modified.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from circuit_graph.ir import Component, DeviceKind
from circuit_graph.layout_check.assertions.base import refdes_prefix
from circuit_graph.layout_check.model import (
    LayoutComponent,
    LayoutNet,
    LayoutPin,
    LayoutSchematic,
)
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

# Net-kind inference heuristic.
#
# Mirrors the KiCad parser's net-name classification so that downstream
# ``applies_to.kind`` matching produces consistent results across input
# formats. Kept textually identical in both adapters (parallel work streams
# avoid a shared helper to sidestep merge friction).
_POWER_RE = re.compile(
    r"(?i)\b(vcc|vdd|vbat|vin|vout|vref|v\d|3v3|5v|12v|24v|\+\d)"
)
_GROUND_RE = re.compile(r"(?i)\b(gnd|vss|agnd|dgnd|earth)\b|^gnd$")


def _net_kind(name: str) -> Literal["power", "ground", "signal"]:
    """Classify a net by its name."""
    if _POWER_RE.search(name):
        return "power"
    if _GROUND_RE.search(name):
        return "ground"
    return "signal"


# IR ``DeviceKind`` -> layout-check ``type`` string.
_DEVICE_KIND_TO_TYPE: dict[DeviceKind, str] = {
    DeviceKind.RESISTOR: "resistor",
    DeviceKind.CAPACITOR: "capacitor",
    DeviceKind.INDUCTOR: "inductor",
    DeviceKind.IC: "ic",
    DeviceKind.MOSFET: "mosfet",
    DeviceKind.DIODE: "diode",
    DeviceKind.CONNECTOR: "connector",
    DeviceKind.UNKNOWN: "unknown",
}


def _infer_type(comp: Component) -> str:
    """Infer layout-check ``type`` string from IR ``DeviceKind`` enum."""
    return _DEVICE_KIND_TO_TYPE.get(comp.device_kind, "unknown")


def _pin_net(pin_id: str, net_to_pins: dict[str, list[str]]) -> str:
    """Return the net a pin belongs to, or empty string if unconnected."""
    for net_id, members in net_to_pins.items():
        if pin_id in members:
            return net_id
    return ""


def parse_protel(path: Path) -> LayoutSchematic:
    """Parse a Protel .NET file into a :class:`LayoutSchematic` side-car model.

    Geometry fields are all ``None``; ``wires`` is empty. Per-pin coordinates
    are ``None``. The :class:`LayoutNet.kind` is inferred from the net name
    via the same heuristic used by the KiCad parser.
    """
    # 1. Read the file via the existing ProtelNetParser.
    parser = ProtelNetParser()
    ir = parser.parse(ParserInput(file_path=str(path)))

    # 2. Build a net-name -> list[pin_id] map from connections.
    net_to_pins: dict[str, list[str]] = {}
    for conn in ir.connections:
        net_to_pins.setdefault(conn.net_id, []).append(conn.pin_id)

    # 3. Build LayoutComponents.
    layout_comps: list[LayoutComponent] = []
    for comp in ir.components:
        layout_comps.append(
            LayoutComponent(
                id=comp.id,
                type=_infer_type(comp),
                value=comp.raw_value,
                position_mm=None,
                rotation_deg=None,
                refdes_prefix=refdes_prefix(comp.id),
                sheet=None,
                page=None,
                pins=[
                    LayoutPin(
                        number=p.number,
                        net=_pin_net(p.id, net_to_pins),
                        x_mm=None,
                        y_mm=None,
                    )
                    for p in comp.pins
                ],
            )
        )

    # 4. Build LayoutNets (kind-inferred, no wire info). Dedupe by net id.
    layout_nets: list[LayoutNet] = []
    seen: set[str] = set()
    for net in ir.nets:
        if net.id in seen:
            continue
        seen.add(net.id)
        layout_nets.append(
            LayoutNet(
                name=net.id,
                kind=_net_kind(net.id),
                members=net_to_pins.get(net.id, []),
            )
        )

    return LayoutSchematic(
        components=layout_comps,
        nets=layout_nets,
        wires=[],  # Protel has no wire data
    )


__all__ = ["parse_protel"]
