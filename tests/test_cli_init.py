"""Tests for `cg init` — bootstrap another project with the bundled skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import _BUNDLED_SKILL_DIR, main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _bundled_skill_md() -> Path:
    """Path to the SKILL.md that ships inside the package."""
    return _BUNDLED_SKILL_DIR / "SKILL.md"


def test_bundled_skill_exists() -> None:
    """Sanity: the package must actually ship SKILL.md, otherwise all init tests
    are meaningless."""
    assert _BUNDLED_SKILL_DIR.is_dir(), f"missing bundled skill dir: {_BUNDLED_SKILL_DIR}"
    assert _bundled_skill_md().is_file(), f"missing SKILL.md: {_bundled_skill_md()}"


def test_init_installs_skill_into_target(runner: CliRunner, tmp_path: Path) -> None:
    """Default behaviour on a fresh target: copy SKILL.md into
    <target>/.claude/skills/circuit-graph-analysis/."""
    result = runner.invoke(main, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.stderr or result.stdout

    payload = json.loads(result.stdout)
    assert payload["status"] == "installed"
    assert payload["file_count"] >= 1

    installed = tmp_path / ".claude" / "skills" / "circuit-graph-analysis"
    assert installed.is_dir()
    assert (installed / "SKILL.md").is_file()
    # Byte-identical to source.
    assert (installed / "SKILL.md").read_bytes() == _bundled_skill_md().read_bytes()


def test_init_defaults_to_cwd_when_target_omitted(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting TARGET should default to the current working directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0, result.stderr or result.stdout
    assert (tmp_path / ".claude" / "skills" / "circuit-graph-analysis" / "SKILL.md").is_file()


def test_init_refuses_to_overwrite_without_force(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Re-running on an existing install should refuse (exit 0) without --force."""
    runner.invoke(main, ["init", str(tmp_path)])
    assert (tmp_path / ".claude" / "skills" / "circuit-graph-analysis" / "SKILL.md").is_file()

    result = runner.invoke(main, ["init", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "already_installed"
    assert "--force" in payload["hint"]


def test_init_force_overwrites_existing(runner: CliRunner, tmp_path: Path) -> None:
    """--force replaces an existing install and reports success."""
    runner.invoke(main, ["init", str(tmp_path)])

    # Tamper with the existing file to verify --force actually re-copies.
    target_md = tmp_path / ".claude" / "skills" / "circuit-graph-analysis" / "SKILL.md"
    target_md.write_text("tampered", encoding="utf-8")

    result = runner.invoke(main, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["status"] == "installed"
    assert target_md.read_text(encoding="utf-8") != "tampered"
    assert target_md.read_bytes() == _bundled_skill_md().read_bytes()


def test_init_dry_run_does_not_write(runner: CliRunner, tmp_path: Path) -> None:
    """--dry-run prints the plan and creates no files."""
    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.stderr or result.stdout

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["would_overwrite"] is False
    assert payload["file_count"] >= 1
    assert "SKILL.md" in payload["files"]

    # Nothing should have been created.
    assert not (tmp_path / ".claude").exists()


def test_init_dry_run_after_install_reports_would_overwrite(
    runner: CliRunner, tmp_path: Path
) -> None:
    """--dry-run on an existing install must NOT be short-circuited — it should
    report what would happen, with would_overwrite=true."""
    runner.invoke(main, ["init", str(tmp_path)])

    result = runner.invoke(main, ["init", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["would_overwrite"] is True


def test_init_target_must_be_directory(runner: CliRunner, tmp_path: Path) -> None:
    """TARGET must not be an existing file (click.Path file_okay=False)."""
    bad = tmp_path / "not_a_dir"
    bad.write_text("hello", encoding="utf-8")
    result = runner.invoke(main, ["init", str(bad)])
    assert result.exit_code != 0