"""Tests for the VF2 topology subgraph matcher."""

from __future__ import annotations

import pytest

from circuit_graph.ir import DeviceKind, NetType
from circuit_graph.primitives import load_primitives
from circuit_graph.vf2_matcher import Vf2TopologyMatcher
from tests.fir_helpers import build_ir, make_component, make_net


def _cc_pull_down_ir(
    *,
    value: str = "5.1K",
    cc_name: str = "CC1",
) -> object:
    """Build a minimal IR containing a single candidate CC pull-down resistor."""
    r = make_component(
        "R1",
        DeviceKind.RESISTOR,
        [("1", cc_name), ("2", "GND")],
        raw_value=value,
    )
    cc = make_net("N_CC", cc_name, NetType.SIGNAL)
    gnd = make_net("N_GND", "GND", NetType.GND)
    return build_ir(
        [r],
        [cc, gnd],
        [("R1.1", "N_CC"), ("R1.2", "N_GND")],
    )


@pytest.fixture
def cc_template():
    return next(t for t in load_primitives() if t.id == "type-c-cc-pull-down")


def test_find_matches_detects_cc_pull_down(cc_template) -> None:
    """The matcher finds the single Type-C CC pull-down resistor."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir())
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.component_mapping == {"R_pull": "R1"}
    assert match.signal_mapping["cc"] == "N_CC"
    assert match.signal_mapping["gnd"] == "N_GND"
    assert any("R1" in e for e in match.evidence)


def test_find_matches_no_match_for_wrong_net_name(cc_template) -> None:
    """A resistor on nets that do not match the primitive patterns is skipped."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir(cc_name="FOO"))
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert not result.matches


def test_find_matches_still_structural_match_for_wrong_value(cc_template) -> None:
    """VF2 matches structure only; value filtering happens in scoring."""
    matcher = Vf2TopologyMatcher(_cc_pull_down_ir(value="10K"))
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1


def test_find_matches_honours_timeout_on_large_graph(cc_template) -> None:
    """A near-zero timeout aborts before the full exponential search completes."""
    # One real candidate so the matcher enters the isomorphism iterator; the
    # rest of the graph provides enough candidate roots to make early abort
    # meaningful.
    cc_resistor = make_component(
        "RCC",
        DeviceKind.RESISTOR,
        [("1", "CC1"), ("2", "GND")],
        raw_value="5.1K",
    )
    components = [cc_resistor] + [
        make_component(
            f"R{i}",
            DeviceKind.RESISTOR,
            [("1", f"NET{i}"), ("2", "GND")],
            raw_value="5.1K",
        )
        for i in range(2499)
    ]
    nets = [
        make_net("N_CC", "CC1", NetType.SIGNAL),
        make_net("N_GND", "GND", NetType.GND),
    ] + [make_net(f"N{i}", f"NET{i}", NetType.SIGNAL) for i in range(2499)]
    wiring = [("RCC.1", "N_CC"), ("RCC.2", "N_GND")]
    wiring += [(f"R{i}.1", f"N{i}") for i in range(2499)]
    wiring += [(f"R{i}.2", "N_GND") for i in range(2499)]
    ir = build_ir(components, nets, wiring)

    matcher = Vf2TopologyMatcher(ir)
    result = matcher.find_matches(cc_template, timeout_seconds=0.0)

    assert result.aborted
    assert result.duration_seconds < 2.0


def test_find_matches_large_graph_within_default_timeout(cc_template) -> None:
    """A 5000-node graph finishes within the default timeout without aborting."""
    components = [
        make_component(
            f"R{i}",
            DeviceKind.RESISTOR,
            [("1", f"NET{i}"), ("2", "GND")],
            raw_value="5.1K",
        )
        for i in range(2500)
    ]
    nets = [make_net(f"N{i}", f"NET{i}", NetType.SIGNAL) for i in range(2500)] + [
        make_net("N_GND", "GND", NetType.GND)
    ]
    wiring = [(f"R{i}.1", f"N{i}") for i in range(2500)] + [
        (f"R{i}.2", "N_GND") for i in range(2500)
    ]
    ir = build_ir(components, nets, wiring)

    matcher = Vf2TopologyMatcher(ir)
    result = matcher.find_matches(cc_template, timeout_seconds=2.0)

    assert not result.aborted
    assert result.duration_seconds < 2.0
    # No false positives: the synthetic nets do not match CC/GND patterns.
    assert not result.matches


# ---------------------------------------------------------------------------
# Strict-mode tests (expected_role / edge_semantic)
# ---------------------------------------------------------------------------


def _make_pull_up_template():
    """Inline primitive used to drive strict-mode tests (not a real library entry)."""
    from circuit_graph.primitives import (
        PrimitiveComponent,
        PrimitiveConstraints,
        PrimitivePin,
        PrimitiveScoring,
        PrimitiveSignal,
        PrimitiveTemplate,
    )

    return PrimitiveTemplate(
        id="test-pull-up",
        name="Test Pull-Up",
        description="",
        role="unknown",
        version="0.1.0",
        signals=[
            PrimitiveSignal(
                id="vcc", type="net", name_pattern="^VCC$",
                expected_role="power", expected_direction="bidirectional",
            ),
            PrimitiveSignal(
                id="gpio", type="net", name_pattern="^GPIO",
                expected_role="digital",
            ),
        ],
        components=[
            PrimitiveComponent(
                id="R_pull", type="Resistor",
                pins=[
                    PrimitivePin(number="1", net="vcc"),
                    PrimitivePin(number="2", net="gpio"),
                ],
            ),
        ],
        constraints=PrimitiveConstraints(),
        scoring=PrimitiveScoring(),
        raw={},
    )


def _pull_up_ir(*, with_semantics: bool = True, vcc_role: str = "power"):
    """Build a 1-resistor IR; resistor between VCC and GPIO nets."""
    r = make_component(
        "R1", DeviceKind.RESISTOR,
        [("1", "VCC"), ("2", "GPIO1")],
        raw_value="10K",
    )
    vcc = make_net("N_VCC", "VCC", NetType.POWER)
    gpio = make_net("N_GPIO", "GPIO1", NetType.SIGNAL)
    edge_semantics = {}
    if with_semantics:
        edge_semantics = {
            ("R1.1", "N_VCC"):  {"role": vcc_role, "direction": "bidirectional", "level": "5V"},
            ("R1.2", "N_GPIO"): {"role": "digital", "direction": "bidirectional", "level": None},
        }
    return build_ir([r], [vcc, gpio],
                    [("R1.1", "N_VCC"), ("R1.2", "N_GPIO")],
                    edge_semantics=edge_semantics)


def test_strict_mode_role_match_passes():
    """When edge_semantic matches expected_role, the primitive matches."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="power"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert len(result.matches) == 1
    assert result.matches[0].signal_mapping["vcc"] == "N_VCC"
    assert result.matches[0].signal_mapping["gpio"] == "N_GPIO"


def test_strict_mode_role_mismatch_rejected():
    """When edge_semantic.role disagrees with expected_role, no match."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=True, vcc_role="digital"))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []


def test_strict_mode_missing_edge_semantic_rejected():
    """Strict mode: edges without edge_semantic metadata are rejected."""
    matcher = Vf2TopologyMatcher(_pull_up_ir(with_semantics=False))
    result = matcher.find_matches(_make_pull_up_template(), timeout_seconds=2.0)

    assert not result.aborted
    assert result.matches == []
