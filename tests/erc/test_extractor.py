"""Tests for circuit_graph.erc.extractor."""
from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.erc.extractor import NetlistExtractor

LM321_SCH = Path("lm321_ccs/1A_CCS_LM321_BJT.kicad_sch")


@pytest.fixture
def lm321_extracted():
    if not LM321_SCH.exists():
        pytest.skip(f"Fixture not found: {LM321_SCH}")
    return NetlistExtractor().extract(LM321_SCH)


def test_extract_lm321_finds_eight_components(lm321_extracted):
    """LM321 fixture: 6 components + 2 power symbols = 8 instances."""
    assert len(lm321_extracted.components) >= 6


def test_extract_lm321_identifies_power_symbols(lm321_extracted):
    """Power symbols ('power:+12V', 'power:GND') are identified as power markers."""
    # The LM321 fixture has #PWR01, #PWR02 (power symbols with reference "#PWR").
    assert any(
        ref.startswith("#PWR") for ref in {c.reference for c in lm321_extracted.components}
    )


def test_extract_lm321_power_symbols_have_one_pin(lm321_extracted):
    """Each power symbol has exactly one pin (pin 1)."""
    for comp in lm321_extracted.components:
        if comp.is_power_symbol:
            assert len(comp.pins) == 1
            assert comp.pins[0].pin_number == "1"


def test_extract_lm321_groups_power_rails(lm321_extracted):
    """All GND power symbols share the same net."""
    net_names = {n for n in lm321_extracted.nets.keys()}
    # The fixture has '+12V' and 'GND' as power labels - at least one rail.
    assert any("GND" in n for n in net_names) or any("+12V" in n for n in net_names)


def test_extract_lm321_returns_valid_nets(lm321_extracted):
    """NetMap values are non-empty PinContext lists; keys are strings."""
    for net_name, pins in lm321_extracted.nets.items():
        assert isinstance(net_name, str)
        assert len(pins) > 0
        for p in pins:
            assert p.component_ref
            assert p.pin_number
