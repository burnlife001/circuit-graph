"""SPICE deck data model — immutable, self-rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class ProbeDefinition:
    """A measurement point in a SPICE simulation.

    Args:
        node: SPICE node or expression to probe, e.g. ``"vout"`` or ``"i(r1)"``.
        label: Human-readable label for this probe.
        expected: Optional expected value ranges, e.g. ``(("gain_min", 1.0), ("gain_max", 100.0))``.
    """

    node: str
    label: str
    expected: tuple[tuple[str, float], ...] | None = None

    def to_print(self, analysis_kind: str) -> str:
        """Render as a .PRINT line for the given analysis kind."""
        if self.node.startswith("i(") or self.node.startswith("I("):
            expr = f"I({self.node[2:-1]})"
        else:
            expr = f"V({self.node})"
        return f".PRINT {analysis_kind} {expr}"


@dataclass(frozen=True)
class SimulationDeck:
    """An immutable, self-contained SPICE simulation deck.

    All collection fields use tuples for immutability.  Call ``to_spice()``
    to render the complete deck text ready for Ngspice.
    """

    name: str
    includes: tuple[Path, ...] = ()
    models: tuple[str, ...] = ()
    subckts: tuple[str, ...] = ()
    netlist: str = ""
    stimuli: tuple[str, ...] = ()
    analyses: tuple[str, ...] = ()
    probes: tuple[ProbeDefinition, ...] = ()
    options: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_spice(self) -> str:
        """Render the complete SPICE deck as a string."""
        lines: list[str] = []

        # Title line
        lines.append(f"* {self.name}")
        lines.append("")

        # Includes
        for inc in self.includes:
            lines.append(f'.include "{inc.as_posix()}"')

        # .MODEL definitions
        for model in self.models:
            lines.append(model)

        # .SUBCKT instantiations
        for sub in self.subckts:
            lines.append(sub)

        # Options
        for opt in self.options:
            lines.append(opt)

        lines.append("")
        lines.append("* --- Netlist ---")

        # Netlist body
        for line in self.netlist.strip().splitlines():
            lines.append(line.rstrip())

        # Stimuli
        if self.stimuli:
            lines.append("")
            lines.append("* --- Stimuli ---")
            for stim in self.stimuli:
                lines.append(stim)

        # Analyses
        lines.append("")
        lines.append("* --- Analyses ---")
        for analysis in self.analyses:
            lines.append(analysis)

        # Probes (.PRINT lines)
        if self.probes:
            lines.append("")
            lines.append("* --- Probes ---")
            # Determine analysis kind for .PRINT from first analysis
            analysis_kind = _analysis_kind_short(self.analyses[0] if self.analyses else "OP")
            for probe in self.probes:
                lines.append(probe.to_print(analysis_kind))

        # End
        lines.append("")
        lines.append(".END")
        lines.append("")

        return "\n".join(lines)


def _analysis_kind_short(analysis_line: str) -> str:
    """Extract the short kind string from a SPICE analysis line."""
    upper = analysis_line.strip().upper()
    if upper.startswith(".AC"):
        return "AC"
    if upper.startswith(".TRAN"):
        return "TRAN"
    if upper.startswith(".DC"):
        return "DC"
    if upper.startswith(".NOISE"):
        return "NOISE"
    if upper.startswith(".TF"):
        return "TF"
    return "DC"  # .OP and others default to DC print
