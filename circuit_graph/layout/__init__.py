"""Schematic layout: turn a CircuitIR into a readable KiCad schematic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig, load_layout_config
from circuit_graph.layout.decomposer import decompose
from circuit_graph.layout.kicad_writer import apply_layout
from circuit_graph.layout.page_layout import layout_page
from circuit_graph.layout.unit_layout import layout_unit

__all__ = ["layout_schematic", "LayoutConfig", "load_layout_config"]


def layout_schematic(
    ir: CircuitIR,
    sch: Any,
    unit_configs: dict[str, dict | str | Path] | None = None,
) -> None:
    """Layout ``ir`` onto ``sch`` (a kicad_sch_api.Schematic object).

    ``unit_configs`` maps unit **name** or **unit_type** to a LayoutConfig dict
    or a JSON path. Resolution order: unit.name → unit.unit_type → default config.
    """
    units = decompose(ir)

    # Build a cache mapping component id → kicad_sch_api component/definition so
    # geometry.py can use real symbol pin positions instead of rectangular
    # approximations.
    symbol_cache = _build_symbol_cache(sch)

    results: dict[str, Any] = {}
    for unit in units:
        cfg = _resolve_config(unit, unit_configs)
        results[unit.id] = layout_unit(unit.ir, cfg, symbol_cache=symbol_cache)

    page = layout_page(results)
    apply_layout(sch, page)


def _build_symbol_cache(sch: Any) -> dict[str, Any]:
    """Return {component_id: ksa component object} for real pin lookups."""
    try:
        import kicad_sch_api as ksa
    except ImportError:
        return {}

    if not isinstance(sch, ksa.Schematic):
        return {}

    return {c.reference: c for c in sch.components}


def _resolve_config(unit, unit_configs):
    configs = unit_configs or {}
    candidate = configs.get(unit.name)
    if candidate is None:
        candidate = configs.get(unit.unit_type)
    if candidate is None:
        candidate = configs.get(unit.id)
    if candidate is None:
        candidate = Path(__file__).with_name("configs") / "default.json"
    return load_layout_config(candidate)
