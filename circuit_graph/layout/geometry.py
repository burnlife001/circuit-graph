"""Geometric helpers: pin positions after component rotation/translation."""

from __future__ import annotations

import math
from typing import Any

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import Placement


def compute_pin_positions(
    ir: CircuitIR,
    placements: dict[str, Placement],
    config: LayoutConfig,
    symbol_cache: dict[str, Any] | None = None,
) -> dict[str, dict[str, tuple[float, float]]]:
    """Return absolute schematic-space pin positions for every component.

    If a kicad_sch_api symbol cache is provided, real pin positions are used;
    otherwise a simple rectangular approximation is used for testing.

    Rotation semantics (design-spec §3):
      - ``symbols.<name>.rotation`` is applied to the whole symbol body.
      - ``symbols.<name>.pin_directions`` describe pin directions in the
        **unrotated** symbol-local coordinate system.
      - This function therefore computes unrotated local offsets and lets
        ``_transform()`` apply the symbol rotation.
    """
    result: dict[str, dict[str, tuple[float, float]]] = {}

    for comp in ir.components:
        placement = placements[comp.id]
        result[comp.id] = {}

        sym_key = comp.metadata.get("symbol", comp.part_number or comp.id)
        sym_cfg = config.symbols.get(sym_key)
        rotation = placement.rotation

        for pin in comp.pins:
            local = _local_pin_position(
                comp.id,
                pin.number,
                sym_cfg.pin_directions if sym_cfg else {},
                symbol_cache,
            )
            abs_pos = _transform(local, placement.x, placement.y, rotation)
            result[comp.id][pin.number] = abs_pos

    return result


def _local_pin_position(
    comp_id: str,
    pin_num: str,
    pin_directions: dict[str, str],
    symbol_cache: dict[str, Any] | None,
) -> tuple[float, float]:
    """Return pin position in unrotated symbol-local space (mm).

    ``pin_directions`` is keyed by pin number and gives the direction the pin
    faces in the unrotated symbol (up/down/left/right). The actual rotation of
    the symbol is applied later by ``_transform()``.

    If a kicad_sch_api symbol cache is provided, real pin positions from the
    library definition are used.  The cache entry may be a ``SymbolDefinition``
    or a component exposing a ``get_symbol_definition()`` method.
    """
    if symbol_cache is not None:
        entry = symbol_cache.get(comp_id)
        if entry is not None:
            return _pin_from_symbol_cache(entry, pin_num)

    direction = pin_directions.get(pin_num, "right")
    length = 2.54  # default pin length
    table = {
        "up": (0.0, length),
        "down": (0.0, -length),
        "left": (-length, 0.0),
        "right": (length, 0.0),
    }
    return table.get(direction, (length, 0.0))


def _pin_from_symbol_cache(entry: Any, pin_num: str) -> tuple[float, float]:
    """Return the local tip position of ``pin_num`` from a cached symbol.

    ``entry`` is either a kicad_sch_api ``SymbolDefinition`` or a component
    object that can resolve its own symbol definition.  The returned position
    is the pin tip in symbol-local coordinates, with +Y up.
    """
    # Accept SymbolDefinition directly, or resolve it from a component.
    if hasattr(entry, "pins"):
        symbol_def = entry
    elif hasattr(entry, "get_symbol_definition"):
        symbol_def = entry.get_symbol_definition()
        if symbol_def is None:
            raise RuntimeError(f"cannot resolve symbol definition for {entry!r}")
    else:
        raise TypeError(
            f"symbol cache entry must expose pins or get_symbol_definition; "
            f"got {type(entry).__name__}"
        )

    pin = symbol_def.get_pin(pin_num)
    if pin is None:
        raise KeyError(f"pin {pin_num} not found in symbol {symbol_def.lib_id}")

    # KiCad symbol-local coordinates: +Y is up.  ``pin.position`` is the
    # wire-connection point (the point where a schematic wire must end), and
    # ``rotation`` is the pin direction in degrees, 0 = right, 90 = up,
    # 180 = left, 270 = down.  Return ``pin.position`` directly so wires meet
    # the actual pin tip in schematic space.
    return (pin.position.x, pin.position.y)


def _transform(
    local: tuple[float, float], cx: float, cy: float, rotation_deg: float
) -> tuple[float, float]:
    """Apply KiCad-style transform: invert Y, rotate, translate.

    The input ``local`` is in unrotated symbol space where +Y is up. KiCad
    schematic space uses +Y down, so we first flip Y, then rotate by
    ``rotation_deg`` (matching kicad_sch_api / convert_to_kicad.py), then
    translate.
    """
    x, y = local[0], -local[1]
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    rx = x * cos_r - y * sin_r
    ry = x * sin_r + y * cos_r
    return (cx + rx, cy + ry)
