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


def test_propose_config_picks_up_detected_schematics() -> None:
    ctx = json.dumps({
        "protel_nets": ["/abs/board1.NET", "/abs/board2.net"],
        "kicad_schematics": ["/abs/main.kicad_sch"],
        "kicad_projects": [],
    })
    result = json.loads(bootstrap.propose_config(ctx))
    assert "board1.NET" in result["schematics"]
    assert "board2.net" in result["schematics"]
    assert "main.kicad_sch" in result["schematics"]
    assert result["project_name"]  # non-empty


def test_propose_config_uses_defaults_when_empty() -> None:
    ctx = json.dumps({"protel_nets": [], "kicad_schematics": [], "kicad_projects": []})
    result = json.loads(bootstrap.propose_config(ctx))
    assert result["schematics"] == []
    assert result["default_graph_file"] == "output/circuit.graph.json"
    assert result["default_kb_path"] == "circuit_graph/knowledge/circuit_graph.db"


def test_propose_config_returns_json_string() -> None:
    ctx = json.dumps({"protel_nets": [], "kicad_schematics": [], "kicad_projects": []})
    out = bootstrap.propose_config(ctx)
    assert isinstance(out, str)
    json.loads(out)  # must parse without error


def test_write_config_creates_cg_toml(tmp_path: Path) -> None:
    cfg = json.dumps({"project_name": "demo", "default_graph_file": "output/x.json",
                      "default_graphml_file": "output/x.graphml",
                      "default_kb_path": "kb.db", "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path))
    assert result["written"] is True
    assert result["was_overwritten"] is False
    assert result["reason"] == "ok"
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "project_name" in out
    assert "demo" in out


def test_write_config_refuses_overwrite_without_force(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    cfg = json.dumps({"project_name": "new", "default_graph_file": "",
                      "default_graphml_file": "", "default_kb_path": "",
                      "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path))
    assert result["written"] is False
    assert result["was_overwritten"] is False
    assert "--force" in result["reason"]
    assert (tmp_path / ".cg.toml").read_text(encoding="utf-8") == 'project_name = "old"\n'


def test_write_config_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    cfg = json.dumps({"project_name": "new", "default_graph_file": "",
                      "default_graphml_file": "", "default_kb_path": "",
                      "schematics": [], "skills": []})
    result = json.loads(bootstrap.write_config(cfg, tmp_path, force=True))
    assert result["written"] is True
    assert result["was_overwritten"] is True
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "new" in out
    assert "old" not in out


def test_delegate_kb_init_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "{\"status\": \"ok\"}"
        stderr = ""
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "kb", "init"])
    result = json.loads(out)
    assert result["exit_code"] == 0
    assert result["status"] == "ok"
    assert "kb" in result["cmd"]


def test_delegate_kb_init_captures_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "no bundled skills found"
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "kb", "init"])
    result = json.loads(out)
    assert result["exit_code"] == 1
    assert result["status"] == "failed"
    assert "no bundled skills" in result["stderr"]


def test_delegate_initskill_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "{\"status\": \"ok\"}"
        stderr = ""
    def fake_run(cmd, **kwargs):
        return FakeCompleted()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    out = bootstrap.delegate(["cg", "initskill", "/tmp/target"])
    result = json.loads(out)
    assert result["exit_code"] == 0
    assert "initskill" in result["cmd"]