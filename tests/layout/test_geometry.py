import math

import pytest

from circuit_graph.ir import Component, DeviceKind, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.geometry import compute_pin_positions
from circuit_graph.layout.placer import Placement


def test_compute_pin_positions_grid_aligned():
    from circuit_graph.ir import CircuitIR, CircuitMeta

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
        metadata={"symbol": "Device:R"},
    )
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict(
        {
            "symbols": {
                "Device:R": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}}
            },
            "grid": {"size_mm": 2.54},
        }
    )
    placements = {"R1": Placement(x=0.0, y=0.0, rotation=90.0)}
    pins = compute_pin_positions(ir, placements, cfg)
    # Unrotated pin 1 extends up (+Y). After 90° clockwise rotation it points right (+X).
    assert pins["R1"]["1"][0] > 0.0
    assert math.isclose(pins["R1"]["1"][1], 0.0, abs_tol=1e-9)
    grid = cfg.grid.size_mm
    for pos in pins["R1"].values():
        assert math.isclose(pos[0] / grid, round(pos[0] / grid), abs_tol=1e-9)
        assert math.isclose(pos[1] / grid, round(pos[1] / grid), abs_tol=1e-9)


def test_compute_pin_positions_uses_symbol_cache():
    """When a symbol cache entry is provided, use the real pin tip position."""
    import os

    ksa = pytest.importorskip("kicad_sch_api")

    from circuit_graph.ir import CircuitIR, CircuitMeta

    # Make the KiCad symbol libraries discoverable in this test environment.
    os.environ.setdefault(
        "KICAD_SYMBOL_DIR", "D:/Programs/KiCad/10.0/share/kicad/symbols"
    )

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
        metadata={"symbol": "Device:R"},
    )
    ir = CircuitIR(meta=CircuitMeta(source="test", format="manual"), components=[r1])
    cfg = LayoutConfig.from_dict(
        {
            "symbols": {
                "Device:R": {"rotation": 0, "pin_directions": {"1": "left", "2": "right"}}
            },
            "grid": {"size_mm": 2.54},
        }
    )
    placements = {"R1": Placement(x=10.0, y=10.0, rotation=0.0)}

    symbol_def = ksa.get_symbol_cache().get_symbol("Device:R")
    if symbol_def is None:
        pytest.skip("KiCad Device:R symbol not available")
    cache = {"R1": symbol_def}

    pins = compute_pin_positions(ir, placements, cfg, symbol_cache=cache)
    # Device:R pin 1 is at the top of the resistor body; ``pin.position`` is the
    # wire-connection point, so the returned position is the real pin tip in
    # schematic space.  _transform flips Y before translating.
    assert pins["R1"]["1"] == (10.0, 10.0 - 3.81)
    assert pins["R1"]["2"] == (10.0, 10.0 + 3.81)
