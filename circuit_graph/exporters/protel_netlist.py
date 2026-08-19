"""Protel .NET netlist writer — the inverse of circuit_graph/parsers/protel.py.

Component blocks::

    [
    R1
    0603
    14k



    ]

Net blocks::

    (
    VIN
    R1-1
    )

Output is UTF-8 with LF line endings; the parser's encoding auto-detection
reads it back without conversion.
"""

from __future__ import annotations

from pathlib import Path

from circuit_graph.ir import CircuitIR, NormalizedValue


def format_value(value: NormalizedValue | None, raw_value: str | None) -> str:
    """Return the third line of a .NET component block."""
    if raw_value:
        return raw_value
    if value is None:
        return ""
    return f"{value.magnitude:g}{value.unit}"


def write_protel_netlist(ir: CircuitIR, path: Path | str) -> None:
    """Write ``ir`` as a Protel .NET netlist file at ``path``."""
    lines: list[str] = []
    for comp in ir.components:
        lines.append("[")
        lines.append(comp.id)
        lines.append(comp.footprint or "")
        lines.append(format_value(comp.value, comp.raw_value))
        lines.extend(["", "", ""])
        lines.append("]")
    for net in ir.nets:
        lines.append("(")
        lines.append(net.name)
        for conn in ir.connections:
            if conn.net_id != net.id:
                continue
            comp_id, _, pin_num = conn.pin_id.rpartition(".")
            lines.append(f"{comp_id}-{pin_num}")
        lines.append(")")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
