import math

from circuit_graph.ir import Component, Connection, DeviceKind, Net, Pin
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.placer import place_components


def _ir_two_resistors() -> "CircuitIR":
    from circuit_graph.ir import CircuitIR, CircuitMeta, Provenance

    r1 = Component(
        id="R1",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R1.1", number="1"), Pin(id="R1.2", number="2")],
    )
    r2 = Component(
        id="R2",
        device_kind=DeviceKind.RESISTOR,
        pins=[Pin(id="R2.1", number="1"), Pin(id="R2.2", number="2")],
    )
    n1 = Net(id="N1", name="IN")
    n2 = Net(id="N2", name="OUT")
    return CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[r1, r2],
        nets=[n1, n2],
        connections=[
            Connection(pin_id="R1.1", net_id="N1", provenance=Provenance.NETLIST),
            Connection(pin_id="R1.2", net_id="N2", provenance=Provenance.NETLIST),
            Connection(pin_id="R2.1", net_id="N2", provenance=Provenance.NETLIST),
        ],
    )


def test_place_components_grid_aligned():
    ir = _ir_two_resistors()
    cfg = LayoutConfig.from_dict(
        {
            "io": {"inputs": ["IN"], "outputs": ["OUT"]},
            "placement": {"input_x_mm": 38.1, "column_step_mm": 25.4, "row_step_mm": 12.7},
            "grid": {"size_mm": 2.54},
        }
    )
    placements = place_components(ir, cfg)
    grid = cfg.grid.size_mm
    for p in placements.values():
        # Avoid catastrophic cancellation: check ratio closeness to int.
        assert math.isclose(p.x / grid, round(p.x / grid), abs_tol=1e-9)
        assert math.isclose(p.y / grid, round(p.y / grid), abs_tol=1e-9)


def test_input_is_left_of_output():
    ir = _ir_two_resistors()
    cfg = LayoutConfig.from_dict({"io": {"inputs": ["IN"], "outputs": ["OUT"]}})
    placements = place_components(ir, cfg)
    assert placements["R1"].x < placements["R2"].x
