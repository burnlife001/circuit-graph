"""SpiceResult and AnalysisResult — immutable simulation output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck


@dataclass(frozen=True)
class AnalysisResult:
    """Result of a single SPICE analysis (.OP, .AC, .TRAN, etc.).

    Args:
        kind: Analysis type — ``"op"``, ``"ac"``, ``"tran"``, ``"dc"``, ``"noise"``.
        variables: Column names in the order they appear.
        data: Row-major data matrix — each inner tuple is one row.
        convergence: Whether the analysis converged.
        iterations: Number of iterations to convergence (DC analyses only).
    """

    kind: str
    variables: tuple[str, ...]
    data: tuple[tuple[float, ...], ...]
    convergence: bool
    iterations: int | None = None

    def as_dict(self) -> dict[str, list[float]]:
        """Convert row-major data to column-major dict.

        Returns a dict mapping each variable name to its list of values,
        suitable for plotting or JSON export.
        """
        if not self.variables or not self.data:
            return {}
        columns: dict[str, list[float]] = {v: [] for v in self.variables}
        for row in self.data:
            for i, value in enumerate(row):
                columns[self.variables[i]].append(value)
        return columns


@dataclass(frozen=True)
class SpiceResult:
    """Complete result of running a SimulationDeck.

    Args:
        deck: The deck that produced this result.
        exit_code: Ngspice process exit code (0 = success).
        timestamp: ISO 8601 timestamp of the run.
        analyses: Results for each analysis in the deck.
    """

    deck: "SimulationDeck"
    exit_code: int
    timestamp: str
    analyses: tuple[AnalysisResult, ...]
