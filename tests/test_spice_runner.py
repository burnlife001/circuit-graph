"""Tests for SpiceRunner — unit tests for parser, skip Ngspice integration."""

from __future__ import annotations

import pytest
from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig


def _minimal_deck() -> SimulationDeck:
    return SimulationDeck(
        name="test",
        netlist="V1 1 0 DC 5\nR1 1 0 10k",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
    )


class TestParseOpOutput:
    """Unit tests for .OP output parsing — no Ngspice process needed."""

    def test_parse_simple_op(self):
        """Parse a typical .OP output block."""
        raw = """\
**** OPERATING POINT INFORMATION
Node     Voltage
----     -------
1               5.000000e+00
v1#branch       5.000000e-04
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) == 1
        op_result = result[0]
        assert op_result.kind == "op"
        assert op_result.convergence is True
        assert "v(1)" in op_result.variables
        assert "i(v1)" in op_result.variables
        assert op_result.data[0][0] == pytest.approx(5.0)

    def test_parse_op_alternate_format(self):
        """Parse .OP output with 'Voltage' header format."""
        raw = """\
Initial Transient Solution
--------------------------

Node     Voltage
----     -------
out             1.09999e+01
in_p            1.00000e+00
v1#branch       5.00000e-04
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) >= 1
        op = result[0]
        d = op.as_dict()
        assert "v(out)" in d

    def test_non_convergence_detected(self):
        """Non-convergence message is detected."""
        raw = """\
doAnalyses: iteration limit reached
run simulation(s) aborted
Error: no such vector v(out)
"""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output(raw, _minimal_deck())

        assert len(result) >= 1
        assert result[0].convergence is False

    def test_empty_output(self):
        """Empty stdout returns empty analyses."""
        runner = SpiceRunner(SpiceRunnerConfig())
        result = runner._parse_output("", _minimal_deck())
        assert len(result) == 1
        assert result[0].convergence is False
        assert result[0].data == ()
