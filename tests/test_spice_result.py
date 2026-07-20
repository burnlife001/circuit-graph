"""Tests for SpiceResult and AnalysisResult data models."""

from __future__ import annotations

from circuit_graph.sim.spice_deck import SimulationDeck
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult


def _minimal_deck() -> SimulationDeck:
    return SimulationDeck(name="test")


def test_analysis_result_as_dict():
    """as_dict() converts row-major data to column-major dict."""
    result = AnalysisResult(
        kind="op",
        variables=("v(1)", "v(2)", "i(v1)"),
        data=(
            (1.0, 2.0, 0.001),
            (1.1, 2.1, 0.002),
        ),
        convergence=True,
        iterations=42,
    )

    d = result.as_dict()
    assert d["v(1)"] == [1.0, 1.1]
    assert d["v(2)"] == [2.0, 2.1]
    assert d["i(v1)"] == [0.001, 0.002]


def test_spice_result_immutable():
    """SpiceResult is frozen."""
    result = SpiceResult(
        deck=_minimal_deck(),
        exit_code=0,
        timestamp="2026-07-09T00:00:00",
        analyses=(),
    )
    try:
        result.exit_code = 1  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass


def test_analysis_result_single_row():
    """Single-row analysis (e.g. .OP) works correctly."""
    result = AnalysisResult(
        kind="op",
        variables=("v(out)",),
        data=((5.0,),),
        convergence=True,
    )
    assert result.as_dict() == {"v(out)": [5.0]}


def test_analysis_result_empty():
    """Empty data edge case."""
    result = AnalysisResult(
        kind="ac",
        variables=(),
        data=(),
        convergence=False,
    )
    assert result.as_dict() == {}
