"""Net classifier enricher.

Classifies nets as ``gnd``, ``power``, ``signal``, ``clock`` or ``analog``
based on naming conventions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from circuit_graph.ir import CircuitIR, Net, NetType


@dataclass
class NetClassifier:
    """Classify nets by name heuristics."""

    name: str = "NetClassifier"
    priority: int = 20
    requires: list[str] | None = None

    def can_enrich(self, ir: CircuitIR) -> bool:
        return any(n.type is None for n in ir.nets)

    def enrich(self, ir: CircuitIR) -> CircuitIR:
        nets: list[Net] = []
        for net in ir.nets:
            if net.type is not None:
                nets.append(net)
                continue
            net_type, confidence = _classify_net(net.name)
            metadata = dict(net.metadata)
            if net_type is not NetType.UNKNOWN and confidence is not None:
                metadata["classification"] = {
                    "source": "name_heuristic",
                    "confidence": confidence,
                }
            nets.append(
                Net(
                    id=net.id,
                    name=net.name,
                    type=net_type,
                    metadata=metadata,
                )
            )
        return CircuitIR(
            meta=ir.meta,
            components=ir.components,
            nets=nets,
            connections=ir.connections,
            blocks=ir.blocks,
            semantic_edges=ir.semantic_edges,
            version=ir.version,
            metadata=ir.metadata,
        )


def _classify_net(name: str) -> tuple[NetType, str | None]:
    """Classify a net by name; returns (type, confidence).

    Confidence is ``None`` for unknown nets (callers omit classification
    metadata in that case), ``high`` for strong name conventions
    (GND families, voltage rails), ``medium`` for weaker heuristics.
    """
    upper = name.upper()

    if re.match(r"^(C?GND|PGND|AGND|DGND|SGND)", upper):
        return NetType.GND, "high"
    if re.search(r"\d+\.?\d*V", upper) or re.search(r"VBUS|VCC|VDD|VEE|VBAT|VIN", upper):
        return NetType.POWER, "high"
    if re.match(r"^(CC1|CC2|SBU1|SBU2|DP|DM|D\+|D-)$", upper):
        return NetType.SIGNAL, "medium"
    if re.search(r"CLK|OSC", upper):
        return NetType.CLOCK, "medium"
    if re.search(r"AIN|ADC|DAC|ANALOG", upper):
        return NetType.ANALOG, "medium"
    return NetType.UNKNOWN, None
