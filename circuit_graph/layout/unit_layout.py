"""Orchestrate layout for a single unit."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.geometry import compute_pin_positions
from circuit_graph.layout.label_placer import Label, place_labels
from circuit_graph.layout.placer import Placement, place_components
from circuit_graph.layout.signal_graph import validate_io_nets
from circuit_graph.layout.wire_placer import WireSegment, place_wires


@dataclass(frozen=True)
class UnitLayoutResult:
    placements: dict[str, Placement]
    pin_positions: dict[str, dict[str, tuple[float, float]]]
    wires: list[WireSegment]
    labels: list[Label]


def layout_unit(
    ir: CircuitIR,
    config: LayoutConfig,
    symbol_cache: dict[str, Any] | None = None,
) -> UnitLayoutResult:
    """Layout one unit and return placements, wires, and labels."""
    validate_io_nets(ir, config)

    placements = place_components(ir, config)
    pin_positions = compute_pin_positions(ir, placements, config, symbol_cache)
    wires = place_wires(ir, placements, pin_positions, config)
    labels = place_labels(ir, placements, config)

    _warn_dangling_nets(ir, config)

    return UnitLayoutResult(
        placements=placements,
        pin_positions=pin_positions,
        wires=wires,
        labels=labels,
    )


def _warn_dangling_nets(ir: CircuitIR, config: LayoutConfig) -> None:
    """Emit a warning for nets with fewer than two connected pins."""
    pin_by_id = {p.id: p for comp in ir.components for p in comp.pins}
    seen: dict[str, list] = {}
    for conn in ir.connections:
        pin = pin_by_id.get(conn.pin_id)
        if pin is None:
            continue
        seen.setdefault(conn.net_id, []).append(pin)
    for net_id, pins in seen.items():
        if len(pins) < 2:
            warnings.warn(
                f"Net '{net_id}' has fewer than 2 pins; no wire placed", stacklevel=2
            )
