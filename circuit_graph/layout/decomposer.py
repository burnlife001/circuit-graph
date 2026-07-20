"""Split a CircuitIR into layout units."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import CircuitIR


@dataclass(frozen=True)
class LayoutUnit:
    id: str
    name: str
    unit_type: str
    ir: CircuitIR


def decompose(ir: CircuitIR) -> list[LayoutUnit]:
    """Return layout units.

    v1: treat the entire circuit as a single unit.
    """
    return [
        LayoutUnit(
            id="main",
            name=ir.meta.source or "main",
            unit_type="default",
            ir=ir,
        )
    ]
