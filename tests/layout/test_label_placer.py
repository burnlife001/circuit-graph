from circuit_graph.ir import Component, DeviceKind
from circuit_graph.layout.config import LayoutConfig
from circuit_graph.layout.label_placer import place_labels
from circuit_graph.layout.placer import Placement


def test_place_labels_resolve_overlap():
    comp = Component(id="R1", device_kind=DeviceKind.RESISTOR, raw_value="10k")
    placements = {"R1": Placement(x=0.0, y=0.0, rotation=0.0)}
    cfg = LayoutConfig.from_dict(
        {
            "labels": {
                "reference": {"offset_mm": [0, -1.27], "font_height_mm": 1.27},
                "value": {"offset_mm": [0, -1.27], "font_height_mm": 1.27},
            }
        }
    )

    from circuit_graph.ir import CircuitIR, CircuitMeta

    ir = CircuitIR(
        meta=CircuitMeta(source="test", format="manual"),
        components=[comp],
    )
    labels = place_labels(ir, placements, cfg)
    assert len(labels) == 2
    assert labels[0].angle == 0.0
    assert labels[1].angle == 0.0
    assert labels[0].y != labels[1].y
