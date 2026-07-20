"""Layout configuration dataclasses and loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.layout.exceptions import LayoutConfigError


@dataclass(frozen=True)
class GridConfig:
    size_mm: float = 2.54
    char_unit_mm: float = 1.27


@dataclass(frozen=True)
class PlacementConfig:
    input_x_mm: float = 38.1
    output_x_mm: float = 152.4
    column_step_mm: float = 25.4
    row_step_mm: float = 12.7
    direction: str = "left_to_right"


@dataclass(frozen=True)
class IoConfig:
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    power_top: list[str] = field(default_factory=list)
    power_bottom: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolConfig:
    rotation: float = 0.0
    pin_directions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LabelConfig:
    offset_mm: tuple[float, float] = (0.0, -2.54)
    font_height_mm: float = 1.27


_VALID_TOP_LEVEL_KEYS = {"meta", "grid", "placement", "io", "symbols", "labels"}


@dataclass(frozen=True)
class LayoutConfig:
    meta: dict[str, str] = field(default_factory=dict)
    grid: GridConfig = field(default_factory=GridConfig)
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    io: IoConfig = field(default_factory=IoConfig)
    symbols: dict[str, SymbolConfig] = field(default_factory=dict)
    labels: dict[str, LabelConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayoutConfig":
        """Build a LayoutConfig from a nested dict (JSON payload)."""
        if not isinstance(data, dict):
            raise LayoutConfigError(f"config must be a dict, got {type(data).__name__}")

        unknown = set(data.keys()) - _VALID_TOP_LEVEL_KEYS
        if unknown:
            raise LayoutConfigError(f"unknown top-level config keys: {sorted(unknown)}")

        meta = data.get("meta", {})
        grid = _build_grid(data.get("grid", {}))
        placement = _build_placement(data.get("placement", {}))
        io = _build_io(data.get("io", {}))
        symbols = _build_symbols(data.get("symbols", {}))
        labels = _build_labels(data.get("labels", {}))

        return cls(
            meta=meta,
            grid=grid,
            placement=placement,
            io=io,
            symbols=symbols,
            labels=labels,
        )


def _build_grid(raw: dict[str, Any]) -> GridConfig:
    return GridConfig(
        size_mm=float(raw.get("size_mm", 2.54)),
        char_unit_mm=float(raw.get("char_unit_mm", 1.27)),
    )


def _build_placement(raw: dict[str, Any]) -> PlacementConfig:
    return PlacementConfig(
        input_x_mm=float(raw.get("input_x_mm", 38.1)),
        output_x_mm=float(raw.get("output_x_mm", 152.4)),
        column_step_mm=float(raw.get("column_step_mm", 25.4)),
        row_step_mm=float(raw.get("row_step_mm", 12.7)),
        direction=str(raw.get("direction", "left_to_right")),
    )


def _build_io(raw: dict[str, Any]) -> IoConfig:
    return IoConfig(
        inputs=list(raw.get("inputs", [])),
        outputs=list(raw.get("outputs", [])),
        power_top=list(raw.get("power_top", [])),
        power_bottom=list(raw.get("power_bottom", [])),
    )


def _build_symbols(raw: dict[str, Any]) -> dict[str, SymbolConfig]:
    result: dict[str, SymbolConfig] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise LayoutConfigError(f"symbol '{key}' must be a dict")
        result[key] = SymbolConfig(
            rotation=float(value.get("rotation", 0.0)),
            pin_directions=dict(value.get("pin_directions", {})),
        )
    return result


def _build_labels(raw: dict[str, Any]) -> dict[str, LabelConfig]:
    result: dict[str, LabelConfig] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise LayoutConfigError(f"label '{key}' must be a dict")
        offset = value.get("offset_mm", [0.0, -2.54])
        result[key] = LabelConfig(
            offset_mm=(float(offset[0]), float(offset[1])),
            font_height_mm=float(value.get("font_height_mm", 1.27)),
        )
    return result


def load_layout_config(path_or_dict: Path | str | dict[str, Any] | None) -> LayoutConfig:
    """Load a LayoutConfig from a file path or dict.

    ``None`` returns the default config shipped with the package.
    """
    if path_or_dict is None:
        path_or_dict = Path(__file__).with_name("configs") / "default.json"

    if isinstance(path_or_dict, dict):
        return LayoutConfig.from_dict(path_or_dict)

    path = Path(path_or_dict)
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise LayoutConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LayoutConfigError(f"invalid JSON in {path}: {exc}") from exc

    return LayoutConfig.from_dict(data)
