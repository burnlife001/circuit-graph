"""Tests for DeckBuilder — hardcoded LM321 proof-of-concept."""

from __future__ import annotations

import pytest

from circuit_graph.sim.deck_builder import DeckBuilder


def test_build_lm321_non_inverting_defaults():
    """Default values produce a valid deck."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    spice = deck.to_spice()
    assert "* LM321 Non-Inverting Amplifier" in spice
    assert "Rf out in_n 10k" in spice
    assert "Rg in_n 0 1k" in spice
    assert "VIN in_p 0 DC 0.5" in spice
    assert ".OP" in spice
    assert ".END" in spice


def test_build_lm321_non_inverting_custom_values():
    """Custom Rf, Rg, VCC are reflected in the deck."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting(rf="22k", rg="2k", vcc="12V")

    spice = deck.to_spice()
    assert "Rf out in_n 22k" in spice
    assert "Rg in_n 0 2k" in spice
    assert "VDC vcc 0 DC 12" in spice


def test_build_lm321_non_inverting_probes():
    """Deck includes probes for output and input nodes."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting()

    probe_nodes = {p.node for p in deck.probes}
    assert "out" in probe_nodes
    assert "in_p" in probe_nodes


def test_deck_has_expected_gain_range():
    """With Rf=10k, Rg=1k, expected gain is ~11."""
    builder = DeckBuilder()
    deck = builder.build_lm321_non_inverting(rf="10k", rg="1k")

    out_probe = next(p for p in deck.probes if p.node == "out")
    assert out_probe.expected is not None
    expected = dict(out_probe.expected)
    assert expected["gain_min"] == pytest.approx(9.0, rel=0.1)
    assert expected["gain_max"] == pytest.approx(13.0, rel=0.1)
