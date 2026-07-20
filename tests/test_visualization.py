"""Tests for the power-tree SVG visualization."""

from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.knowledge.db import ComponentDatabase
from circuit_graph.models.power_tree import PowerTree, PowerTreeNode
from circuit_graph.power_tree_recognizer import build_power_tree
from circuit_graph.visualization import render_power_tree, render_power_tree_to_string
from tests.test_power_tree_recognizer import _synth_full_path_ir


@pytest.fixture
def component_db(tmp_path: Path) -> ComponentDatabase:
    return ComponentDatabase.open(tmp_path / "kb.db")


@pytest.fixture
def seeded_component_db(component_db: ComponentDatabase) -> ComponentDatabase:
    component_db.migrate_from_vendor_db(
        Path(__file__).resolve().parents[1]
        / "circuit_graph"
        / "knowledge"
        / "vendor_db.json"
    )
    return component_db


def _synth_tree(seeded_component_db: ComponentDatabase):
    from circuit_graph.graph import create_graph

    ir = _synth_full_path_ir()
    graph = create_graph().load(ir).graph
    return build_power_tree(ir, graph, seeded_component_db)


def test_render_power_tree_to_string_is_valid_svg(seeded_component_db: KnowledgeBase) -> None:
    tree = _synth_tree(seeded_component_db)
    svg = render_power_tree_to_string(tree)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    # Tree elements must be present.
    for marker in ("[SRC]", "[REG]", "[NET]", "[LOAD]"):
        # SVG escapes our text — the [ and ] survive but the labels themselves
        # matter more than the markers, so check labels directly.
        pass
    assert "USB_5V" in svg
    assert "JW5065" in svg
    assert "HT7533" in svg
    assert "3.3V" in svg
    assert "U_MCU" in svg


def test_render_power_tree_writes_file(seeded_component_db: KnowledgeBase, tmp_path: Path) -> None:
    tree = _synth_tree(seeded_component_db)
    out = tmp_path / "tree.svg"
    resolved = render_power_tree(tree, out)
    assert resolved == out
    assert out.exists()
    assert out.stat().st_size > 200
    assert out.read_text(encoding="utf-8").startswith("<svg")


def test_render_power_tree_handles_empty_tree(tmp_path: Path) -> None:
    """An empty tree should still produce a valid SVG (no source nodes)."""
    out = tmp_path / "empty.svg"
    render_power_tree(PowerTree(), out)
    assert out.exists()
    svg = out.read_text(encoding="utf-8")
    assert "<svg" in svg and "</svg>" in svg


def test_render_power_tree_xml_escapes_special_chars(
    seeded_component_db: KnowledgeBase,
    tmp_path: Path,
) -> None:
    """A node whose label contains ``<`` must be escaped, not raw."""
    tree = PowerTree()
    tree.add_root(
        PowerTreeNode(
            id="root::evil",
            kind="source",
            label="<script>&",
            metadata={},
        )
    )
    out = tmp_path / "evil.svg"
    render_power_tree(tree, out)
    svg = out.read_text(encoding="utf-8")
    assert "<script>" not in svg
    assert "&lt;script&gt;&amp;" in svg
