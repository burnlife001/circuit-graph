"""Tests for multi-source topology match scoring."""

from __future__ import annotations

import pytest

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import load_primitives
from circuit_graph.scoring import TopologyScorer
from circuit_graph.vf2_matcher import TopologyMatch
from tests.fir_helpers import build_ir, make_component, make_net


def _make_match(
    *,
    cc_name: str = "CC1",
    gnd_name: str = "GND",
    value: str = "5.1K",
) -> tuple[TopologyMatch, dict[str, object], dict[str, object]]:
    """Return a scored match plus component/net maps for the scorer."""
    r = make_component(
        "R1",
        DeviceKind.RESISTOR,
        [("1", cc_name), ("2", gnd_name)],
        raw_value=value,
    )
    cc = make_net("N_CC", cc_name, NetType.SIGNAL)
    gnd = make_net("N_GND", gnd_name, NetType.GND)
    ir = build_ir(
        [r],
        [cc, gnd],
        [("R1.1", "N_CC"), ("R1.2", "N_GND")],
    )
    match = TopologyMatch(
        primitive_id="type-c-cc-pull-down",
        component_mapping={"R_pull": "R1"},
        signal_mapping={"cc": "N_CC", "gnd": "N_GND"},
        evidence=[],
    )
    return match, {c.id: c for c in ir.components}, {n.id: n for n in ir.nets}


@pytest.fixture
def cc_template():
    return next(t for t in load_primitives() if t.id == "type-c-cc-pull-down")


def test_score_perfect_match_is_high_confidence(cc_template) -> None:
    """A fully-aligned match receives a fused score of 1.0 and high confidence."""
    match, comp_map, net_map = _make_match()
    scorer = TopologyScorer(comp_map, net_map)
    breakdown = scorer.score(match, cc_template)

    assert breakdown.structural == pytest.approx(1.0)
    assert breakdown.naming == pytest.approx(1.0)
    assert breakdown.parametric == pytest.approx(1.0)
    assert breakdown.type == pytest.approx(1.0)
    assert breakdown.fused == pytest.approx(1.0)
    assert breakdown.confidence == "high"


def test_score_wrong_value_lowers_parametric_and_confidence(cc_template) -> None:
    """A parametric mismatch drops the parametric signal and overall confidence."""
    match, comp_map, net_map = _make_match(value="10K")
    scorer = TopologyScorer(comp_map, net_map)
    breakdown = scorer.score(match, cc_template)

    assert breakdown.parametric == pytest.approx(0.0)
    assert breakdown.fused == pytest.approx(0.75)
    assert breakdown.confidence == "medium"


def test_score_wrong_net_names_lower_naming(cc_template) -> None:
    """Net names that do not match the template patterns lower the naming score."""
    match, comp_map, net_map = _make_match(cc_name="FOO", gnd_name="BAR")
    scorer = TopologyScorer(comp_map, net_map)
    breakdown = scorer.score(match, cc_template)

    assert breakdown.naming == pytest.approx(0.0)
    assert breakdown.fused == pytest.approx(0.75)
    assert breakdown.confidence == "medium"


def test_score_empty_match_is_low_confidence(cc_template) -> None:
    """A match with no mapped components/signals has the lowest confidence band."""
    match = TopologyMatch(
        primitive_id="type-c-cc-pull-down",
        component_mapping={},
        signal_mapping={},
        evidence=[],
    )
    scorer = TopologyScorer({}, {})
    breakdown = scorer.score(match, cc_template)

    assert breakdown.fused == pytest.approx(0.0)
    assert breakdown.confidence == "low"


def test_score_evidence_includes_all_signals(cc_template) -> None:
    """The evidence list records every signal score and the final confidence."""
    match, comp_map, net_map = _make_match()
    scorer = TopologyScorer(comp_map, net_map)
    breakdown = scorer.score(match, cc_template)

    evidence = " ".join(breakdown.evidence)
    assert "structural=1.00" in evidence
    assert "naming=1.00" in evidence
    assert "parametric=1.00" in evidence
    assert "type=1.00" in evidence
    assert "confidence=high" in evidence
