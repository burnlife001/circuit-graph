"""Tests for `cg init` orchestrator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph import bootstrap


def test_detect_project_finds_protel_nets(tmp_path: Path) -> None:
    (tmp_path / "board1.NET").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    (tmp_path / "board2.net").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    nets = [Path(p).name for p in result["protel_nets"]]
    assert "board1.NET" in nets
    assert "board2.net" in nets
    assert result["target"] == str(tmp_path.resolve())
    assert result["existing_cg_toml"] is None
    assert result["existing_kb_db"] is None
    assert result["existing_skills_dir"] is None