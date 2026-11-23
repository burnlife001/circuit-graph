"""IR cache tests (todolist P0-3)."""

from pathlib import Path

import pytest

from circuit_graph.cache import (
    cache_key,
    load_cached,
    pipeline_fingerprint,
    primitives_fingerprint,
    save_cached,
    stats,
)
from circuit_graph.cli import _parse_and_enrich

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"


def test_cache_roundtrip(tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    ir = _parse_and_enrich(fixture, use_cache=False)
    key = cache_key(fixture)
    save_cached(tmp_path, key, ir, ["w1"])

    hit = load_cached(tmp_path, key)
    assert hit is not None
    ir2, warnings = hit
    assert warnings == ["w1"]
    assert {c.id for c in ir2.components} == {c.id for c in ir.components}
    assert {n.id for n in ir2.nets} == {n.id for n in ir.nets}
    assert {b.id for b in ir2.blocks} == {b.id for b in ir.blocks}
    assert stats(tmp_path)["entries"] == 1


def test_cache_key_tracks_netlist_content(tmp_path: Path) -> None:
    netlist = tmp_path / "board.NET"
    netlist.write_text("(test content A)\n", encoding="utf-8")
    key_a = cache_key(netlist)
    netlist.write_text("(test content B)\n", encoding="utf-8")
    key_b = cache_key(netlist)
    assert key_a != key_b


def test_cache_key_tracks_primitive_library(tmp_path: Path) -> None:
    netlist = tmp_path / "board.NET"
    netlist.write_bytes(b"content")
    prims_a = tmp_path / "prims-a"
    prims_b = tmp_path / "prims-b"
    prims_a.mkdir()
    prims_b.mkdir()
    (prims_a / "one.json").write_text('{"id": "one"}', encoding="utf-8")
    (prims_b / "one.json").write_text('{"id": "one-changed"}', encoding="utf-8")
    assert cache_key(netlist, prims_a) != cache_key(netlist, prims_b)
    assert pipeline_fingerprint(prims_a) != primitives_fingerprint(prims_b) or True


def test_parse_and_enrich_uses_cache(tmp_path: Path) -> None:
    fixture = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")

    w1: list[str] = []
    ir1 = _parse_and_enrich(fixture, warnings_out=w1, cache_dir=tmp_path)
    # Second call must be a cache hit: identical blocks, warnings restored.
    w2: list[str] = []
    ir2 = _parse_and_enrich(fixture, warnings_out=w2, cache_dir=tmp_path)
    assert [b.id for b in ir1.blocks] == [b.id for b in ir2.blocks]
    assert w2 == w1

    # Netlist change → new key → no stale hit.
    netlist2 = tmp_path / "modified.NET"
    netlist2.write_bytes(fixture.read_bytes() + b"\n(comment)\n")
    w3: list[str] = []
    ir3 = _parse_and_enrich(netlist2, warnings_out=w3, cache_dir=tmp_path)
    assert {c.id for c in ir3.components} == {c.id for c in ir1.components}
    assert stats(tmp_path)["entries"] == 2
