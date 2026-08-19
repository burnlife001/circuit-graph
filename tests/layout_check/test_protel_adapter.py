"""Protel .NET -> LayoutSchematic adapter tests.

This adapter consumes ``circuit_graph.parsers.protel`` (read-only) and
produces a ``LayoutSchematic`` with all geometry fields ``None`` and
``wires=[]``. Spatial assertions on a .NET input must auto-skip; the
adapter itself does not flag this -- that's the orchestrator's job.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.layout_check.model import LayoutSchematic
from circuit_graph.layout_check.protel_adapter import parse_protel

_FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "protel"


@pytest.fixture(scope="module")
def fx_t268():
    path = _FIXTURES / "FX-T268-V6-10米-5065.NET"
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return parse_protel(path)


def test_returns_layout_schematic(fx_t268):
    assert isinstance(fx_t268, LayoutSchematic)


def test_fxt268_has_components(fx_t268):
    # Don't hardcode the exact count -- the fixture has ~80 components
    # (variants of R/C/U/D/Q/...), but we only sanity-check structure.
    assert len(fx_t268.components) >= 20
    assert all(c.id for c in fx_t268.components)


def test_fxt268_refdes_prefixes_are_populated(fx_t268):
    # Most components should have a non-empty refdes_prefix.
    empty = [c for c in fx_t268.components if not c.refdes_prefix]
    # Allow a small number (e.g. power symbols) but most are real parts.
    assert len(empty) < 5


def test_fxt268_geometry_is_none(fx_t268):
    # Protel has no geometry -- every spatial field must be None.
    for c in fx_t268.components:
        assert c.position_mm is None
        assert c.rotation_deg is None
        assert c.sheet is None
        assert c.page is None
        for p in c.pins:
            assert p.x_mm is None
            assert p.y_mm is None


def test_fxt268_no_wires(fx_t268):
    # Protel has no wire data.
    assert fx_t268.wires == []


def test_fxt268_net_kinds_are_valid(fx_t268):
    # All nets must have a kind in {"power", "ground", "signal"}.
    for n in fx_t268.nets:
        assert n.kind in {"power", "ground", "signal"}


def test_fxt268_gnd_net_detected_as_ground(fx_t268):
    # At least one net must be tagged "ground" (the typical GND net).
    gnd = [n for n in fx_t268.nets if n.kind == "ground"]
    assert gnd
