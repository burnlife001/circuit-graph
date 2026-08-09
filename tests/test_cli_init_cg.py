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


def test_detect_project_finds_kicad_schematics(tmp_path: Path) -> None:
    (tmp_path / "main.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "aux.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "project.kicad_pro").write_text("(kicad_pro)", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    sch_names = [Path(p).name for p in result["kicad_schematics"]]
    assert "main.kicad_sch" in sch_names
    assert "aux.kicad_sch" in sch_names
    assert any(p.endswith("project.kicad_pro") for p in result["kicad_projects"])


def test_detect_project_finds_existing_cg_toml(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text("project_name = 'x'", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "skills").mkdir()
    (tmp_path / "circuit_graph" / "knowledge").mkdir(parents=True)
    (tmp_path / "circuit_graph" / "knowledge" / "circuit_graph.db").write_text("", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    assert result["existing_cg_toml"] is not None
    assert result["existing_skills_dir"] is not None
    assert result["existing_kb_db"] is not None


def test_detect_project_missing_target_returns_error(tmp_path: Path) -> None:
    ghost = tmp_path / "does-not-exist"
    result = json.loads(bootstrap.detect_project(ghost))
    assert result["status"] == "failed"
    assert "does not exist" in result["error"]


def test_detect_project_target_is_file(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir"
    f.write_text("x", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(f))
    assert result["status"] == "failed"
    assert "not a directory" in result["error"]