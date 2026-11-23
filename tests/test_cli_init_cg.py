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
    (tmp_path / "circuit_graph" / "knowledge").mkdir(parents=True)
    (tmp_path / "circuit_graph" / "knowledge" / "circuit_graph.db").write_text("", encoding="utf-8")
    result = json.loads(bootstrap.detect_project(tmp_path))
    assert result["existing_cg_toml"] is not None
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


@pytest.fixture
def mock_delegate(monkeypatch: pytest.MonkeyPatch):
    """Replace bootstrap.delegate with a recorder. Returns a list that
    each fake invocation appends its cmd to, so tests can assert what
    was called."""
    calls: list[list[str]] = []
    def fake(cmd, cwd=None):
        calls.append(list(cmd))
        return json.dumps({
            "cmd": list(cmd),
            "exit_code": 0,
            "status": "ok",
            "stdout": "{\"status\":\"ok\"}",
            "stderr": "",
            "duration_s": 0.0,
        })
    monkeypatch.setattr(bootstrap, "delegate", fake)
    return calls


def test_init_writes_cg_toml_default_path(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    report = json.loads(out)
    assert report["status"] == "ok"
    assert report["config"]["written"] is True
    assert (tmp_path / ".cg.toml").is_file()


def test_init_runs_config_and_kb_by_default(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True)
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" in cmds_str


def test_init_skip_config_only_runs_kb(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True, skip_config=True)
    assert not (tmp_path / ".cg.toml").exists()
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" in cmds_str


def test_init_skip_kb_only_writes_config(tmp_path: Path, mock_delegate) -> None:
    bootstrap.init_orchestrator(tmp_path, yes=True, skip_kb=True)
    assert (tmp_path / ".cg.toml").is_file()
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" not in cmds_str


def test_init_force_overwrites_existing_config(tmp_path: Path, mock_delegate) -> None:
    (tmp_path / ".cg.toml").write_text('project_name = "old"\n', encoding="utf-8")
    bootstrap.init_orchestrator(tmp_path, yes=True, force=True)
    out = (tmp_path / ".cg.toml").read_text(encoding="utf-8")
    assert "old" not in out


def test_init_dry_run_prints_plan_no_writes(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True, dry_run=True)
    report = json.loads(out)
    assert report["status"] == "dry_run"
    assert report["dry_run"] is True
    assert not (tmp_path / ".cg.toml").exists()
    assert mock_delegate == []
    assert report["kb"]["status"] == "skipped"
    assert report["kb"]["reason"] == "dry-run"


def test_init_idempotent_second_run_keeps_config_and_kb(
    tmp_path: Path, mock_delegate
) -> None:
    """Config and KB stay put on a re-run."""
    bootstrap.init_orchestrator(tmp_path, yes=True)
    # Simulate the side-effects of the first run having succeeded
    # (mock_delegate does not actually write to disk), so the orchestrator
    # can detect existing artifacts on the second run.
    (tmp_path / "circuit_graph" / "knowledge").mkdir(parents=True)
    (tmp_path / "circuit_graph" / "knowledge" / "circuit_graph.db").write_text("", encoding="utf-8")
    mock_delegate.clear()
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    report = json.loads(out)
    assert report["status"] == "ok"
    assert report["config"]["written"] is False
    assert report["config"].get("skipped") is True
    assert report["kb"]["status"] == "skipped"
    cmds_str = " ".join(" ".join(c) for c in mock_delegate)
    assert "kb init" not in cmds_str


def test_init_yes_flag_skips_interactive_confirm(tmp_path: Path, monkeypatch, mock_delegate) -> None:
    import click
    confirm_calls: list[str] = []
    def fake_confirm(text, default=True):
        confirm_calls.append(text)
        return True
    monkeypatch.setattr(click, "confirm", fake_confirm)
    bootstrap.init_orchestrator(tmp_path, yes=True)
    # yes=True means we never call click.confirm
    assert confirm_calls == []


def test_init_returns_json_string_at_top_level(tmp_path: Path, mock_delegate) -> None:
    out = bootstrap.init_orchestrator(tmp_path, yes=True)
    assert isinstance(out, str)
    parsed = json.loads(out)
    assert "status" in parsed
    assert "target" in parsed
    assert "config" in parsed
    assert "kb" in parsed
    assert "claude" not in parsed


from click.testing import CliRunner  # noqa: E402

from circuit_graph.cli import main  # noqa: E402


def test_cli_init_listed_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.stdout


def test_cli_init_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0
    assert "Bootstrap" in result.stdout
    assert "--skip-config" in result.stdout
    assert "--skip-kb" in result.stdout
    assert "--skip-skill" not in result.stdout


def test_cli_init_end_to_end_dry_run(tmp_path: Path) -> None:
    (tmp_path / "board.NET").write_text("(NET1 R1-1 R2-1)", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run", "--yes"])
    assert result.exit_code == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "dry_run"
    assert report["config"]["written"] is False
    assert report["kb"]["status"] == "skipped"
    assert report["kb"]["reason"] == "dry-run"
    assert not (tmp_path / ".cg.toml").exists()