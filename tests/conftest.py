"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_ir_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the IR parse/enrich cache at a per-test tmp directory.

    Without this, ``_parse_and_enrich`` would share ``<repo>/.cg-cache``
    across tests: a test that monkeypatches the knowledge base (or any other
    enrichment input) would silently receive a cached IR built under a
    different configuration.
    """
    import circuit_graph.cache as cache_mod

    monkeypatch.setattr(cache_mod, "_DEFAULT_CACHE_DIR", tmp_path / ".cg-cache")
