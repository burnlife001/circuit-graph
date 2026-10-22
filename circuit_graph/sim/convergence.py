"""Convergence configuration and retry logic for Ngspice.

Provides a frozen :class:`ConvergenceConfig` that applies tolerance options
to a :class:`~circuit_graph.sim.spice_deck.SimulationDeck` and a three-level
retry strategy: relax tolerances → switch to Gear → limit integration order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck

_DEFAULT_RETRY_OPTIONS: tuple[tuple[tuple[str, str], ...], ...] = (
    # Retry 1: relax tolerances
    (("gmin", "1e-10"), ("abstol", "1e-10")),
    # Retry 2: switch to Gear method
    (("gmin", "1e-9"), ("abstol", "1e-9"), ("method", "gear")),
    # Retry 3: limit integration order
    (("gmin", "1e-8"), ("abstol", "1e-8"), ("method", "gear"), ("maxord", "2")),
)


@dataclass(frozen=True)
class ConvergenceConfig:
    """Ngspice convergence parameters and retry strategy.

    Args:
        gmin: Minimum conductance shunt (S).  Default 1e-12.
        abstol: Absolute current tolerance (A).  Default 1e-12.
        reltol: Relative tolerance.  Default 1e-3.
        max_retries: Maximum convergence retry attempts.  Default 3.
        retry_options: Per-retry option overrides as ``((name, value), ...)``.
    """

    gmin: float = 1e-12
    abstol: float = 1e-12
    reltol: float = 1e-3
    max_retries: int = 3
    retry_options: tuple[tuple[tuple[str, str], ...], ...] = _DEFAULT_RETRY_OPTIONS

    def apply_to_deck(
        self,
        deck: "SimulationDeck",
        *,
        retry_attempt: int = 0,
    ) -> "SimulationDeck":
        """Return a new SimulationDeck with convergence options applied.

        On ``retry_attempt=0``, the base tolerance options are used.
        On subsequent attempts, relaxed options from ``retry_options`` are
        layered on top.  When ``retry_attempt`` exceeds ``max_retries``,
        the last available option set is reused.
        """
        options: dict[str, str] = {
            "GMIN": f"{self.gmin}",
            "ABSTOL": f"{self.abstol}",
            "RELTOL": f"{self.reltol}",
        }

        if retry_attempt > 0 and self.retry_options:
            idx = min(retry_attempt - 1, len(self.retry_options) - 1)
            for name, value in self.retry_options[idx]:
                options[name.upper()] = value

        option_lines = (
            f".OPTIONS {' '.join(f'{k}={v}' for k, v in options.items())}",
        )

        # Merge with existing options (originals first, converge opts last —
        # Ngspice last-write-wins)
        merged_options = deck.options + option_lines

        return type(deck)(
            name=deck.name,
            includes=deck.includes,
            models=deck.models,
            subckts=deck.subckts,
            netlist=deck.netlist,
            stimuli=deck.stimuli,
            analyses=deck.analyses,
            probes=deck.probes,
            options=merged_options,
            diagnostics=deck.diagnostics,
        )
