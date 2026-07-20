"""Tests for DeckValidator."""

from __future__ import annotations

from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.spice_deck import ProbeDefinition, SimulationDeck


def test_valid_deck_passes():
    """A well-formed deck produces no diagnostics."""
    deck = SimulationDeck(
        name="test",
        netlist="R1 1 0 10k\nV1 1 0 DC 5",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
        probes=(ProbeDefinition(node="1", label="V1"),),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert len(diags) == 0


def test_missing_end_is_ok():
    """.END is auto-appended by to_spice(), so missing it is not an error."""
    deck = SimulationDeck(
        name="test",
        netlist="R1 1 0 10k",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert len(diags) == 0  # to_spice() handles .END


def test_empty_netlist_warns():
    """Empty netlist (no components) produces a warning."""
    deck = SimulationDeck(
        name="empty_test",
        netlist="",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert any("empty" in d.lower() for d in diags)


def test_no_analyses_warns():
    """Missing analyses produces a warning."""
    deck = SimulationDeck(
        name="no_analysis_test",
        netlist="R1 1 0 10k",
        analyses=(),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    assert any("analysis" in d.lower() for d in diags)


def test_floating_node_detected():
    """A node mentioned only once is flagged as potentially floating."""
    deck = SimulationDeck(
        name="floating_test",
        netlist="R1 floating_node 0 10k",
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    # floating_node appears once -> should warn
    assert any("floating_node" in d for d in diags)


def test_zero_resistance_loop_warns():
    """Zero-resistance loop (shorted voltage source) is flagged."""
    deck = SimulationDeck(
        name="short_test",
        netlist="V1 1 0 DC 5\nR1 1 0 0",
        stimuli=("V1 1 0 DC 5",),
        analyses=(".OP",),
    )
    validator = DeckValidator()
    diags = validator.validate(deck)
    has_zero_r = any(
        "0" in d and ("resistance" in d.lower() or "ohm" in d.lower())
        for d in diags
    )
    assert has_zero_r
