"""Enricher pipeline framework.

Enrichers transform a CircuitIR in a prioritized, dependency-checked order
without mutating the input IR.  Errors in individual enrichers are isolated
and reported as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from circuit_graph.ir import CircuitIR


@runtime_checkable
class Enricher(Protocol):
    """Protocol for an IR enricher."""

    name: str
    priority: int
    requires: list[str] | None

    def can_enrich(self, ir: CircuitIR) -> bool | Any: ...  # noqa: D102
    def enrich(self, ir: CircuitIR) -> CircuitIR: ...  # noqa: D102


@dataclass
class EnricherResult:
    ir: CircuitIR
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def run_enrichers(ir: CircuitIR, enrichers: list[Enricher]) -> EnricherResult:
    """Run ``enrichers`` against ``ir`` in priority order.

    Returns the enriched IR plus any warnings produced by dependency misses or
    execution failures.
    """
    warnings: list[str] = []
    sorted_enrichers = sorted(enrichers, key=lambda e: e.priority)
    completed: set[str] = set()

    for e in sorted_enrichers:
        if e.requires:
            missing = [r for r in e.requires if r not in completed]
            if missing:
                warnings.append(
                    f"ENRICH_DEPENDENCY_MISSING: {e.name} requires [{', '.join(missing)}]"
                )
                continue
        try:
            can = e.can_enrich(ir)
            if hasattr(can, "__await__"):
                can = __import__("asyncio").run(can)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ENRICH_CANENRICH_ERROR: {e.name} — {exc}")
            continue

        if not can:
            warnings.append(f"{e.name}: skipped (canEnrich=false)")
            continue

        try:
            result = e.enrich(ir)
            if hasattr(result, "__await__"):
                result = __import__("asyncio").run(result)  # pragma: no cover
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"ENRICH_EXECUTION_ERROR: {e.name} — {exc}")
            continue

        ir = result
        completed.add(e.name)

    return EnricherResult(ir=ir, warnings=warnings)
