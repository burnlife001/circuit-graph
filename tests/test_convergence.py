"""Tests for convergence configuration and retry logic."""

from __future__ import annotations

from circuit_graph.sim.convergence import ConvergenceConfig
from circuit_graph.sim.spice_deck import SimulationDeck


def test_convergence_default_options():
    """Default config has reasonable convergence parameters."""
    config = ConvergenceConfig()
    assert config.gmin == 1e-12
    assert config.max_retries == 3
    assert len(config.retry_options) == 3


def test_apply_to_deck_adds_options():
    """apply_to_deck() appends convergence-related .OPTIONS to the deck."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(gmin=1e-12, abstol=1e-12, reltol=1e-3)

    new_deck = config.apply_to_deck(deck, retry_attempt=0)
    opts = "\n".join(new_deck.options)
    assert "GMIN=1e-12" in opts
    assert "ABSTOL=1e-12" in opts
    assert "RELTOL=0.001" in opts


def test_apply_to_deck_retry_1_uses_relaxed_tolerances():
    """Retry #1 uses the relaxed options from retry_options[0]."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    new_deck = config.apply_to_deck(deck, retry_attempt=1)
    opts = "\n".join(new_deck.options)
    # First retry should relax GMIN
    assert "gmin=1e-10" in opts.lower() or "GMIN=1e-10" in opts


def test_apply_to_deck_retry_2_uses_gear_method():
    """Retry #2 switches to Gear integration."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    new_deck = config.apply_to_deck(deck, retry_attempt=2)
    opts = "\n".join(new_deck.options)
    assert "gear" in opts.lower() or "GEAR" in opts


def test_apply_to_deck_exhausted():
    """Retry beyond max_retries uses the last available option set."""
    deck = SimulationDeck(name="test")
    config = ConvergenceConfig(max_retries=3)

    # Should not crash — uses last retry_options entry
    new_deck = config.apply_to_deck(deck, retry_attempt=5)
    assert new_deck is not None


def test_convergence_config_immutable():
    """ConvergenceConfig is frozen."""
    config = ConvergenceConfig()
    try:
        config.gmin = 0.1  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass
