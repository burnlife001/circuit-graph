"""Place units on the schematic page."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.layout.exceptions import LayoutPageOverflowError
from circuit_graph.layout.unit_layout import UnitLayoutResult


@dataclass(frozen=True)
class PageLayoutResult:
    units: dict[str, UnitLayoutResult]


def layout_page(units: dict[str, UnitLayoutResult]) -> PageLayoutResult:
    """v1: page layout is a passthrough; only one unit is supported."""
    if len(units) > 1:
        raise LayoutPageOverflowError(
            "Multiple layout units are not yet supported in v1"
        )
    return PageLayoutResult(units=units)
