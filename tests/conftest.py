"""Shared pytest fixtures."""

from __future__ import annotations

import os
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


@pytest.fixture(autouse=True)
def _relax_autodraft_deadline_under_xdist(monkeypatch: pytest.MonkeyPatch) -> None:
    """xdist 多 worker 抢 CPU 时，draft 回放的 2 s 墙钟预算会偶发超时，
    导致 gate1 以 replay_timeout 拒写（写库测试在满载机器上必挂）。
    测试进程把预算放宽到稳过值；产品默认 2 s 不变。
    """
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    from circuit_graph import primitive_autodraft

    monkeypatch.setattr(primitive_autodraft, "AUTO_MATCH_TIMEOUT_SECONDS", 30.0)
