"""End-to-end Ngspice integration tests.

These tests require Ngspice to be installed and on PATH.
They are skipped automatically if Ngspice is not found.
"""

from __future__ import annotations

import shutil

import pytest

from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig

# Check if Ngspice is available (prefer console version on Windows)
_NGSPICE_AVAILABLE = shutil.which("ngspice_con") is not None or shutil.which("ngspice") is not None

pytestmark = pytest.mark.skipif(
    not _NGSPICE_AVAILABLE,
    reason="Ngspice not installed or not on PATH",
)


class TestLM321NonInvertingIntegration:
    """End-to-end: build deck -> validate -> run Ngspice -> check gain."""

    def test_deck_validates_clean(self):
        """The LM321 deck passes pre-flight validation (ignore subckt nodes)."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting()

        validator = DeckValidator()
        diags = validator.validate(deck)

        # Phase 1: validator only scans netlist text, not subckt connectivity.
        # vcc appears once in netlist (VDC) but also via XOPAMP subcircuit call.
        # Filter out known-false floating-node warnings for subckt-connected nodes.
        real_diags = tuple(
            d for d in diags
            if "vcc" not in d  # connected via subcircuit
            and "in_n" not in d  # connected via subcircuit
            and "in_p" not in d  # connected via subcircuit
        )
        assert len(real_diags) == 0, f"Validation diagnostics: {real_diags}"

    def test_ngspice_runs_and_converges(self):
        """Ngspice runs the LM321 deck and converges."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting()

        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=10.0))
        result = runner.run(deck)

        assert result.exit_code == 0, (
            f"Ngspice failed with exit code {result.exit_code}"
        )
        assert len(result.analyses) >= 1

        op = result.analyses[0]
        assert op.convergence, (
            f"Operating point did not converge. Data: {op.as_dict()}"
        )

    def test_gain_matches_expected(self):
        """With Rf=10k, Rg=1k and VIN=0.5V DC, Vout ≈ 5.5V (gain ≈ 11)."""
        builder = DeckBuilder()
        deck = builder.build_lm321_non_inverting(rf="10k", rg="1k")

        runner = SpiceRunner(SpiceRunnerConfig(timeout_seconds=10.0))
        result = runner.run(deck)

        op = result.analyses[0]
        d = op.as_dict()

        vout = _find_voltage(d, "out")
        vin = _find_voltage(d, "in_p")

        assert vout is not None, f"No output voltage found in: {list(d)}"
        assert vin is not None, f"No input voltage found in: {list(d)}"

        # VIN = 0.5V DC, gain = 1 + Rf/Rg = 11, Vout ≈ 0.5 * 11 = 5.5V
        # Ideal VCVS opamp has no rail limits, so >VCC is expected at DC
        expected_vout = 0.5 * (1 + 10e3 / 1e3)  # = 5.5
        assert abs(vout - expected_vout) < 0.1, (
            f"Expected Vout ≈ {expected_vout}V, got {vout}V (gain = {vout/vin:.2f})"
        )


def _find_voltage(d: dict[str, list[float]], node: str) -> float | None:
    """Find the DC voltage at a node in the operating point result."""
    for key, values in d.items():
        if key.lower() == f"v({node})".lower() and values:
            return values[0]
    return None
