"""Value normalizer enricher.

Translates raw component values (e.g. ``5.1K%1``, ``4.7UF16V``) into
structured normalized values for resistors, capacitors and inductors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    Component,
    DeviceKind,
    InductanceValue,
    NormalizedValue,
    ResistanceValue,
    UnknownValue,
)


@dataclass
class ValueNormalizer:
    """Normalize raw component values based on device kind."""

    name: str = "ValueNormalizer"
    priority: int = 10
    requires: list[str] | None = None

    def can_enrich(self, ir: CircuitIR) -> bool:
        return any(c.raw_value and c.value is None for c in ir.components)

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        components: list[Component] = []
        for comp in ir.components:
            if comp.value is not None or not comp.raw_value:
                components.append(comp)
                continue
            normalized = _normalize_value(comp.raw_value, comp.device_kind)
            if normalized is not None:
                components.append(
                    Component(
                        id=comp.id,
                        device_kind=comp.device_kind,
                        footprint=comp.footprint,
                        value=normalized,
                        raw_value=comp.raw_value,
                        manufacturer=comp.manufacturer,
                        part_number=comp.part_number,
                        pins=comp.pins,
                        metadata=comp.metadata,
                    )
                )
            else:
                components.append(comp)
        return CircuitIR(
            meta=ir.meta,
            components=components,
            nets=ir.nets,
            connections=ir.connections,
            blocks=ir.blocks,
            semantic_edges=ir.semantic_edges,
            version=ir.version,
            metadata=ir.metadata,
        )


def _normalize_value(raw: str, device_kind: str | DeviceKind) -> NormalizedValue | None:
    trimmed = raw.strip()
    if trimmed.upper() == "NC":
        return UnknownValue(raw=trimmed)

    kind_str = device_kind.value if isinstance(device_kind, DeviceKind) else device_kind
    if kind_str == DeviceKind.RESISTOR.value:
        r = _parse_resistance(trimmed)
        return ResistanceValue(**r) if r else None
    if kind_str == DeviceKind.CAPACITOR.value:
        value_str, tolerance = _strip_percent_tolerance(trimmed)
        c = _parse_capacitance(value_str)
        return CapacitanceValue(**c, tolerance=tolerance) if c else None
    if kind_str == DeviceKind.INDUCTOR.value:
        value_str, tolerance = _strip_percent_tolerance(trimmed)
        inductance = _parse_inductance(value_str)
        return InductanceValue(**inductance, tolerance=tolerance) if inductance else None
    return UnknownValue(raw=trimmed)


def _strip_percent_tolerance(value_str: str) -> tuple[str, float | None]:
    """Strip an explicit percent tolerance (e.g. ``%1``, ``%5``) from a value string."""
    m = re.search(r"%([\d.]+)", value_str)
    if m:
        tolerance = float(m.group(1)) / 100
        return re.sub(r"%[\d.]+", "", value_str).strip(), tolerance
    return value_str, None


# Matches values such as ``100``, ``100K``, ``5.1K%1``, ``1.5M``, ``4.7 Ω``,
# ``10R`` and ``0.02R%1``.
_RESISTANCE_RE = re.compile(r"^([\d.]+)\s*([kKmMrR]?)[Ω\s]*(?:(%)([\d.]+))?$")


def _parse_resistance(value_str: str) -> dict[str, Any] | None:
    value_str = value_str.strip()
    m = _RESISTANCE_RE.match(value_str)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2).upper()
    multiplier = 1
    if suffix == "K":
        multiplier = 1000
    elif suffix == "M":
        multiplier = 1_000_000
    # R/r and no suffix both mean base ohms.
    tolerance = float(m.group(4)) / 100 if m.group(3) else None
    return {"magnitude": num * multiplier, "unit": "Ω", "tolerance": tolerance}


def _parse_capacitance(
    value_str: str,
) -> dict[str, Any] | None:
    m = re.match(r"^([\d.]+)\s*(pF|nF|µF|uF|UF|MF)?[/\s]*(?:(\d+)V)?", value_str, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "µF").lower()
    if unit == "uf":
        unit = "µF"
    voltage_rating = int(m.group(3)) if m.group(3) else None
    return {"magnitude": num, "unit": unit, "voltage_rating": voltage_rating}


def _parse_inductance(
    value_str: str,
) -> dict[str, Any] | None:
    m = re.match(r"^([\d.]+)\s*(nH|µH|uH|mH)?$", value_str, re.IGNORECASE)
    if not m:
        return None
    unit = (m.group(2) or "µH").lower()
    if unit == "uh":
        unit = "µH"
    return {"magnitude": float(m.group(1)), "unit": unit}
