"""Helpers for building CircuitIR fixtures in tests.

The T268 fixture ships part numbers as ``None`` so end-to-end power-tree
tests need a hand-rolled IR. ``build_ir`` turns a list of (component_id,
kind, pin list) tuples into a CircuitIR that the rest of the stack can
process just like a parsed netlist.
"""

from __future__ import annotations

from typing import Any, Iterable

from circuit_graph.ir import (
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    Net,
    NetType,
    Pin,
)


def make_component(
    comp_id: str,
    device_kind: DeviceKind,
    pins: list[tuple[str, str]],
    *,
    part_number: str | None = None,
    footprint: str | None = None,
    raw_value: str | None = None,
) -> Component:
    """Build a :class:`Component` from ``(pin_number, pin_name)`` pairs.

    ``pin_name`` is the signal label printed on the schematic; the
    power-tree recognizer keys off this name.
    """
    ir_pins = [
        Pin(
            id=f"{comp_id}.{pin_num}",
            number=pin_num,
            name=pin_name,
            type="unknown",
        )
        for pin_num, pin_name in pins
    ]
    return Component(
        id=comp_id,
        device_kind=device_kind,
        footprint=footprint,
        value=None,
        raw_value=raw_value,
        manufacturer=None,
        part_number=part_number,
        pins=ir_pins,
        metadata={},
    )


def make_net(net_id: str, name: str, net_type: NetType | None = None) -> Net:
    return Net(id=net_id, name=name, type=net_type, metadata={})


def build_ir(
    components: list[Component],
    nets: list[Net],
    wiring: Iterable[tuple[str, str]],
    *,
    source: str = "synthetic-fixture",
    edge_semantics: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> CircuitIR:
    """Wire components to nets and return a CircuitIR.

    ``wiring`` is an iterable of ``(component_id.pin_number, net_id)``
    tuples — same shape the protel parser produces.

    ``edge_semantics`` optionally maps ``(pin_id, net_id)`` to a dict that will
    be stored as ``metadata["edge_semantic"]`` on the corresponding Connection,
    emulating what EdgeSemanticEnricher produces.
    """
    edge_semantics = edge_semantics or {}
    connections = []
    for pin_ref, net_id in wiring:
        sem = edge_semantics.get((pin_ref, net_id))
        metadata = {"edge_semantic": sem} if sem is not None else {}
        connections.append(Connection(pin_id=pin_ref, net_id=net_id, metadata=metadata))
    return CircuitIR(
        meta=CircuitMeta(source=source, format="synthetic"),
        components=components,
        nets=nets,
        connections=connections,
        blocks=[],
        semantic_edges=[],
    )
