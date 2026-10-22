"""Repeated-structure (symmetry) grouping tests (todolist P1-7)."""

from pathlib import Path

import pytest

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.symmetry import detect_symmetry_groups, groups_touching

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"


@pytest.fixture(scope="module")
def t268_ir():
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")
    ir = ProtelNetParser().parse(ParserInput(file_path=str(fixture)))
    return run_enrichers(
        ir,
        [
            MultiUnitSplitter(),
            NetClassifier(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(),
        ],
    ).ir


def test_snubber_four_way_group(t268_ir) -> None:
    """SW1..SW4 snubber triplets form ONE 4-way symmetry group."""
    groups = {g.template_id: g for g in detect_symmetry_groups(t268_ir)}
    assert "rc-snubber" in groups
    group = groups["rc-snubber"]
    assert group.count == 4
    sw_nets = sorted(inst.signals["sw"] for inst in group.instances)
    assert sw_nets == ["SW1", "SW2", "SW3", "SW4"]


def test_group_key_is_substructure_not_whole_net(t268_ir) -> None:
    """SW1/SW3 and SW2/SW4 whole nets differ, yet all four land in one group."""
    groups = {g.template_id: g for g in detect_symmetry_groups(t268_ir)}
    group = groups["rc-snubber"]
    extras_sets = {frozenset(inst.extras) for inst in group.instances}
    # Channels genuinely differ (test point / extra NPO caps) — if grouping
    # keyed on the whole net, we'd get two 2-way groups instead of one 4-way.
    assert len(extras_sets) > 1
    assert group.count == 4


def test_per_channel_differences_listed(t268_ir) -> None:
    """Test points T5/T12 and extra capacitance appear as extras."""
    groups = {g.template_id: g for g in detect_symmetry_groups(t268_ir)}
    extras = [set(inst.extras) for inst in groups["rc-snubber"].instances]
    assert any("T5" in e for e in extras)
    assert any("T12" in e for e in extras)
    # SW2/SW4 carry the additional 10nF NPO + CBB decoupling caps.
    assert any(len(e) > 6 for e in extras)


def test_bootstrap_group(t268_ir) -> None:
    groups = {g.template_id: g for g in detect_symmetry_groups(t268_ir)}
    group = groups["bootstrap-cap"]
    assert group.count == 4
    sw_nets = sorted(inst.signals["sw"] for inst in group.instances)
    assert sw_nets == ["SW1", "SW2", "SW3", "SW4"]


def test_groups_touching_filters_by_ego(t268_ir) -> None:
    groups = detect_symmetry_groups(t268_ir)
    touching = groups_touching(groups, {"C9", "R26"})
    ids = {g.template_id for g in touching}
    assert "rc-snubber" in ids
    # An unrelated component touches neither group's instances.
    touching_none = groups_touching(groups, {"R70"})
    assert touching_none == []
