"""SpiceRunner — Ngspice process management and output parsing.

Launches Ngspice as a subprocess, feeds it a :class:`SimulationDeck`,
and parses the stdout into a structured :class:`SpiceResult`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from circuit_graph.sim.convergence import ConvergenceConfig
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult
from circuit_graph.sim._win32_dialog import NgspiceDialogSuppressor

if TYPE_CHECKING:
    from circuit_graph.sim.spice_deck import SimulationDeck


class SpiceRunnerError(Exception):
    """Raised when Ngspice execution fails at the process level."""


@dataclass(frozen=True)
class SpiceRunnerConfig:
    """Configuration for :class:`SpiceRunner`.

    Args:
        ngspice_bin: Path or name of the Ngspice executable.
        timeout_seconds: Maximum wall-clock time per simulation.
        tmp_dir: Directory for temporary deck files (default: system temp).
    """

    ngspice_bin: str = "ngspice_con"
    timeout_seconds: float = 30.0
    tmp_dir: Path | None = None


# Sentinel import guard — ConvergenceConfig is used here but defined in convergence.py.
# This is fine for Phase 1 since both modules are in the same package.
from circuit_graph.sim.convergence import ConvergenceConfig  # noqa: E402


class SpiceRunner:
    """Execute SPICE simulations via Ngspice subprocess."""

    def __init__(
        self,
        config: SpiceRunnerConfig | None = None,
        convergence: ConvergenceConfig | None = None,
    ):
        self.config = config or SpiceRunnerConfig()
        self.convergence = convergence or ConvergenceConfig()

    def run(self, deck: "SimulationDeck") -> "SpiceResult":
        """Run a SimulationDeck through Ngspice and return structured results.

        Convergence retry is automatic: if the first run fails to converge,
        the deck is re-run with progressively relaxed tolerances up to
        ``convergence.max_retries`` times.

        Args:
            deck: The simulation deck to execute.

        Returns:
            A :class:`SpiceResult` with parsed analyses.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        for attempt in range(self.convergence.max_retries + 1):
            retry_deck = self.convergence.apply_to_deck(deck, retry_attempt=attempt)
            raw_output, returncode = self._run_ngspice(retry_deck)
            analyses = self._parse_output(raw_output, retry_deck)
            actual_exit = 1 if returncode != 0 else 0

            if analyses and all(a.convergence for a in analyses):
                return SpiceResult(
                    deck=retry_deck,
                    exit_code=actual_exit,
                    timestamp=timestamp,
                    analyses=analyses,
                )
            if attempt == self.convergence.max_retries:
                # Last attempt — return whatever we got
                return SpiceResult(
                    deck=retry_deck,
                    exit_code=actual_exit,
                    timestamp=timestamp,
                    analyses=analyses,
                )

        # Should not reach here
        return SpiceResult(
            deck=deck,
            exit_code=1,
            timestamp=timestamp,
            analyses=(),
        )

    def _run_ngspice(self, deck: "SimulationDeck") -> tuple[str, int]:
        """Write deck to temp file, launch Ngspice, return (stdout, returncode)."""
        spice_text = deck.to_spice()

        tmp_dir = self.config.tmp_dir
        if tmp_dir is not None:
            tmp_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".cir",
            prefix="cg_sim_",
            dir=str(tmp_dir) if tmp_dir else None,
            encoding="utf-8",
            delete=False,
        ) as f:
            f.write(spice_text)
            tmp_path = Path(f.name)

        try:
            with NgspiceDialogSuppressor():
                result = subprocess.run(
                    [self.config.ngspice_bin, "-b", str(tmp_path)],
                    capture_output=True,
                    encoding="utf-8",
                    timeout=self.config.timeout_seconds,
                )
            return result.stdout, result.returncode
        except subprocess.TimeoutExpired:
            return "Ngspice timeout", -1
        except FileNotFoundError:
            raise SpiceRunnerError(
                f"Ngspice not found at '{self.config.ngspice_bin}'. "
                "Install Ngspice or set ngspice_bin path."
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_output(
        self,
        raw: str,
        deck: "SimulationDeck",
    ) -> tuple[AnalysisResult, ...]:
        """Parse Ngspice stdout into structured AnalysisResults."""
        convergence = raw != "Ngspice timeout" and not _has_convergence_failure(raw)

        # Try .OP parsing first — look for operating point table
        op_result = _parse_op_block(raw)
        if op_result is not None:
            variables = tuple(f"v({v})" for v in op_result[0]) + tuple(
                f"i({v})" for v in op_result[1]
            )
            try:
                values = tuple(float(v) for v in op_result[2] + op_result[3])
            except ValueError:
                values = ()
                convergence = False
            return (
                AnalysisResult(
                    kind="op",
                    variables=variables if values else (),
                    data=(values,) if values else (),
                    convergence=convergence,
                ),
            )

        # No structured data found — return minimal result
        return (
            AnalysisResult(
                kind="op",
                variables=(),
                data=(),
                convergence=convergence,
            ),
        )


# ---------------------------------------------------------------------------
# Output parsing helpers
# ---------------------------------------------------------------------------

_NON_CONVERGENCE_PATTERNS = (
    re.compile(r"iteration limit reached", re.IGNORECASE),
    re.compile(r"run simulation\(s\) aborted", re.IGNORECASE),
    re.compile(r"no convergence", re.IGNORECASE),
    re.compile(r"timestep too small", re.IGNORECASE),
    re.compile(r"gmin stepping failed", re.IGNORECASE),
    re.compile(r"source stepping failed", re.IGNORECASE),
)


def _has_convergence_failure(raw: str) -> bool:
    """Check if raw output contains any non-convergence indicator."""
    if not raw.strip():
        return True
    return any(p.search(raw) for p in _NON_CONVERGENCE_PATTERNS)


# Regex matching a column-separator line (e.g. "----     -------").
# This starts with 2+ dashes followed by whitespace, which distinguishes it
# from section underlines like "--------------------------" that have no spaces.
_COL_SEP_RE = re.compile(r"^-{2,}\s")

# Regex for SPICE numeric values: scientific notation, with optional sign
_NUMERIC_RE = re.compile(r"^[+-]?\d+\.?\d*(?:[eE][+-]?\d+)?$")


def _is_numeric(s: str) -> bool:
    """Check if a string looks like a SPICE numeric value (e.g. 5.000e+00)."""
    return bool(_NUMERIC_RE.match(s))


def _parse_op_block(
    raw: str,
) -> tuple[list[str], list[str], list[str], list[str]] | None:
    """Parse Ngspice .OP output into (node_names, source_names, node_voltages, source_currents).

    Returns None if no operating point block is found.
    """
    # Find the operating point section
    # Ngspice outputs one of these headers before voltage/current tables:
    #   "**** OPERATING POINT INFORMATION"  (regular ngspice)
    #   "Initial Transient Solution"         (alt format)
    #   "Doing analysis at TEMP"             (ngspice_con on Windows)
    lines = raw.splitlines()
    start_idx: int | None = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if (
            "operating point" in lower
            or "initial transient solution" in lower
            or "doing analysis at temp" in lower
        ):
            start_idx = i
            break

    if start_idx is None:
        return None

    node_names: list[str] = []
    source_names: list[str] = []
    node_voltages: list[str] = []
    source_currents: list[str] = []

    in_table = False
    prev_was_data = False
    for line in lines[start_idx:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Column separator is a line starting with dashes followed by whitespace
        # (distinct from section underlines which are all dashes with no spaces)
        if _COL_SEP_RE.match(stripped):
            in_table = True
            prev_was_data = False
            continue
        if not in_table:
            continue

        # Table rows: "node_name    value"
        parts = stripped.split()
        if len(parts) < 2:
            continue

        name = parts[0]
        value = parts[1]

        # Section headers like "Resistor models (Simple...)" are not table data
        # Figure out if this row is data by checking if value looks numeric
        if not _is_numeric(value):
            # This might be a section header after the table — stop
            if prev_was_data and not _is_numeric(name):
                break
            continue

        # Skip non-data lines
        if not name or name.startswith("*"):
            continue

        if name.endswith("#branch"):
            source_names.append(name.replace("#branch", ""))
            source_currents.append(value)
        else:
            node_names.append(name)
            node_voltages.append(value)
        prev_was_data = True

    if not node_names and not source_names:
        return None

    return node_names, source_names, node_voltages, source_currents
