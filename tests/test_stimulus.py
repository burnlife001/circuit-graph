"""Tests for StimulusSelector fallback table."""

from __future__ import annotations

from circuit_graph.sim.stimulus import StimulusSelector


def test_known_category_returns_default_stimulus():
    """Known topology categories have default stimuli."""
    selector = StimulusSelector()

    stimuli = selector.select("opamp_amplifier", node="in_p")

    assert any("AC" in s for s in stimuli)
    assert any("in_p" in s for s in stimuli)
    assert any("1.0" in s for s in stimuli)


def test_dcdc_converter_returns_dc_stimulus():
    """DCDC converters get DC + transient load step."""
    selector = StimulusSelector()

    stimuli = selector.select("dcdc_converter", node="vin")

    assert any("DC" in s for s in stimuli)
    assert any("vin" in s for s in stimuli)


def test_oscillator_returns_no_stimulus():
    """Oscillators are self-starting — no stimulus."""
    selector = StimulusSelector()

    stimuli = selector.select("oscillator", node="out")

    assert len(stimuli) == 0


def test_unknown_category_returns_empty():
    """Unknown category returns empty tuple."""
    selector = StimulusSelector()

    stimuli = selector.select("nonexistent_category", node="test")

    assert stimuli == ()


def test_default_analyses_for_opamp():
    """Opamp topologies default to .OP + .AC."""
    selector = StimulusSelector()

    analyses = selector.default_analyses("opamp_amplifier")

    assert ".OP" in analyses
    assert any("AC" in a for a in analyses)


def test_default_analyses_for_unknown():
    """Unknown category defaults to .OP only."""
    selector = StimulusSelector()

    analyses = selector.default_analyses("unknown_thing")

    assert analyses == (".OP",)
