"""Tests for the layout-schematic format dispatcher."""
from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.layout_check.loader import LayoutCheckError
from circuit_graph.layout_check.model import LayoutSchematic
from circuit_graph.layout_check.parsers import parse_schematic

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LM321 = _REPO_ROOT / "lm321_ccs" / "1A_CCS_LM321_BJT.kicad_sch"
_FXT268 = _REPO_ROOT / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"


def test_dispatches_kicad_sch_to_layout_schematic_with_geometry():
    if not _LM321.exists():
        pytest.skip(f"missing {_LM321}")
    s = parse_schematic(_LM321)
    assert isinstance(s, LayoutSchematic)
    # KiCad has coordinates and wires
    assert any(c.position_mm is not None for c in s.components)
    assert s.wires


def test_dispatches_protel_net_to_layout_schematic_without_geometry():
    if not _FXT268.exists():
        pytest.skip(f"missing {_FXT268}")
    s = parse_schematic(_FXT268)
    assert isinstance(s, LayoutSchematic)
    # Protel has neither coordinates nor wires
    for c in s.components:
        assert c.position_mm is None
    assert s.wires == []


def test_dispatch_is_case_insensitive_on_extension(tmp_path):
    """``parse_schematic`` should accept ``.NET`` (uppercase) just like ``.net``.

    Windows filesystems are case-insensitive but Python ``Path.suffix`` is;
    we normalize to lowercase before dispatch. Verify by copying a Protel
    fixture to a path with mixed-case extension.
    """
    if not _FXT268.exists():
        pytest.skip("fxt268 fixture missing")
    # Copy fixture to a tmp path with mixed-case extension (".NeT").
    mixed = tmp_path / "MIXED.Net"
    mixed.write_bytes(_FXT268.read_bytes())
    s = parse_schematic(mixed)
    assert isinstance(s, LayoutSchematic)
    assert s.wires == []  # still Protel → no wires


def test_unknown_extension_raises_schematic_parse_error(tmp_path):
    p = tmp_path / "weird.sch"
    p.write_text("foo", encoding="utf-8")
    with pytest.raises(LayoutCheckError) as ei:
        parse_schematic(p)
    assert ei.value.kind == "schematic_parse_error"


def test_missing_kicad_file_raises_schematic_parse_error(tmp_path):
    p = tmp_path / "nope.kicad_sch"
    with pytest.raises(LayoutCheckError) as ei:
        parse_schematic(p)
    assert ei.value.kind == "schematic_parse_error"


def test_missing_protel_file_raises_schematic_parse_error(tmp_path):
    p = tmp_path / "nope.NET"
    with pytest.raises(LayoutCheckError) as ei:
        parse_schematic(p)
    assert ei.value.kind == "schematic_parse_error"
