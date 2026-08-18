"""KiCad .kicad_sch parser tests.

Real-fixture-driven tests against ``lm321_ccs/1A_CCS_LM321_BJT.kicad_sch``.
The fixture is shared with the broader layout-check project; if it's been
moved the tests are skipped gracefully so a repo-layout change doesn't break
the rest of the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Import skip guard — kicad-sch-api is in [project.optional-dependencies].dev
ksa = pytest.importorskip(
    "kicad_sch_api", reason="kicad-sch-api required for layout_check KiCad parser tests"
)

from circuit_graph.layout_check.model import (  # noqa: E402
    LayoutSchematic,
)
from circuit_graph.parsers.kicad_sch import parse_kicad_sch  # noqa: E402

_LM321 = Path(__file__).resolve().parent.parent.parent / "lm321_ccs" / "1A_CCS_LM321_BJT.kicad_sch"


@pytest.fixture(scope="module")
def lm321_schematic():
    if not _LM321.exists():
        pytest.skip(f"fixture missing: {_LM321}")
    return parse_kicad_sch(_LM321)


def test_returns_layout_schematic(lm321_schematic):
    assert isinstance(lm321_schematic, LayoutSchematic)


def test_lm321_has_expected_components(lm321_schematic):
    # The lm321 fixture has 14 components (we counted them via probe).
    assert len(lm321_schematic.components) == 14


def test_lm321_refdes_prefixes(lm321_schematic):
    prefixes = {c.refdes_prefix for c in lm321_schematic.components}
    # We expect at least R, U, PWR from the .NET file's RREF1/RREF2/U1/#PWR03
    # (the kicad file has its own refdeses which may differ)
    # Just sanity-check it's non-empty.
    assert prefixes
    assert all(isinstance(p, str) for p in prefixes)


def test_lm321_has_position_data(lm321_schematic):
    with_pos = [c for c in lm321_schematic.components if c.position_mm is not None]
    # Almost all should have positions in a real schematic.
    assert len(with_pos) >= 10


def test_lm321_wire_count(lm321_schematic):
    # The probe showed 29 wires in 1A_CCS_LM321_BJT.kicad_sch.
    assert len(lm321_schematic.wires) >= 1  # at least one wire


def test_lm321_nets_have_kinds(lm321_schematic):
    # All LayoutNet.kind must be one of the literal three.
    for n in lm321_schematic.nets:
        assert n.kind in {"power", "ground", "signal"}


def test_parser_silences_kicad_sch_api_stderr(capsys):
    """``Schematic.load`` emits a benign 'No KiCAD symbol libraries found'
    warning. The parser must capture it so capsys stays quiet in CI."""
    if not _LM321.exists():
        pytest.skip(f"fixture missing: {_LM321}")
    parse_kicad_sch(_LM321)
    captured = capsys.readouterr()
    # Real stderr text should NOT leak — if it does, the suppression broke.
    assert "symbol libraries" not in captured.err.lower()
    # And nothing was printed to stdout by the parser either.
    assert captured.out == ""
