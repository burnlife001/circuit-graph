"""Instantiate a primitive template into a concrete CircuitIR.

The instantiated IR is a real, serialisable circuit: components carry resolved
values, nets are named, pins and connections follow the canonical id formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from circuit_graph.design.expr import ExpressionError, evaluate, expr_names, parse_number
from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    CircuitMeta,
    Component,
    Connection,
    DeviceKind,
    InductanceValue,
    Net,
    NormalizedValue,
    Pin,
    Provenance,
    ResistanceValue,
    make_pin_id,
)
from circuit_graph.primitives import PrimitiveTemplate

_KIND_BY_TYPE = {kind.value: kind for kind in DeviceKind}

_DESIGNATOR_PREFIX = {
    DeviceKind.RESISTOR: "R",
    DeviceKind.CAPACITOR: "C",
    DeviceKind.INDUCTOR: "L",
    DeviceKind.DIODE: "D",
    DeviceKind.MOSFET: "Q",
    DeviceKind.IC: "U",
    DeviceKind.BRIDGE_RECTIFIER: "BR",
    DeviceKind.UNKNOWN: "U",
}


class DesignError(ValueError):
    """Raised when a design spec cannot be instantiated."""


@dataclass(frozen=True)
class DesignSpec:
    """Designer-provided values for instantiating a primitive."""

    inputs: dict[str, float] = field(default_factory=dict)
    nets: dict[str, str] = field(default_factory=dict)
    designators: dict[str, str] = field(default_factory=dict)
    start_index: int = 1


@dataclass(frozen=True)
class DesignResult:
    ir: CircuitIR
    resolved: dict[str, float]
    warnings: list[str] = field(default_factory=list)


def instantiate(template: PrimitiveTemplate, spec: DesignSpec) -> DesignResult:
    """Resolve ``template`` parameters against ``spec`` and build a CircuitIR."""
    inputs = _gather_inputs(template, spec)
    resolved = _resolve_parameters(template.parameters, inputs)
    ir = _build_ir(template, spec, resolved)
    return DesignResult(ir=ir, resolved=resolved)


def _gather_inputs(template: PrimitiveTemplate, spec: DesignSpec) -> dict[str, float]:
    inputs = dict(spec.inputs)
    for design_input in template.design_inputs:
        if design_input.id in inputs:
            continue
        if design_input.default is not None:
            inputs[design_input.id] = parse_number(str(design_input.default))
        else:
            raise DesignError(f"Missing required design input: {design_input.id!r}")
    return inputs


def _resolve_parameters(parameters: dict[str, str], inputs: dict[str, float]) -> dict[str, float]:
    """Resolve parameter expressions in dependency order (topological, cycle-safe)."""
    bound = dict(inputs)
    resolved: dict[str, float] = {}
    pending = dict(parameters)
    while pending:
        progress = False
        for key, expr in list(pending.items()):
            names = expr_names(expr)
            if (
                any(
                    name in pending or any(k.startswith(f"{name}.") for k in pending)
                    for name in names
                )
                or expr in pending
            ):
                continue
            try:
                value = evaluate(expr, bound)
            except ZeroDivisionError as exc:
                raise DesignError(f"Division by zero in parameter {key!r}") from exc
            except ExpressionError as exc:
                raise DesignError(f"Invalid expression for {key!r}: {exc}") from exc
            bound[key] = value
            resolved[key] = value
            del pending[key]
            progress = True
        if not progress:
            raise DesignError(f"Circular parameter dependency: {', '.join(sorted(pending))}")
    return resolved


def _to_device_kind(comp_type: str | tuple[str, ...]) -> DeviceKind:
    candidates = (comp_type,) if isinstance(comp_type, str) else comp_type
    for candidate in candidates:
        if candidate in _KIND_BY_TYPE:
            return _KIND_BY_TYPE[candidate]
    raise DesignError(f"No DeviceKind mapping for component type {comp_type!r}")


def _make_value(kind: DeviceKind, magnitude: float | None) -> NormalizedValue | None:
    if magnitude is None:
        return None
    if kind is DeviceKind.RESISTOR:
        return ResistanceValue(magnitude=magnitude, unit="Ω")
    if kind is DeviceKind.CAPACITOR:
        return CapacitanceValue(magnitude=magnitude, unit="µF")
    if kind is DeviceKind.INDUCTOR:
        return InductanceValue(magnitude=magnitude, unit="µH")
    return None


def _build_ir(
    template: PrimitiveTemplate, spec: DesignSpec, resolved: dict[str, float]
) -> CircuitIR:
    kinds: dict[str, DeviceKind] = {}
    designators: dict[str, str] = {}
    for index, comp in enumerate(template.components):
        kind = _to_device_kind(comp.type)
        kinds[comp.id] = kind
        designators[comp.id] = spec.designators.get(
            comp.id, f"{_DESIGNATOR_PREFIX[kind]}{spec.start_index + index}"
        )

    net_names = {
        sig.id: spec.nets.get(sig.id, sig.id.upper()) for sig in template.signals
    }

    components: list[Component] = []
    connections: list[Connection] = []
    for comp in template.components:
        comp_id = designators[comp.id]
        magnitude = resolved.get(f"{comp.id}.value")
        pins = [
            Pin(
                id=make_pin_id(comp_id, pin.number),
                number=str(pin.number),
                net_id=net_names[pin.net],
            )
            for pin in comp.pins
        ]
        for pin in comp.pins:
            connections.append(
                Connection(
                    pin_id=make_pin_id(comp_id, pin.number),
                    net_id=net_names[pin.net],
                    provenance=Provenance.MANUAL,
                )
            )
        components.append(
            Component(
                id=comp_id,
                device_kind=kinds[comp.id],
                value=_make_value(kinds[comp.id], magnitude),
                raw_value=_format_raw(magnitude) if magnitude is not None else None,
                pins=pins,
                metadata={"primitive": template.id, "primitive_component": comp.id},
            )
        )

    nets = [Net(id=name, name=name) for name in dict.fromkeys(net_names.values())]
    return CircuitIR(
        meta=CircuitMeta(source=template.id, format="primitive-design"),
        components=components,
        nets=nets,
        connections=connections,
    )


def _format_raw(magnitude: float) -> str:
    """Compact engineering-ish string for the .NET value line, e.g. 14000.0 -> '14k'."""
    if magnitude >= 1e6:
        return f"{magnitude / 1e6:g}M"
    if magnitude >= 1e3:
        return f"{magnitude / 1e3:g}k"
    return f"{magnitude:g}"
