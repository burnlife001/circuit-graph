import json
from pathlib import Path

import pytest

from circuit_graph.design import DesignError, DesignSpec, instantiate
from circuit_graph.ir import DeviceKind, ResistanceValue
from circuit_graph.primitives import PrimitiveTemplate, load_primitives


def _divider_template(tmp_path) -> PrimitiveTemplate:
    p = tmp_path / "div.json"
    p.write_text(
        json.dumps(
            {
                "id": "div",
                "name": "分压",
                "signals": [
                    {"id": "vin", "type": "net", "name_pattern": "VIN"},
                    {"id": "vout", "type": "net", "name_pattern": "VOUT"},
                    {"id": "gnd", "type": "net", "name_pattern": "GND"},
                ],
                "components": [
                    {
                        "id": "R_top",
                        "type": "Resistor",
                        "pins": [
                            {"number": "1", "net": "vin"},
                            {"number": "2", "net": "vout"},
                        ],
                    },
                    {
                        "id": "R_bottom",
                        "type": "Resistor",
                        "pins": [
                            {"number": "1", "net": "vout"},
                            {"number": "2", "net": "gnd"},
                        ],
                    },
                ],
                "design_inputs": [
                    {"id": "vin"},
                    {"id": "vout"},
                    {"id": "r_bottom", "default": 10000},
                ],
                "parameters": {
                    "R_top.value": "(vin - vout) * r_bottom / vout",
                    "R_bottom.value": "r_bottom",
                },
            }
        ),
        encoding="utf-8",
    )
    return load_primitives(tmp_path)[0]


def test_instantiate_divider_resolves_parameters(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    assert result.resolved == {"R_top.value": 14000.0, "R_bottom.value": 10000.0}
    by_id = {c.id: c for c in result.ir.components}
    assert isinstance(by_id["R1"].value, ResistanceValue)
    assert by_id["R1"].value.magnitude == 14000.0
    assert by_id["R2"].value.magnitude == 10000.0
    assert {n.name for n in result.ir.nets} == {"VIN", "VOUT", "GND"}


def test_instantiate_pin_and_connection_wiring(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(template, DesignSpec(inputs={"vin": 12, "vout": 5}))
    ir = result.ir
    # R1 pins: 1->VIN, 2->VOUT; R2 pins: 1->VOUT, 2->GND
    pin_to_net = {p.id: p.net_id for c in ir.components for p in c.pins}
    assert pin_to_net["R1.1"] == "VIN"
    assert pin_to_net["R1.2"] == "VOUT"
    assert pin_to_net["R2.1"] == "VOUT"
    assert pin_to_net["R2.2"] == "GND"
    conns = {(c.pin_id, c.net_id) for c in ir.connections}
    assert conns == {
        ("R1.1", "VIN"),
        ("R1.2", "VOUT"),
        ("R2.1", "VOUT"),
        ("R2.2", "GND"),
    }


def test_instantiate_net_and_designator_overrides(tmp_path) -> None:
    template = _divider_template(tmp_path)
    result = instantiate(
        template,
        DesignSpec(
            inputs={"vin": 12, "vout": 5},
            nets={"vin": "VIN_12V", "gnd": "GND_D"},
            designators={"R_top": "R12", "R_bottom": "R34"},
        ),
    )
    assert {c.id for c in result.ir.components} == {"R12", "R34"}
    assert {n.name for n in result.ir.nets} == {"VIN_12V", "VOUT", "GND_D"}


def test_instantiate_missing_required_input_raises(tmp_path) -> None:
    template = _divider_template(tmp_path)
    with pytest.raises(DesignError, match="vin"):
        instantiate(template, DesignSpec(inputs={"vout": 5}))


def test_instantiate_circular_dependency_raises(tmp_path) -> None:
    p = tmp_path / "cycle.json"
    p.write_text(
        json.dumps(
            {
                "id": "cyc",
                "name": "环",
                "signals": [{"id": "gnd", "type": "net", "name_pattern": "GND"}],
                "components": [
                    {
                        "id": "R1",
                        "type": "Resistor",
                        "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                    },
                    {
                        "id": "R2",
                        "type": "Resistor",
                        "pins": [{"number": "1", "net": "gnd"}, {"number": "2", "net": "gnd"}],
                    },
                ],
                "parameters": {"R1.value": "R2.value", "R2.value": "R1.value"},
            }
        ),
        encoding="utf-8",
    )
    template = load_primitives(tmp_path)[0]
    with pytest.raises(DesignError, match="[Cc]ircular"):
        instantiate(template, DesignSpec())
