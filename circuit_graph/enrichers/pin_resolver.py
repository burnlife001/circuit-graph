"""Pin-name resolver enricher.

Reads the SQLite knowledge base and fills in ``pin.name`` for every
non-passive component whose pin list came from the netlist with ``name=None``.

This enricher is read-only against the KB — it never invokes the chip resolver
(a slow, subprocess-based fallback).  Use :command:`cg kb resolve` for the
batch, side-effecting resolution pipeline; ``cg view`` only needs the KB
results to render useful pin labels like ``"1 (+IN)"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.ir import CircuitIR, DeviceKind
from circuit_graph.knowledge.db import ComponentDatabase


@dataclass
class PinNameResolver:
    """Look up ``pin.name`` for non-passive components in the KB.

    ``db_path`` defaults to the bundled ``circuit_graph.db`` next to
    ``db.py``.  Pass an explicit path to point at a different database
    (e.g. for testing).
    """

    name: str = "pin_name_resolver"
    # Run AFTER ValueNormalizer (priority 10) so that the
    # ``comp.value.raw`` fallback key is available; TopologyRecognizer (30)
    # and NetClassifier (20) don't need the resolved pin names.
    priority: int = 15
    requires: list[str] | None = field(default_factory=lambda: ["ValueNormalizer"])
    db_path: Path | str | None = None
    warnings: list[str] = field(default_factory=list, repr=False)

    def _open_db(self) -> ComponentDatabase:
        if self.db_path is None:
            return ComponentDatabase.default()
        return ComponentDatabase.open(self.db_path)

    def can_enrich(self, ir: CircuitIR) -> bool:
        for comp in ir.components:
            if comp.device_kind in _NON_PASSIVE_KINDS:
                if any(p.name is None for p in comp.pins):
                    return True
        return False

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        self.warnings = []
        try:
            db = self._open_db()
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"KB_OPEN_FAILED: {exc}")
            return ir

        from circuit_graph.knowledge.db import _replace_pin  # local for symmetry

        new_components = []
        for comp in ir.components:
            if comp.device_kind not in _NON_PASSIVE_KINDS:
                new_components.append(comp)
                continue
            pinout = None
            if comp.part_number:
                pinout = db.get_pinout(comp.part_number)
            if pinout is None and comp.value and getattr(comp.value, "raw", None):
                # Component dataclass stores value as NormalizedValue, but the
                # original netlist value lives in raw_value; try that as a
                # fallback search key.
                pinout = db.get_pinout(comp.value.raw)  # type: ignore[union-attr]
            if pinout is None and comp.raw_value:
                pinout = db.get_pinout(comp.raw_value)
            if pinout is None:
                new_components.append(comp)
                continue
            new_pins = []
            for pin in comp.pins:
                if pin.name is not None:
                    new_pins.append(pin)
                    continue
                name = pinout.get(str(pin.number)) or pinout.get(pin.number)
                if name is None:
                    new_pins.append(pin)
                else:
                    new_pins.append(_replace_pin(pin, name=name))
            try:
                comp.pins = new_pins  # type: ignore[attr-defined]
            except Exception:
                object.__setattr__(comp, "pins", new_pins)
            new_components.append(comp)

        # CircuitIR is frozen; reconstruct with replaced components.
        return CircuitIR(
            meta=ir.meta,
            components=new_components,
            nets=ir.nets,
            connections=ir.connections,
            blocks=ir.blocks,
            semantic_edges=ir.semantic_edges,
            version=ir.version,
            metadata=ir.metadata,
        )


_NON_PASSIVE_KINDS: frozenset[DeviceKind] = frozenset(
    {DeviceKind.IC, DeviceKind.MOSFET, DeviceKind.DIODE, DeviceKind.CONNECTOR}
)


__all__ = ["PinNameResolver"]


def _coerce_value(comp: Any) -> str | None:
    """Return a stringified raw value or ``None``."""
    val = getattr(comp, "value", None)
    if val is None:
        return None
    return getattr(val, "raw", None)
