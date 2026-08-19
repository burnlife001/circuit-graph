"""Split multi-unit ICs (e.g. LM358 dual op-amp) into per-unit virtual components.

This enricher runs before TopologyRecognizer so that each op-amp unit can be
matched independently by primitive templates. For example, LM358 (U2) is split
into:
  - U2A: first op-amp unit (pins 1, 2, 3)
  - U2B: second op-amp unit (pins 5, 6, 7)
  - U2C: power unit (pins 4, 8)

Unit definitions are loaded from ``circuit_graph/knowledge/multi_unit_devices.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from circuit_graph.ir import CircuitIR, Component, Connection, DeviceKind


@dataclass
class MultiUnitSplitter:
    """Split multi-unit ICs into per-unit virtual components."""

    name: str = "MultiUnitSplitter"
    priority: int = 10  # run before TopologyRecognizer (priority=30)
    requires: list[str] | None = None

    # part_number -> unit definitions loaded from knowledge base
    _device_db: dict[str, Any] | None = None

    def _load_device_db(self) -> dict[str, Any]:
        """Load multi-unit device definitions from knowledge base."""
        if self._device_db is not None:
            return self._device_db

        db_path = Path(__file__).parent.parent / "knowledge" / "multi_unit_devices.json"
        if not db_path.exists():
            self._device_db = {}
            return self._device_db

        with open(db_path, encoding="utf-8") as f:
            self._device_db = json.load(f)
        return self._device_db

    def can_enrich(self, ir: CircuitIR) -> bool:
        db = self._load_device_db()
        return any(
            c.device_kind == DeviceKind.IC and c.part_number in db
            for c in ir.components
        )

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        db = self._load_device_db()
        if not db:
            return ir

        new_components: list[Component] = []
        new_connections: list[Connection] = []

        for comp in ir.components:
            if comp.device_kind != DeviceKind.IC or comp.part_number not in db:
                new_components.append(comp)
                # keep original connections for non-split components
                new_connections.extend(c for c in ir.connections if c.pin_id.startswith(f"{comp.id}."))
                continue

            # split this IC into units
            device_info = db[comp.part_number]
            units = device_info.get("units", {})
            pin_map = {p.number: p for p in comp.pins}

            for suffix, unit_info in units.items():
                unit_id = f"{comp.id}{suffix}"
                pin_names = unit_info.get("pins", {})  # {"OUT": "1", "IN-": "2", ...}
                pin_numbers = list(pin_names.values())
                unit_pins = [pin_map[num] for num in pin_numbers if num in pin_map]
                if not unit_pins:
                    continue

                # rewrite pin numbers to logical names (e.g. "1" -> "OUT")
                # so that primitive templates can match by logical name
                logical_pins = []
                for pin in unit_pins:
                    logical_name = next(
                        (name for name, num in pin_names.items() if num == pin.number),
                        pin.number,
                    )
                    logical_pin = replace(pin, id=f"{unit_id}.{logical_name}", number=logical_name)
                    logical_pins.append(logical_pin)

                unit_comp = Component(
                    id=unit_id,
                    device_kind=comp.device_kind,
                    footprint=comp.footprint,
                    value=comp.value,
                    raw_value=comp.raw_value,
                    manufacturer=comp.manufacturer,
                    part_number=comp.part_number,
                    pins=logical_pins,
                    metadata={
                        **comp.metadata,
                        "split_from": comp.id,
                        "unit": suffix,
                        "unit_type": unit_info.get("type", "unknown"),
                        "pin_names": pin_names,  # {"OUT": "1", "IN-": "2", ...}
                    },
                )
                new_components.append(unit_comp)

                # rewrite connections for this unit's pins
                for pin, logical_pin in zip(unit_pins, logical_pins):
                    original_pin_id = pin.id
                    new_pin_id = f"{unit_id}.{logical_pin.number}"
                    for conn in ir.connections:
                        if conn.pin_id == original_pin_id:
                            new_connections.append(
                                Connection(
                                    pin_id=new_pin_id,
                                    net_id=conn.net_id,
                                    provenance=conn.provenance,
                                    metadata={
                                        **conn.metadata,
                                        "split_from": original_pin_id,
                                        "original_pin_number": pin.number,
                                        "pin_name": logical_pin.number,
                                    },
                                )
                            )

        return replace(ir, components=new_components, connections=new_connections)
